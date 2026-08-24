"""
The email queue.

BullMQ has no Python equivalent, so this is ARQ: same shape (Redis-backed,
retries with backoff, a separate worker process), a different library.

Nothing in a request path awaits a send. A booking is confirmed in Postgres
before anything is queued, and a mail provider must never be able to fail a
booking the customer has already made.
"""

from __future__ import annotations

import base64
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal, TypedDict

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select

from ..config import IS_TEST, settings
from ..db import Session
from ..lib.emails import (
    booking_cancelled_email,
    booking_confirmed_email,
    show_cancelled_email,
    waitlist_offer_email,
)
from ..lib.mailer import Mail, send_mail
from ..lib.qr import offer_url, render_qr_data_url, verify_url
from ..models import (
    Booking,
    BookingSeat,
    BookingStatus,
    Event,
    Seat,
    SeatCategory,
    Show,
    ShowSeat,
    User,
    Venue,
    WaitlistStatus,
    utcnow,
)

EmailKind = Literal["booking-confirmed", "booking-cancelled", "show-cancelled", "waitlist-offer"]


class EmailJob(TypedDict, total=False):
    kind: EmailKind
    bookingId: str
    entryId: str


def _redis_settings() -> RedisSettings | None:
    if not settings.REDIS_URL:
        return None
    return RedisSettings.from_dsn(settings.REDIS_URL)


_pool: Any = None


async def enqueue_email(job: EmailJob) -> None:
    """
    Hands an email to the queue and returns immediately.

    Never awaited by a request handler in a way that can fail it. If the queue
    is unreachable the booking still stands and the failure is logged loudly
    rather than turned into a 500 for a customer whose seat is already
    confirmed in the database.
    """
    global _pool
    subject = job.get("entryId") or job.get("bookingId")

    # Tests never reach the real queue. REDIS_URL points at the live Upstash
    # instance, and a suite that enqueues thousands of jobs there is the same
    # mistake as one that writes to the production database — plus it made the
    # booking tests fourteen times slower, one network round trip at a time.
    if IS_TEST:
        return

    redis_settings = _redis_settings()
    if redis_settings is None:
        print(f"[email] REDIS_URL not set — {job['kind']} for {subject} was not queued")
        return

    try:
        if _pool is None:
            _pool = await create_pool(redis_settings)
        await _pool.enqueue_job("send_email", job)
    except Exception as err:  # noqa: BLE001 - a queue failure must never fail a booking
        print(f"[email] could not queue {job['kind']} for {subject}: {err}")


def _utc_string(value: Any) -> str:
    """Matches JavaScript's toUTCString(), which is what the old emails showed."""
    return value.strftime("%a, %d %b %Y %H:%M:%S GMT")


