from __future__ import annotations

from psycopg.errors import UniqueViolation
from sqlalchemy import delete as sql_delete
from sqlalchemy import distinct, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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
    SeatCategory,
    SeatStatus,
    Show,
    ShowSeat,
    ShowStatus,
    User,
    Venue,
    WaitlistEntry,
    WaitlistStatus,
    iso,
    money,
    utcnow,
)
from ...realtime.emit import broadcast_status
from ...security import TokenPayload
from ..venues.scheduling import assert_venue_free, occupied_window
from .schemas import (
    CategoryOut,
    CreateCategoryInput,
    CreateEventInput,
    CreateShowInput,
    EventListResult,
    EventOut,
    EventWritten,
    ListEventsQuery,
    OrganiserRef,
    OwnEvent,
    ShowCancelled,
    ShowCreated,
    ShowDetail,
    ShowEvent,
    ShowRef,
    UpdateEventInput,
    VenueRef,
)


async def _assert_owns(session: AsyncSession, event_id: str, caller: TokenPayload) -> Event:
    """
    Role alone is not authorisation. `require_role(ORGANISER)` says "some
    organiser"; this says "the organiser who owns this event". Without it any
    organiser can edit any other organiser's event and read their revenue.

    ADMIN passes deliberately — an admin exists to fix things.
    """
    event = (await session.execute(select(Event).where(Event.id == event_id))).scalars().first()
    if event is None:
        raise ApiError.not_found("EVENT_NOT_FOUND", "No event with that id.")
    if caller["role"] != Role.ADMIN and event.organiser_id != caller["sub"]:
        raise ApiError.forbidden("This event belongs to another organiser.")
    return event


def _category_out(category: SeatCategory, *, with_sections: bool) -> CategoryOut:
    return CategoryOut(
        id=category.id,
        name=category.name,
        price=money(category.price),
        sections=list(category.sections) if with_sections else None,
    )


# ------------------------------------------------------------------- events


async def list_events(query: ListEventsQuery) -> EventListResult:
    filters = []
    if query.type is not None:
        filters.append(Event.type == query.type)
    if query.venueId:
        filters.append(Event.venue_id == query.venueId)
    if query.q:
        filters.append(Event.title.ilike(f"%{query.q}%"))
    if query.from_ is not None or query.to is not None:
        window = [Show.event_id == Event.id]
        if query.from_ is not None:
            window.append(Show.starts_at >= query.from_)
        if query.to is not None:
            window.append(Show.starts_at <= query.to)
        filters.append(select(Show.id).where(*window).exists())

    async with Session() as session:
        total = await session.scalar(select(func.count()).select_from(Event).where(*filters)) or 0
        events = (
            (
                await session.execute(
                    select(Event)
                    .where(*filters)
                    .order_by(Event.title.asc())
                    .limit(query.limit)
                    .offset(query.offset)
                )
            )
            .scalars()
            .all()
        )

        out = [
            await _event_out(session, event, with_sections=False, show_limit=5) for event in events
        ]

    return EventListResult(events=out, total=total, limit=query.limit, offset=query.offset)


async def _event_out(
    session: AsyncSession,
    event: Event,
    *,
    with_sections: bool,
    show_limit: int | None,
    with_seat_counts: bool = False,
) -> EventOut:
    venue = (await session.execute(select(Venue).where(Venue.id == event.venue_id))).scalars().one()
    organiser = (
        (await session.execute(select(User).where(User.id == event.organiser_id))).scalars().one()
    )
    categories = (
        (
            await session.execute(
                select(SeatCategory)
                .where(SeatCategory.event_id == event.id)
                .order_by(SeatCategory.price.desc())
            )
        )
        .scalars()
        .all()
    )

    # Only upcoming, still-scheduled shows: a listing full of last month's
    # dates is noise, and a cancelled show is not something to sell.
    show_query = (
        select(Show)
        .where(
            Show.event_id == event.id,
            Show.starts_at >= utcnow(),
            Show.status == ShowStatus.SCHEDULED,
        )
        .order_by(Show.starts_at.asc())
    )
    if show_limit is not None:
        show_query = show_query.limit(show_limit)
    shows = (await session.execute(show_query)).scalars().all()

    seat_counts: dict[str, int] = {}
    if with_seat_counts and shows:
        rows = (
            await session.execute(
                select(ShowSeat.show_id, func.count(ShowSeat.id))
                .where(ShowSeat.show_id.in_([s.id for s in shows]))
                .group_by(ShowSeat.show_id)
            )
        ).all()
        seat_counts = dict(rows)

    return EventOut(
        id=event.id,
        title=event.title,
        type=event.type,
        description=event.description,
        venue=VenueRef(id=venue.id, name=venue.name, address=venue.address),
        organiser=OrganiserRef(id=organiser.id, name=organiser.name),
        categories=[_category_out(c, with_sections=with_sections) for c in categories],
        shows=[
            ShowRef(
                id=s.id,
                startsAt=iso(s.starts_at) or "",
                count={"showSeats": seat_counts.get(s.id, 0)} if with_seat_counts else None,
            )
            for s in shows
        ],
    )


