from __future__ import annotations

from psycopg.errors import UniqueViolation
from sqlalchemy import distinct, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import Session, transaction
from ...errors import ApiError
from ...models import (
    Event,
    Role,
    Seat,
    SeatCategory,
    Show,
    ShowSeat,
    User,
    Venue,
    iso,
    money,
    utcnow,
)
from ...security import TokenPayload
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

    # Only upcoming shows: a listing full of last month's dates is noise.
    show_query = (
        select(Show)
        .where(Show.event_id == event.id, Show.starts_at >= utcnow())
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
        venue = await session.scalar(select(Venue.id).where(Venue.id == data.venueId))
        if venue is None:
            raise ApiError.bad_request("VENUE_NOT_FOUND", "No venue with that id.")

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

    async with Session() as session:
        event = await _assert_owns(session, event_id, caller)
        for key, value in changes.items():
            setattr(event, key, value)
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
        venue_id = event.venue_id

    # One transaction: a show whose seats failed to generate is worse than no
    # show at all — it renders as a bookable date with an empty seat map.
    async with transaction() as session:
        show = Show(event_id=event_id, starts_at=data.startsAt)
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
        event=ShowEvent(
            id=event.id,
            title=event.title,
            type=event.type,
            venue=VenueRef(id=venue.id, name=venue.name, address=venue.address),
            categories=[_category_out(c, with_sections=False) for c in categories],
        ),
        count={"showSeats": seat_count},
    )
