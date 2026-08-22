from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db import Session, transaction
from ...errors import ApiError
from ...models import (
    Event,
    Role,
    SeatCategory,
    SeatStatus,
    Show,
    ShowSeat,
    Venue,
    WaitlistEntry,
    WaitlistStatus,
    iso,
    money,
    utcnow,
)
from ...realtime.emit import broadcast_seats, broadcast_status
from ...security import TokenPayload, random_token
from ..bookings.write import booking_view, write_booking
from .schemas import MyWaitlistEntry, OfferView, WaitlistJoined, WaitlistLeft


@dataclass(slots=True)
class PendingOffer:
    """An offer that was just created and whose email still has to be sent."""

    entry_id: str
    show_seat_id: str


# The two locking queries, kept as constants so the FOR UPDATE clauses are read
# as one thing rather than hunted for inside a function body.

_LOCK_SEAT = text(
    """
    SELECT id, "showId", "categoryId", status::text AS status
    FROM "ShowSeat"
    WHERE id = :seat_id
    FOR UPDATE
    """
)

# FIFO by joinedAt — the queue's whole promise.
#
# SKIP LOCKED is what makes concurrent advances safe: if another transaction is
# already offering this same person a different seat, we step over them and take
# the next in line instead of blocking and then handing one customer two offers.
# Plain FOR UPDATE would serialise here and, worse, could wake up to find the row
# already OFFERED.
_NEXT_IN_LINE = text(
    """
    SELECT id, "customerId"
    FROM "WaitlistEntry"
    WHERE "showId" = :show_id
      AND "categoryId" = :category_id
      AND status = 'WAITING'
    ORDER BY "joinedAt" ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED
    """
)


async def advance_waitlist(session: AsyncSession, show_seat_id: str) -> PendingOffer | None:
    """
    Offer the freed seat to the next person in line.

    **This is the only implementation of "a seat became free, find the next
    customer".** Booking cancellation calls it, and so does offer expiry. Rule 3
    exists because two copies drift: a fix to the ordering or the SKIP LOCKED
    clause lands in one and not the other, and the bug then only shows up on
    whichever path is rarer and less tested.

    Runs inside the caller's transaction. Returns the offer that needs an email,
    or None if the queue was empty and the seat went back on general sale — the
    caller sends after commit, never inside it.
    """
    # Lock the seat first. Without this, two cancellations freeing seats in the
    # same category could each read an empty-looking queue.
    seat = (await session.execute(_LOCK_SEAT, {"seat_id": show_seat_id})).mappings().first()
    if seat is None:
        return None

    # Every legitimate caller passes a seat that is BOOKED (a cancellation) or
    # OFFERED (an offer that lapsed or was given up). A HELD seat means somebody
    # is mid-checkout, and a future caller passing one here would silently take
    # a live hold away from a paying customer. Refuse rather than trust the
    # caller.
    if seat["status"] in ("HELD", "AVAILABLE"):
        raise RuntimeError(
            f"advance_waitlist called on a {seat['status']} seat ({show_seat_id}) — "
            "it must only be given a seat that has just been freed."
        )

    entry = (
        (
            await session.execute(
                _NEXT_IN_LINE,
                {"show_id": seat["showId"], "category_id": seat["categoryId"]},
            )
        )
        .mappings()
        .first()
    )

    if entry is None:
        # Nobody waiting — back on general sale.
        await session.execute(
            update(ShowSeat)
            .where(ShowSeat.id == show_seat_id)
            .values(
                status=SeatStatus.AVAILABLE,
                held_by_user_id=None,
                hold_expires_at=None,
                offer_expires_at=None,
            )
        )
        return None

    expires_at = utcnow() + timedelta(seconds=settings.OFFER_TTL_SECONDS)

    # OFFERED, not HELD. The two expire differently — an expired hold goes back
    # to AVAILABLE, an expired offer has to walk the queue — and collapsing them
    # would make the sweeper guess which kind of expiry it found (ADR-002).
    await session.execute(
        update(ShowSeat)
        .where(ShowSeat.id == show_seat_id)
        .values(
            status=SeatStatus.OFFERED,
            held_by_user_id=None,
            hold_expires_at=None,
            offer_expires_at=expires_at,
        )
    )
    await session.execute(
        update(WaitlistEntry)
        .where(WaitlistEntry.id == entry["id"])
        .values(
            status=WaitlistStatus.OFFERED,
            offered_seat_id=show_seat_id,
            # A bearer credential for a real seat: 32 CSPRNG bytes, single use,
            # time-limited, and checked against the logged-in customer on accept.
            offer_token=random_token(),
            offer_expires_at=expires_at,
        )
    )

    return PendingOffer(entry_id=entry["id"], show_seat_id=show_seat_id)