async def send_email(_ctx: dict[str, Any], job: EmailJob) -> None:
    """
    Renders and sends.

    Reads the booking fresh rather than trusting a payload serialised minutes
    ago — by the time a retry runs the booking may have been cancelled, and
    sending a confirmation for a cancelled booking is worse than sending
    nothing.
    """
    if job["kind"] == "waitlist-offer":
        await _process_offer(job["entryId"])
        return

    booking_id = job["bookingId"]
    async with Session() as session:
        booking = (
            (await session.execute(select(Booking).where(Booking.id == booking_id)))
            .scalars()
            .first()
        )
        if booking is None:
            print(f"[email] booking {booking_id} no longer exists, skipping")
            return

        customer = (
            (await session.execute(select(User).where(User.id == booking.customer_id)))
            .scalars()
            .one()
        )
        show = (
            (await session.execute(select(Show).where(Show.id == booking.show_id))).scalars().one()
        )
        event = (
            (await session.execute(select(Event).where(Event.id == show.event_id))).scalars().one()
        )
        venue = (
            (await session.execute(select(Venue).where(Venue.id == event.venue_id))).scalars().one()
        )

        rows = (
            await session.execute(
                select(BookingSeat, Seat)
                .join(ShowSeat, ShowSeat.id == BookingSeat.show_seat_id)
                .join(Seat, Seat.id == ShowSeat.seat_id)
                .where(BookingSeat.booking_id == booking_id)
                .order_by(Seat.row.asc(), Seat.number.asc())
            )
        ).all()

    seats = [f"{seat.row}{seat.number}" for _, seat in rows]

    if job["kind"] == "show-cancelled":
        await send_mail(
            Mail(
                to=customer.email,
                subject=f"Cancelled — {event.title}",
                html=show_cancelled_email(
                    reference=booking.reference,
                    event_title=event.title,
                    venue=venue.name,
                    starts_at=_utc_string(show.starts_at),
                    seats=seats,
                ),
            )
        )
        return

    if job["kind"] == "booking-cancelled":
        await send_mail(
            Mail(
                to=customer.email,
                subject=f"Cancelled — {booking.reference}",
                html=booking_cancelled_email(
                    reference=booking.reference, event_title=event.title, seats=seats
                ),
            )
        )
        return

    if booking.status != BookingStatus.CONFIRMED:
        print(f"[email] booking {booking.reference} is {booking.status}, not confirming")
        return

    # Decimal arithmetic, never float. A float cannot hold 0.10, and money that
    # is off by a cent in an email is money the customer will ask about.
    total = sum((bs.price_at_booking for bs, _ in rows), Decimal(0))
    qr_data_url = render_qr_data_url(booking.qr_token)

    await send_mail(
        Mail(
            to=customer.email,
            subject=f"Your tickets — {event.title} ({booking.reference})",
            html=booking_confirmed_email(
                reference=booking.reference,
                event_title=event.title,
                venue=venue.name,
                starts_at=_utc_string(show.starts_at),
                seats=seats,
                total=f"{total:.2f}",
                qr_data_url=qr_data_url,
                verify_link=verify_url(booking.qr_token),
            ),
            attachments=[
                {
                    "filename": f"ticket-{booking.reference}.png",
                    "content": list(base64.b64decode(qr_data_url.split(",", 1)[1])),
                }
            ],
        )
    )
    print(f"[email] sent {job['kind']} for {booking.reference} to {customer.email}")


async def _process_offer(entry_id: str) -> None:
    """
    Re-reads the entry rather than trusting the payload.

    By the time a retry runs, the offer may have expired and moved on to
    somebody else — emailing a link that is already dead is worse than emailing
    nothing.
    """
    from ..models import WaitlistEntry

    async with Session() as session:
        entry = (
            (await session.execute(select(WaitlistEntry).where(WaitlistEntry.id == entry_id)))
            .scalars()
            .first()
        )

        if (
            entry is None
            or entry.status != WaitlistStatus.OFFERED
            or not entry.offer_token
            or entry.offer_expires_at is None
        ):
            print(f"[email] waitlist offer {entry_id} is no longer open, skipping")
            return

        customer = (
            (await session.execute(select(User).where(User.id == entry.customer_id)))
            .scalars()
            .one()
        )
        category = (
            (
                await session.execute(
                    select(SeatCategory).where(SeatCategory.id == entry.category_id)
                )
            )
            .scalars()
            .one()
        )
        show = (await session.execute(select(Show).where(Show.id == entry.show_id))).scalars().one()
        event = (
            (await session.execute(select(Event).where(Event.id == show.event_id))).scalars().one()
        )
        venue = (
            (await session.execute(select(Venue).where(Venue.id == event.venue_id))).scalars().one()
        )

        remaining: timedelta = entry.offer_expires_at - utcnow()
        minutes = max(1, round(remaining.total_seconds() / 60))
        token = entry.offer_token

    await send_mail(
        Mail(
            to=customer.email,
            subject=f"A seat opened up — {event.title}",
            html=waitlist_offer_email(
                event_title=event.title,
                venue=venue.name,
                starts_at=_utc_string(show.starts_at),
                category=category.name,
                price=f"{category.price:.2f}",
                minutes=minutes,
                claim_url=offer_url(token),
            ),
        )
    )
    print(f"[email] sent waitlist-offer {entry_id} to {customer.email}")


class WorkerSettings:
    """
    Run with: arq ticket_api.jobs.email_queue.WorkerSettings

    Five tries over roughly a minute and a half. A provider blip should not cost
    somebody their ticket, and a permanent failure should stop quickly.
    """

    functions = [send_email]
    max_tries = 5
    job_timeout = 60
    redis_settings = _redis_settings() or RedisSettings()
