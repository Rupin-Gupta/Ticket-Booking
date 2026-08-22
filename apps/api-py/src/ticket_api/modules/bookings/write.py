"""
Booking creation, extracted so the checkout path and the waitlist-offer path
share one implementation.

It lives here rather than in `service.py` to keep the import graph acyclic:
bookings/service imports waitlist/service for advance_waitlist(), so
waitlist/service must not import bookings/service back. Both import this.

Rule 2 in spirit — there is one "turn seats into a booking", never two that can
drift apart.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...lib.qr import booking_reference, random_token
from ...models import (
    Booking,
    BookingSeat,
    Event,
    Seat,
    SeatCategory,
    SeatStatus,
    Show,
    ShowSeat,
    Venue,
    iso,
    money,
)
from .schemas import BookingSeatOut, BookingShow, BookingView


async def write_booking(
    session: AsyncSession,
    *,
    show_id: str,
    customer_id: str,
    seats: list[tuple[str, str]],
) -> str:
    """
    Writes the booking and flips its seats to BOOKED.

    The caller must already hold row locks on those seats and have verified they
    are claimable — this function does no checking of its own, on purpose,
    because the two callers verify different things (a live hold vs. a valid
    offer).

    `seats` is a list of (show_seat_id, category_id). Returns the booking id;
    the caller re-reads it through `booking_view` after commit.
    """
    category_ids = {category_id for _, category_id in seats}

    # Price is read now and frozen onto each row. An organiser re-pricing a
    # category next week must not rewrite what this booking was worth.
    rows = (
        await session.execute(
            select(SeatCategory.id, SeatCategory.price).where(SeatCategory.id.in_(category_ids))
        )
    ).all()
    price_of = {cid: price for cid, price in rows}

    booking = Booking(
        reference=booking_reference(),
        qr_token=random_token(),
        customer_id=customer_id,
        show_id=show_id,
    )
    session.add(booking)
    await session.flush()

    session.add_all(
        [
            BookingSeat(
                booking_id=booking.id,
                show_seat_id=show_seat_id,
                price_at_booking=price_of[category_id],
            )
            for show_seat_id, category_id in seats
        ]
    )

    await session.execute(
        update(ShowSeat)
        .where(ShowSeat.id.in_([s for s, _ in seats]))
        .values(
            status=SeatStatus.BOOKED,
            held_by_user_id=None,
            hold_expires_at=None,
            offer_expires_at=None,
        )
    )
    await session.flush()
    return booking.id


async def booking_view(
    session: AsyncSession, booking_id: str, *, include_qr: bool = False
) -> BookingView:
    """Reads a booking back into the shape the API returns."""
    booking = (
        (await session.execute(select(Booking).where(Booking.id == booking_id))).scalars().one()
    )
    show = (await session.execute(select(Show).where(Show.id == booking.show_id))).scalars().one()
    event = (await session.execute(select(Event).where(Event.id == show.event_id))).scalars().one()
    venue = (await session.execute(select(Venue).where(Venue.id == event.venue_id))).scalars().one()

    seat_rows = (
        await session.execute(
            select(BookingSeat, ShowSeat, Seat)
            .join(ShowSeat, ShowSeat.id == BookingSeat.show_seat_id)
            .join(Seat, Seat.id == ShowSeat.seat_id)
            .where(BookingSeat.booking_id == booking_id)
            .order_by(Seat.row.asc(), Seat.number.asc())
        )
    ).all()

    total = sum((bs.price_at_booking for bs, _, _ in seat_rows), Decimal(0))

    return BookingView(
        id=booking.id,
        reference=booking.reference,
        status=booking.status,
        createdAt=iso(booking.created_at) or "",
        cancelledAt=iso(booking.cancelled_at),
        show=BookingShow(
            id=show.id,
            startsAt=iso(show.starts_at) or "",
            eventId=event.id,
            title=event.title,
            type=event.type,
            venue=venue.name,
            address=venue.address,
        ),
        seats=[
            BookingSeatOut(
                showSeatId=show_seat.id,
                label=f"{seat.row}{seat.number}",
                section=seat.section,
                price=money(booking_seat.price_at_booking),
            )
            for booking_seat, show_seat, seat in seat_rows
        ],
        total=money(total),
        qrToken=booking.qr_token if include_qr else None,
    )