# ------------------------------------------------------------------ joining


async def _available_in_category(session: AsyncSession, show_id: str, category_id: str) -> int:
    """A category is sold out when it has no seat a customer could take now."""
    now = utcnow()
    return (
        await session.scalar(
            select(func.count())
            .select_from(ShowSeat)
            .where(
                ShowSeat.show_id == show_id,
                ShowSeat.category_id == category_id,
                or_(
                    ShowSeat.status == SeatStatus.AVAILABLE,
                    # An expired lease is free even if nothing has swept it yet,
                    # so it must count here too — otherwise a stale row makes a
                    # category look sold out and pushes someone into a queue
                    # they do not belong in.
                    (ShowSeat.status == SeatStatus.HELD) & (ShowSeat.hold_expires_at < now),
                    (ShowSeat.status == SeatStatus.OFFERED) & (ShowSeat.offer_expires_at < now),
                ),
            )
        )
        or 0
    )


async def _position_of(session: AsyncSession, show_id: str, category_id: str, joined_at) -> int:
    """
    How many people are ahead.

    Derived from joined_at, never stored — a stored position would need
    rewriting for everyone behind on every departure.
    """
    ahead = (
        await session.scalar(
            select(func.count())
            .select_from(WaitlistEntry)
            .where(
                WaitlistEntry.show_id == show_id,
                WaitlistEntry.category_id == category_id,
                WaitlistEntry.status == WaitlistStatus.WAITING,
                WaitlistEntry.joined_at < joined_at,
            )
        )
        or 0
    )
    return ahead + 1


