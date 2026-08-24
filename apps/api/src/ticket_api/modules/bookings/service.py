from __future__ import annotations

from sqlalchemy import Text, bindparam, select, text, update
from sqlalchemy.dialects.postgresql import ARRAY

from ...db import Session, transaction
from ...errors import ApiError
from ...jobs.email_queue import enqueue_email
from ...models import (
    Booking,
    BookingSeat,
    BookingStatus,
    Event,
    Role,
    Seat,
    SeatEventKind,
    SeatStatus,
    Show,
    ShowSeat,
    ShowStatus,
    Venue,
    iso,
    utcnow,
)
from ...realtime.emit import broadcast_seats, broadcast_status
from ...security import TokenPayload
from ..seats.pairing import expand_pairs
from ..signals import service as signals
from ..waitlist.service import PendingOffer, advance_waitlist
from .schemas import BookingView, CancelResult, TicketView
from .write import booking_view, write_booking

# Same shape as the hold path's lock-and-read, plus the two columns needed to
# prove the hold belongs to this caller.
_LOCK_AND_READ = text(
    """
    SELECT ss.id,
           ss.status::text     AS status,
           ss."heldByUserId",
           ss."holdExpiresAt",
           ss."categoryId",
           s.row               AS "seatRow",
           s.number            AS "seatNumber",
           s.id                AS "physicalSeatId"
    FROM "ShowSeat" ss
    JOIN "Seat" s ON s.id = ss."seatId"
    WHERE ss.id = ANY(:seat_ids)
      AND ss."showId" = :show_id
    ORDER BY ss.id
    FOR UPDATE OF ss
    """
).bindparams(bindparam("seat_ids", type_=ARRAY(Text)))


async def create_booking(show_id: str, seat_ids: list[str], caller: TokenPayload) -> BookingView:
    """
    Turns held seats into a confirmed booking.

    Same locking discipline as the hold path, for the same reason: lock,
    re-read under the lock, verify, write. The extra condition here is
    ownership — the seats must be held *by this caller* and still unexpired.
    Without that check anyone could book seats somebody else is in the middle
    of paying for.
    """
    # Same expansion as the hold path: a pair is booked together or not at all.
    # Without it somebody could hold a pair and then book only the space.
    async with Session() as reader:
        seat_ids = await expand_pairs(reader, show_id, seat_ids)

    async with transaction() as session:
        # A cancelled show keeps its seat rows, and cancelling resets them to
        # AVAILABLE — so without this the seat map would happily sell a ticket
        # to a performance that is not happening.
        status = await session.scalar(select(Show.status).where(Show.id == show_id))
        if status is ShowStatus.CANCELLED:
            raise ApiError.conflict("SHOW_CANCELLED", "This show has been cancelled.")

        rows = (
            (await session.execute(_LOCK_AND_READ, {"seat_ids": seat_ids, "show_id": show_id}))
            .mappings()
            .all()
        )

        if len(rows) != len(seat_ids):
            raise ApiError.not_found(
                "SEAT_NOT_FOUND", "One or more of those seats are not in this show."
            )

        from ...models import utcnow

        now = utcnow()
        not_mine = [
            r
            for r in rows
            if r["status"] != "HELD"
            or r["heldByUserId"] != caller["sub"]
            or r["holdExpiresAt"] is None
            or r["holdExpiresAt"] <= now
        ]
        if not_mine:
            names = ", ".join(f"{r['seatRow']}{r['seatNumber']}" for r in not_mine)
            raise ApiError.conflict(
                "HOLD_NOT_VALID",
                f"Your hold on {names} has expired or was never yours. Pick the seats again.",
            )

        booking_id = await write_booking(
            session,
            show_id=show_id,
            customer_id=caller["sub"],
            seats=[(r["id"], r["categoryId"]) for r in rows],
        )
        view = await booking_view(session, booking_id)

    # Queued AFTER the transaction commits, and deliberately not awaited into
    # the response's success. The seat is confirmed in Postgres; the email is
    # allowed to be a second late, and a mail provider must never be able to
    # fail a booking the customer has already made.
    await enqueue_email({"kind": "booking-confirmed", "bookingId": booking_id})
    broadcast_status(show_id, seat_ids, SeatStatus.BOOKED.value)
    # The conversion, and the denominator every hesitation ratio is measured
    # against. After commit like everything else here.
    await signals.record([(r["physicalSeatId"], show_id, SeatEventKind.BOOKED) for r in rows])

    return view