async def get_event(event_id: str) -> EventOut:
    async with Session() as session:
        event = (await session.execute(select(Event).where(Event.id == event_id))).scalars().first()
        if event is None:
            raise ApiError.not_found("EVENT_NOT_FOUND", "No event with that id.")
        return await _event_out(
            session, event, with_sections=True, show_limit=None, with_seat_counts=True
        )


async def create_event(data: CreateEventInput, caller: TokenPayload) -> EventWritten:
    async with Session() as session:
        venue = (
            (await session.execute(select(Venue).where(Venue.id == data.venueId))).scalars().first()
        )
        if venue is None:
            raise ApiError.bad_request("VENUE_NOT_FOUND", "No venue with that id.")

        # A venue is admin-owned infrastructure; an organiser books it, and
        # cannot put a film in a room built for concerts.
        if data.type not in venue.allowed_event_types:
            allowed = " and ".join(t.value for t in venue.allowed_event_types)
            raise ApiError.bad_request(
                "EVENT_TYPE_NOT_ALLOWED", f"This venue hosts {allowed} only."
            )

        event = Event(
            venue_id=data.venueId,
            title=data.title,
            type=data.type,
            description=data.description,
            organiser_id=caller["sub"],
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)

    return EventWritten(
        id=event.id,
        title=event.title,
        type=event.type,
        description=event.description,
        venueId=event.venue_id,
    )


async def update_event(event_id: str, data: UpdateEventInput, caller: TokenPayload) -> EventWritten:
    # Omitted keys must not blank a column: "absent" and "explicitly null" are
    # different requests, and a PATCH that left out `description` means leave it.
    changes = data.model_dump(exclude_none=True)

    # Wire names are camelCase and ORM attributes are snake_case. Where they
    # happen to match, setattr works by luck; where they do not, an unmapped key
    # sets a stray attribute on the instance and silently never reaches the
    # database. Naming the translation stops the next added field from being a
    # silent no-op.
    ATTR = {"publishSeatSignals": "publish_seat_signals"}

    async with Session() as session:
        event = await _assert_owns(session, event_id, caller)
        for key, value in changes.items():
            setattr(event, ATTR.get(key, key), value)
        await session.commit()
        await session.refresh(event)

    return EventWritten(
        id=event.id,
        title=event.title,
        type=event.type,
        description=event.description,
        venueId=event.venue_id,
    )


async def list_own_events(caller: TokenPayload) -> list[OwnEvent]:
    async with Session() as session:
        filters = [] if caller["role"] == Role.ADMIN else [Event.organiser_id == caller["sub"]]
        events = (
            (await session.execute(select(Event).where(*filters).order_by(Event.title.asc())))
            .scalars()
            .all()
        )
        if not events:
            return []

        event_ids = [e.id for e in events]
        venues = {
            v.id: v
            for v in (
                await session.execute(
                    select(Venue).where(Venue.id.in_({e.venue_id for e in events}))
                )
            ).scalars()
        }
        categories: dict[str, list[SeatCategory]] = {}
        for c in (
            await session.execute(select(SeatCategory).where(SeatCategory.event_id.in_(event_ids)))
        ).scalars():
            categories.setdefault(c.event_id, []).append(c)

        show_counts = dict(
            (
                await session.execute(
                    select(Show.event_id, func.count(Show.id))
                    .where(Show.event_id.in_(event_ids))
                    .group_by(Show.event_id)
                )
            ).all()
        )

    return [
        OwnEvent(
            id=e.id,
            title=e.title,
            type=e.type,
            venue=VenueRef(id=venues[e.venue_id].id, name=venues[e.venue_id].name),
            categories=[_category_out(c, with_sections=True) for c in categories.get(e.id, [])],
            count={"shows": show_counts.get(e.id, 0)},
        )
        for e in events
    ]