async def join(show_id: str, category_id: str, caller: TokenPayload) -> WaitlistJoined:
    async with Session() as session:
        category = (
            (
                await session.execute(
                    select(SeatCategory)
                    .join(ShowSeat, ShowSeat.category_id == SeatCategory.id)
                    .where(SeatCategory.id == category_id, ShowSeat.show_id == show_id)
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if category is None:
            raise ApiError.bad_request(
                "CATEGORY_NOT_IN_SHOW", "That category is not part of this show."
            )

        if await _available_in_category(session, show_id, category_id) > 0:
            raise ApiError.conflict(
                "SEATS_STILL_AVAILABLE",
                f"{category.name} still has seats. Book one instead of waiting.",
            )

        # Refreshing the page must not buy a third place in line. Only live
        # states block — a previous entry that expired or was cancelled should
        # not lock somebody out forever.
        existing = (
            (
                await session.execute(
                    select(WaitlistEntry).where(
                        WaitlistEntry.show_id == show_id,
                        WaitlistEntry.category_id == category_id,
                        WaitlistEntry.customer_id == caller["sub"],
                        WaitlistEntry.status.in_([WaitlistStatus.WAITING, WaitlistStatus.OFFERED]),
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            raise ApiError.conflict(
                "ALREADY_WAITING",
                "You already have a seat offered to you for this category."
                if existing.status == WaitlistStatus.OFFERED
                else "You are already on the waitlist for this category.",
            )

        entry = WaitlistEntry(show_id=show_id, category_id=category_id, customer_id=caller["sub"])
        session.add(entry)
        await session.commit()
        await session.refresh(entry)

        position = await _position_of(session, show_id, category_id, entry.joined_at)

    return WaitlistJoined(id=entry.id, position=position)


async def list_mine(caller: TokenPayload) -> list[MyWaitlistEntry]:
    async with Session() as session:
        entries = (
            (
                await session.execute(
                    select(WaitlistEntry)
                    .where(
                        WaitlistEntry.customer_id == caller["sub"],
                        WaitlistEntry.status.in_([WaitlistStatus.WAITING, WaitlistStatus.OFFERED]),
                    )
                    .order_by(WaitlistEntry.joined_at.desc())
                )
            )
            .scalars()
            .all()
        )
        if not entries:
            return []

        categories = {
            c.id: c
            for c in (
                await session.execute(
                    select(SeatCategory).where(
                        SeatCategory.id.in_({e.category_id for e in entries})
                    )
                )
            ).scalars()
        }
        shows = {
            s.id: s
            for s in (
                await session.execute(select(Show).where(Show.id.in_({e.show_id for e in entries})))
            ).scalars()
        }
        events = {
            ev.id: ev
            for ev in (
                await session.execute(
                    select(Event).where(Event.id.in_({s.event_id for s in shows.values()}))
                )
            ).scalars()
        }

        out: list[MyWaitlistEntry] = []
        for entry in entries:
            show = shows[entry.show_id]
            event = events[show.event_id]
            category = categories[entry.category_id]
            out.append(
                MyWaitlistEntry(
                    id=entry.id,
                    status=entry.status,
                    joinedAt=iso(entry.joined_at) or "",
                    showId=entry.show_id,
                    eventId=event.id,
                    eventTitle=event.title,
                    startsAt=iso(show.starts_at) or "",
                    category=category.name,
                    price=money(category.price),
                    # Only ever this customer's own token — it is on their own
                    # entry, which the query above already scoped to them.
                    offerToken=(
                        entry.offer_token if entry.status == WaitlistStatus.OFFERED else None
                    ),
                    offerExpiresAt=iso(entry.offer_expires_at),
                    position=(
                        await _position_of(
                            session, entry.show_id, entry.category_id, entry.joined_at
                        )
                        if entry.status == WaitlistStatus.WAITING
                        else None
                    ),
                )
            )
    return out


async def leave(entry_id: str, caller: TokenPayload) -> tuple[WaitlistLeft, PendingOffer | None]:
    async with Session() as session:
        entry = (
            (await session.execute(select(WaitlistEntry).where(WaitlistEntry.id == entry_id)))
            .scalars()
            .first()
        )
        if entry is None:
            raise ApiError.not_found("WAITLIST_ENTRY_NOT_FOUND", "No waitlist entry with that id.")
        if entry.customer_id != caller["sub"] and caller["role"] != Role.ADMIN:
            raise ApiError.forbidden("That waitlist entry belongs to someone else.")
        status, offered_seat_id = entry.status, entry.offered_seat_id

    # Leaving while holding an offer must hand the seat on rather than stranding
    # it in OFFERED until the sweeper notices.
    if status == WaitlistStatus.OFFERED and offered_seat_id:
        async with transaction() as session:
            await session.execute(
                update(WaitlistEntry)
                .where(WaitlistEntry.id == entry_id)
                .values(
                    status=WaitlistStatus.CANCELLED,
                    offer_token=None,
                    offer_expires_at=None,
                    offered_seat_id=None,
                )
            )
            pending = await advance_waitlist(session, offered_seat_id)
        return WaitlistLeft(left=True, passedOn=pending is not None), pending

    async with Session() as session:
        await session.execute(
            update(WaitlistEntry)
            .where(WaitlistEntry.id == entry_id)
            .values(status=WaitlistStatus.CANCELLED)
        )
        await session.commit()
    return WaitlistLeft(left=True, passedOn=False), None


# ------------------------------------------------------------------- offers


async def get_offer(token: str) -> OfferView:
    async with Session() as session:
        entry = (
            (await session.execute(select(WaitlistEntry).where(WaitlistEntry.offer_token == token)))
            .scalars()
            .first()
        )
        if entry is None:
            raise ApiError.not_found("OFFER_NOT_FOUND", "This offer link is not recognised.")

        expired = entry.offer_expires_at is None or entry.offer_expires_at <= utcnow()
        if entry.status != WaitlistStatus.OFFERED or expired:
            # 410, not 404: the link was real, it has simply run out. The
            # customer deserves to know the difference.
            raise ApiError.gone(
                "OFFER_EXPIRED", "This offer has expired and the seat went to someone else."
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

    return OfferView(
        showId=show.id,
        eventId=event.id,
        eventTitle=event.title,
        venue=venue.name,
        startsAt=iso(show.starts_at) or "",
        category=category.name,
        price=money(category.price),
        expiresAt=iso(entry.offer_expires_at) or "",
    )


_LOCK_ENTRY_BY_TOKEN = text(
    """
    SELECT id, "customerId", status::text AS status, "offerExpiresAt",
           "offeredSeatId", "showId"
    FROM "WaitlistEntry"
    WHERE "offerToken" = :token
    FOR UPDATE
    """
)

_LOCK_OFFERED_SEAT = text(
    """
    SELECT id, status::text AS status, "categoryId"
    FROM "ShowSeat"
    WHERE id = :seat_id
    FOR UPDATE
    """
)


async def accept_offer(token: str, caller: TokenPayload):
    """
    Accept an offer and turn it into a booking.

    Five checks, all of them load-bearing:
      1. the token resolves to an entry
      2. that entry is still OFFERED
      3. the offer has not expired
      4. the seat is still OFFERED — not swept, not taken
      5. the caller is the customer the offer was made to

    Five matters because the token is a bearer credential that arrives by
    email: without (5) anyone who sees the link can take the seat, and without
    (4) a race with the sweeper could sell a seat already offered onward.
    """
    async with transaction() as session:
        entry = (await session.execute(_LOCK_ENTRY_BY_TOKEN, {"token": token})).mappings().first()
        if entry is None:
            raise ApiError.not_found("OFFER_NOT_FOUND", "This offer link is not recognised.")

        if entry["customerId"] != caller["sub"]:
            # Deliberately vague. Telling a stranger "this is somebody else's
            # valid offer" confirms the link is live.
            raise ApiError.forbidden("This offer is not yours.")
        if entry["status"] != "OFFERED" or not entry["offeredSeatId"]:
            raise ApiError.gone("OFFER_EXPIRED", "This offer is no longer open.")
        if entry["offerExpiresAt"] is None or entry["offerExpiresAt"] <= utcnow():
            raise ApiError.gone("OFFER_EXPIRED", "This offer has expired.")

        seat = (
            (await session.execute(_LOCK_OFFERED_SEAT, {"seat_id": entry["offeredSeatId"]}))
            .mappings()
            .first()
        )
        if seat is None or seat["status"] != "OFFERED":
            raise ApiError.gone("OFFER_EXPIRED", "That seat is no longer available.")

        booking_id = await write_booking(
            session,
            show_id=entry["showId"],
            customer_id=caller["sub"],
            seats=[(seat["id"], seat["categoryId"])],
        )

        await session.execute(
            update(WaitlistEntry)
            .where(WaitlistEntry.id == entry["id"])
            # Token cleared: single use. A link that still works after the seat
            # is booked is a link somebody will try again.
            .values(status=WaitlistStatus.CONVERTED, offer_token=None, offer_expires_at=None)
        )

        view = await booking_view(session, booking_id)
        show_id, seat_id = entry["showId"], seat["id"]

    broadcast_status(show_id, [seat_id], SeatStatus.BOOKED.value)
    return view


# ------------------------------------------------------------------ sweeping


async def sweep_expired_offers() -> tuple[int, list[PendingOffer]]:
    """
    Expire offers whose clock has run out and pass each seat down the queue.

    Note what this does NOT do: set the seat to AVAILABLE. An expired offer
    means "this person did not take it", not "nobody wants it" —
    advance_waitlist decides, and only returns the seat to general sale when the
    queue is genuinely empty. That is the loop that makes an ignored offer walk
    the line on its own.
    """
    async with Session() as session:
        due = (
            await session.execute(
                select(WaitlistEntry.id, WaitlistEntry.offered_seat_id)
                .where(
                    WaitlistEntry.status == WaitlistStatus.OFFERED,
                    WaitlistEntry.offer_expires_at <= utcnow(),
                )
                # Bounded so one slow tick cannot try to process thousands.
                .limit(50)
            )
        ).all()

    offers: list[PendingOffer] = []
    touched: list[tuple[str, str, str]] = []

    for entry_id, offered_seat_id in due:
        # One transaction per seat. A single transaction over all of them would
        # hold every lock until the slowest finished, and one failure would roll
        # back expiries that had nothing to do with it.
        async with transaction() as session:
            still = await session.scalar(
                select(WaitlistEntry.status).where(WaitlistEntry.id == entry_id)
            )
            # Another sweeper, or the customer accepting at the last second, may
            # have moved it since the list above was read.
            if still != WaitlistStatus.OFFERED:
                continue

            await session.execute(
                update(WaitlistEntry)
                .where(WaitlistEntry.id == entry_id)
                .values(
                    status=WaitlistStatus.EXPIRED,
                    offer_token=None,
                    offer_expires_at=None,
                )
            )
            pending = await advance_waitlist(session, offered_seat_id) if offered_seat_id else None

        if pending:
            offers.append(pending)

        if offered_seat_id:
            async with Session() as session:
                seat = (
                    await session.execute(
                        select(ShowSeat.show_id, ShowSeat.status).where(
                            ShowSeat.id == offered_seat_id
                        )
                    )
                ).first()
            if seat and seat.status in (SeatStatus.OFFERED, SeatStatus.AVAILABLE):
                touched.append((seat.show_id, offered_seat_id, seat.status.value))

    # Offers that moved on: the new holder's seat stays OFFERED, and a seat with
    # nobody left behind it goes back on sale. Both need announcing, because to
    # every other viewer these changed without anyone clicking anything.
    for show_id, seat_id, status in touched:
        broadcast_seats(show_id, [{"id": seat_id, "status": status}])

    return len(due), offers