# ------------------------------------------------------------------ reading


async def list_my_bookings(caller: TokenPayload) -> list[BookingView]:
    async with Session() as session:
        ids = (
            (
                await session.execute(
                    select(Booking.id)
                    .where(Booking.customer_id == caller["sub"])
                    .order_by(Booking.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [await booking_view(session, booking_id) for booking_id in ids]


async def get_booking(booking_id: str, caller: TokenPayload) -> BookingView:
    async with Session() as session:
        booking = (
            (await session.execute(select(Booking).where(Booking.id == booking_id)))
            .scalars()
            .first()
        )
        if booking is None:
            raise ApiError.not_found("BOOKING_NOT_FOUND", "No booking with that reference.")

        # Owner-checked, not merely authenticated. Booking ids are uuids, but
        # "hard to guess" is not an access control.
        if caller["role"] != Role.ADMIN and booking.customer_id != caller["sub"]:
            raise ApiError.forbidden("That booking belongs to someone else.")

        return await booking_view(
            session, booking_id, include_qr=booking.status == BookingStatus.CONFIRMED
        )


# --------------------------------------------------------------- cancelling


async def cancel_booking(booking_id: str, caller: TokenPayload) -> CancelResult:
    from ...models import utcnow

    async with transaction() as session:
        booking = (
            (await session.execute(select(Booking).where(Booking.id == booking_id)))
            .scalars()
            .first()
        )
        if booking is None:
            raise ApiError.not_found("BOOKING_NOT_FOUND", "No booking with that reference.")
        if caller["role"] != Role.ADMIN and booking.customer_id != caller["sub"]:
            raise ApiError.forbidden("That booking belongs to someone else.")
        if booking.status == BookingStatus.CANCELLED:
            raise ApiError.conflict("ALREADY_CANCELLED", "That booking is already cancelled.")

        show = (
            (await session.execute(select(Show).where(Show.id == booking.show_id))).scalars().one()
        )
        # Releasing a seat after the doors open helps nobody and would put a
        # seat back on sale for a show already under way.
        if show.starts_at <= utcnow():
            raise ApiError.conflict("SHOW_ALREADY_STARTED", "This show has already started.")

        now = utcnow()
        await session.execute(
            update(Booking)
            .where(Booking.id == booking_id)
            .values(status=BookingStatus.CANCELLED, cancelled_at=now)
        )

        # Release the claim without deleting the row: the price paid is revenue
        # history and the cancellation email still needs the seat labels. The
        # partial unique index only counts rows where releasedAt IS NULL, so
        # clearing it here is what lets the seat be sold again.
        await session.execute(
            update(BookingSeat).where(BookingSeat.booking_id == booking_id).values(released_at=now)
        )

        show_seat_ids = list(
            (
                await session.execute(
                    select(BookingSeat.show_seat_id).where(BookingSeat.booking_id == booking_id)
                )
            )
            .scalars()
            .all()
        )

        # Each freed seat goes to the next person in line, not straight back on
        # sale. Same function the offer sweeper calls — rule 3.
        offers: list[PendingOffer] = []
        for show_seat_id in show_seat_ids:
            pending = await advance_waitlist(session, show_seat_id)
            if pending is not None:
                offers.append(pending)

        show_id = booking.show_id

    await enqueue_email({"kind": "booking-cancelled", "bookingId": booking_id})
    # Offer emails go out after the transaction commits. Sending inside it would
    # tell somebody about a seat a rollback then takes back.
    for offer in offers:
        await enqueue_email({"kind": "waitlist-offer", "entryId": offer.entry_id})

    # Seats that went to the waitlist are OFFERED, not AVAILABLE — everyone
    # else's map has to show them as unavailable, not as suddenly buyable.
    offered = {o.show_seat_id for o in offers}
    broadcast_seats(
        show_id,
        [
            {
                "id": seat_id,
                "status": (
                    SeatStatus.OFFERED.value if seat_id in offered else SeatStatus.AVAILABLE.value
                ),
            }
            for seat_id in show_seat_ids
        ],
    )

    return CancelResult(
        cancelled=True,
        seatsReleased=len(show_seat_ids),
        offeredToWaitlist=len(offers),
    )


# ------------------------------------------------------------- verification


async def verify_ticket(qr_token: str) -> TicketView:
    """
    What a scanned QR resolves to.

    Public by necessity — the person on the door is not logged in — so it
    returns only what a door needs: is this ticket real, for which show, and
    which seats. Never the customer's email or name. A QR code is a thing
    people photograph and forward.
    """
    async with Session() as session:
        booking = (
            (await session.execute(select(Booking).where(Booking.qr_token == qr_token)))
            .scalars()
            .first()
        )
        if booking is None:
            # A wrong token and a cancelled booking are different facts, and
            # the door staff need to tell them apart.
            raise ApiError.not_found("TICKET_NOT_FOUND", "This ticket is not recognised.")

        show = (
            (await session.execute(select(Show).where(Show.id == booking.show_id))).scalars().one()
        )
        event = (
            (await session.execute(select(Event).where(Event.id == show.event_id))).scalars().one()
        )
        venue = (
            (await session.execute(select(Venue).where(Venue.id == event.venue_id))).scalars().one()
        )
        seats = (
            (
                await session.execute(
                    select(Seat)
                    .join(ShowSeat, ShowSeat.seat_id == Seat.id)
                    .join(BookingSeat, BookingSeat.show_seat_id == ShowSeat.id)
                    .where(BookingSeat.booking_id == booking.id)
                    .order_by(Seat.row.asc(), Seat.number.asc())
                )
            )
            .scalars()
            .all()
        )

    return TicketView(
        valid=booking.status == BookingStatus.CONFIRMED,
        status=booking.status,
        reference=booking.reference,
        eventTitle=event.title,
        venue=venue.name,
        startsAt=iso(show.starts_at) or "",
        seats=[f"{s.row}{s.number}" for s in seats],
        checkedInAt=iso(booking.checked_in_at),
    )


async def check_in(qr_token: str, caller: TokenPayload) -> tuple[TicketView, bool]:
    """
    Admits a ticket at the door, exactly once.

    **Authenticated, unlike the read.** `GET /verify/:token` is public because
    the person on the door is not logged in and only needs to see whether a
    ticket is real. Admitting is a write, and a public write keyed on a bearer
    token is an attack: photograph somebody's QR — people post them — check it
    in before they arrive, and they are turned away at the door holding a valid
    ticket. So the scanner signs in, and only the event's organiser or an admin
    can burn a ticket.

    Locks the booking row. Two scanners on two doors reading the same QR at the
    same moment must not both be told "admitted": the second one has to wait,
    re-read, and be told the time the first one let them in.
    """
    async with transaction() as session:
        booking = (
            (
                await session.execute(
                    select(Booking).where(Booking.qr_token == qr_token).with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if booking is None:
            raise ApiError.not_found("TICKET_NOT_FOUND", "This ticket is not recognised.")

        show = (
            (await session.execute(select(Show).where(Show.id == booking.show_id))).scalars().one()
        )
        event = (
            (await session.execute(select(Event).where(Event.id == show.event_id))).scalars().one()
        )
        if caller["role"] != Role.ADMIN and event.organiser_id != caller["sub"]:
            raise ApiError.forbidden("This ticket belongs to another organiser's event.")

        if booking.status != BookingStatus.CONFIRMED:
            raise ApiError.conflict("TICKET_NOT_VALID", "This booking was cancelled. Do not admit.")
        if show.status is ShowStatus.CANCELLED:
            raise ApiError.conflict("SHOW_CANCELLED", "This show has been cancelled.")

        if booking.checked_in_at is not None:
            # The whole point of the milestone: say WHEN, so the door can tell a
            # duplicate from a mistake.
            raise ApiError.conflict(
                "ALREADY_CHECKED_IN",
                f"Already admitted at {booking.checked_in_at.strftime('%H:%M')}.",
            )

        booking.checked_in_at = utcnow()

    return await verify_ticket(qr_token), True