# --------------------------------------------------------------- categories


async def create_category(
    event_id: str, data: CreateCategoryInput, caller: TokenPayload
) -> CategoryOut:
    async with Session() as session:
        event = await _assert_owns(session, event_id, caller)

        venue_sections = list(
            (
                await session.execute(
                    select(distinct(Seat.section)).where(Seat.venue_id == event.venue_id)
                )
            )
            .scalars()
            .all()
        )
        existing = (
            (await session.execute(select(SeatCategory).where(SeatCategory.event_id == event_id)))
            .scalars()
            .all()
        )

        if not venue_sections:
            raise ApiError.bad_request(
                "VENUE_HAS_NO_SEATS", "Add seats to the venue before pricing its sections."
            )

        # Catching this here means instantiate_show_seats() never meets a seat
        # it cannot price, which is a far worse place to discover the problem.
        unknown = [s for s in data.sections if s not in venue_sections]
        if unknown:
            raise ApiError.bad_request(
                "UNKNOWN_SECTION",
                f"This venue has no section named {', '.join(unknown)}. "
                f"It has: {', '.join(venue_sections)}.",
            )

        # Two categories claiming one section would make a seat's price
        # ambiguous.
        claimed: dict[str, str] = {}
        for category in existing:
            for section in category.sections:
                claimed[section] = category.name
        taken = [s for s in data.sections if s in claimed]
        if taken:
            detail = "; ".join(f'"{s}" is already priced by {claimed[s]}' for s in taken)
            raise ApiError.conflict("SECTION_ALREADY_PRICED", f"{detail}.")

        category = SeatCategory(
            event_id=event_id, name=data.name, price=data.price, sections=data.sections
        )
        session.add(category)
        try:
            await session.commit()
        except IntegrityError as err:
            await session.rollback()
            if isinstance(err.orig, UniqueViolation):
                raise ApiError.conflict(
                    "CATEGORY_EXISTS", f'This event already has a "{data.name}" category.'
                ) from err
            raise
        await session.refresh(category)

    return _category_out(category, with_sections=True)


# -------------------------------------------------------------------- shows


async def create_show(event_id: str, data: CreateShowInput, caller: TokenPayload) -> ShowCreated:
    async with Session() as session:
        event = await _assert_owns(session, event_id, caller)
        venue = (
            (await session.execute(select(Venue).where(Venue.id == event.venue_id))).scalars().one()
        )
        venue_id = venue.id
        turnaround = venue.turnaround_minutes

    ends_at, occupies_until = occupied_window(
        starts_at=data.startsAt,
        duration_minutes=data.durationMinutes,
        turnaround_minutes=turnaround,
    )

    # One transaction: a show whose seats failed to generate is worse than no
    # show at all — it renders as a bookable date with an empty seat map.
    async with transaction() as session:
        await assert_venue_free(
            session,
            venue_id=venue_id,
            starts_at=data.startsAt,
            occupies_until=occupies_until,
        )

        show = Show(
            event_id=event_id,
            venue_id=venue_id,
            starts_at=data.startsAt,
            duration_minutes=data.durationMinutes,
            ends_at=ends_at,
            occupies_until=occupies_until,
        )
        session.add(show)
        await session.flush()

        seat_count = await instantiate_show_seats(
            session, show_id=show.id, event_id=event_id, venue_id=venue_id
        )
        show_id, starts_at = show.id, show.starts_at

    return ShowCreated(id=show_id, startsAt=iso(starts_at) or "", seatCount=seat_count)


async def instantiate_show_seats(
    session: AsyncSession, *, show_id: str, event_id: str, venue_id: str
) -> int:
    """
    Materialises one ShowSeat per venue seat, priced by whichever category
    claims that seat's section.

    A physical Seat carries no status — a chair does not know whether it is
    sold. These rows are what every hold, booking and waitlist offer locks, and
    they exist from the moment the show does so the seat map is never partial.

    Runs inside the caller's transaction; @@unique([showId, seatId]) makes a
    double-instantiation impossible rather than merely unlikely.
    """
    seats = (await session.execute(select(Seat).where(Seat.venue_id == venue_id))).scalars().all()
    categories = (
        (await session.execute(select(SeatCategory).where(SeatCategory.event_id == event_id)))
        .scalars()
        .all()
    )

    if not seats:
        raise ApiError.bad_request("VENUE_HAS_NO_SEATS", "This venue has no seats yet.")

    category_for_section: dict[str, str] = {}
    for category in categories:
        for section in category.sections:
            category_for_section[section] = category.id

    unpriced: set[str] = set()
    rows: list[ShowSeat] = []
    for seat in seats:
        category_id = category_for_section.get(seat.section)
        if category_id is None:
            unpriced.add(seat.section)
            continue
        rows.append(ShowSeat(show_id=show_id, seat_id=seat.id, category_id=category_id))

    # Refuse rather than generate a half-priced seat map. Every seat must have
    # a price before anyone can be sold one.
    if unpriced:
        raise ApiError.bad_request(
            "SECTION_NOT_PRICED",
            f"No category covers {', '.join(sorted(unpriced))}. Add one before creating a show.",
        )

    session.add_all(rows)
    await session.flush()
    return len(rows)


async def get_show(show_id: str) -> ShowDetail:
    async with Session() as session:
        show = (await session.execute(select(Show).where(Show.id == show_id))).scalars().first()
        if show is None:
            raise ApiError.not_found("SHOW_NOT_FOUND", "No show with that id.")

        event = (
            (await session.execute(select(Event).where(Event.id == show.event_id))).scalars().one()
        )
        venue = (
            (await session.execute(select(Venue).where(Venue.id == event.venue_id))).scalars().one()
        )
        categories = (
            (
                await session.execute(
                    select(SeatCategory)
                    .where(SeatCategory.event_id == event.id)
                    .order_by(SeatCategory.price.desc())
                )
            )
            .scalars()
            .all()
        )
        seat_count = (
            await session.scalar(
                select(func.count()).select_from(ShowSeat).where(ShowSeat.show_id == show_id)
            )
            or 0
        )

    return ShowDetail(
        id=show.id,
        startsAt=iso(show.starts_at) or "",
        status=show.status.value,
        event=ShowEvent(
            id=event.id,
            title=event.title,
            type=event.type,
            venue=VenueRef(id=venue.id, name=venue.name, address=venue.address),
            categories=[_category_out(c, with_sections=False) for c in categories],
        ),
        count={"showSeats": seat_count},
    )


async def delete_event(event_id: str, caller: TokenPayload) -> None:
    """
    Delete an event and the scaffolding underneath it: categories, shows, the
    generated `ShowSeat` rows and any waitlist entries.

    Refuses the moment a booking exists on any of its shows — including a
    cancelled one. A booking is the record of somebody paying, and it is the
    one thing here that cannot be regenerated from anything else. Nothing else
    under an event is precious: categories are prices, shows are dates, and
    `ShowSeat` rows are made by `instantiate_show_seats()` from the venue's
    seats, so all of it can be rebuilt.

    The order below is the foreign-key order, deepest first.
    """
    async with Session() as session:
        event = await _assert_owns(session, event_id, caller)

        show_ids = list(
            (await session.execute(select(Show.id).where(Show.event_id == event_id)))
            .scalars()
            .all()
        )

        if show_ids:
            bookings = int(
                await session.scalar(
                    select(func.count(Booking.id)).where(Booking.show_id.in_(show_ids))
                )
                or 0
            )
            if bookings:
                raise ApiError.conflict(
                    "EVENT_HAS_BOOKINGS",
                    f"{bookings} booking{'s' if bookings != 1 else ''} exist on this event. "
                    "Cancel the shows instead — deleting would destroy the ticket history.",
                )

            await session.execute(
                sql_delete(WaitlistEntry).where(WaitlistEntry.show_id.in_(show_ids))
            )
            await session.execute(sql_delete(ShowSeat).where(ShowSeat.show_id.in_(show_ids)))
            await session.execute(sql_delete(Show).where(Show.id.in_(show_ids)))

        await session.execute(sql_delete(SeatCategory).where(SeatCategory.event_id == event_id))
        await session.delete(event)
        await session.commit()


async def cancel_show(show_id: str, caller: TokenPayload) -> ShowCancelled:
    """
    Cancels a show and unwinds everything hanging off it.

    The order matters, and so does what is deliberately *not* done:

      1. lock the show, refuse if already cancelled or already started
      2. cancel every confirmed booking and release its seats
      3. close every waitlist entry — WAITING and OFFERED alike
      4. reset the show's seats so nothing reads as claimed
      5. after the commit: email the affected customers, tell the room

    **`advance_waitlist()` is not called here, and that is the point.** Every
    other path that frees a seat offers it to the next person in line; this one
    must not, because the seat being freed belongs to a show that is no longer
    happening. Handing it on would email somebody an offer for a cancelled
    show — a rule-3 exception, so it is written down rather than left to be
    rediscovered.

    Cancelling also frees the venue slot with no extra work: the exclusion
    constraint is partial on `status = 'SCHEDULED'`, so the row stops
    participating the moment it flips.
    """
    async with transaction() as session:
        show = (
            (await session.execute(select(Show).where(Show.id == show_id).with_for_update()))
            .scalars()
            .first()
        )
        if show is None:
            raise ApiError.not_found("SHOW_NOT_FOUND", "No show with that id.")

        await _assert_owns(session, show.event_id, caller)

        if show.status is ShowStatus.CANCELLED:
            raise ApiError.conflict("SHOW_ALREADY_CANCELLED", "That show is already cancelled.")

        now = utcnow()
        if show.starts_at <= now:
            # Cancelling a show the audience is already sitting in is not a
            # booking problem, and refunding it automatically would be wrong.
            raise ApiError.conflict(
                "SHOW_ALREADY_STARTED", "This show has already started and cannot be cancelled."
            )

        show.status = ShowStatus.CANCELLED

        live = (
            await session.execute(
                select(Booking.id, Booking.customer_id).where(
                    Booking.show_id == show_id, Booking.status == BookingStatus.CONFIRMED
                )
            )
        ).all()
        booking_ids = [row.id for row in live]
        # One customer holding three bookings is one person to email, not three
        # notifications — the count reported back should say so.
        customers = len({row.customer_id for row in live})

        if booking_ids:
            await session.execute(
                update(Booking)
                .where(Booking.id.in_(booking_ids))
                .values(status=BookingStatus.CANCELLED, cancelled_at=now)
            )
            # Release the claim without deleting the row — the price paid is
            # revenue history, and the email still needs the seat labels.
            await session.execute(
                update(BookingSeat)
                .where(BookingSeat.booking_id.in_(booking_ids), BookingSeat.released_at.is_(None))
                .values(released_at=now)
            )

        waitlist_closed = int(
            await session.scalar(
                select(func.count(WaitlistEntry.id)).where(
                    WaitlistEntry.show_id == show_id,
                    WaitlistEntry.status.in_([WaitlistStatus.WAITING, WaitlistStatus.OFFERED]),
                )
            )
            or 0
        )
        if waitlist_closed:
            # The offer token goes too. It is a bearer credential for a seat at
            # a show that no longer exists, and a live token outliving its
            # purpose is exactly the kind of thing that gets redeemed later.
            await session.execute(
                update(WaitlistEntry)
                .where(
                    WaitlistEntry.show_id == show_id,
                    WaitlistEntry.status.in_([WaitlistStatus.WAITING, WaitlistStatus.OFFERED]),
                )
                .values(
                    status=WaitlistStatus.CANCELLED,
                    offered_seat_id=None,
                    offer_token=None,
                    offer_expires_at=None,
                )
            )

        seat_ids = list(
            (await session.execute(select(ShowSeat.id).where(ShowSeat.show_id == show_id)))
            .scalars()
            .all()
        )
        # Nothing is held, offered or booked on a cancelled show. Leaving rows
        # reading BOOKED while their booking says CANCELLED is a lie the seat
        # map would happily render.
        await session.execute(
            update(ShowSeat)
            .where(ShowSeat.show_id == show_id)
            .values(
                status=SeatStatus.AVAILABLE,
                held_by_user_id=None,
                hold_expires_at=None,
                offer_expires_at=None,
            )
        )

    # After the commit, never inside it: a rolled-back transaction must not have
    # already emailed anybody that their show is off.
    for booking_id in booking_ids:
        await enqueue_email({"kind": "show-cancelled", "bookingId": booking_id})

    broadcast_status(show_id, seat_ids, SeatStatus.AVAILABLE.value)

    return ShowCancelled(
        id=show_id,
        status=ShowStatus.CANCELLED.value,
        bookingsCancelled=len(booking_ids),
        customersNotified=customers,
        waitlistClosed=waitlist_closed,
    )
