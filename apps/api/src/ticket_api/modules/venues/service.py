from __future__ import annotations

import math

from psycopg.errors import UniqueViolation
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import Session
from ...errors import ApiError
from ...lib.geometry import generate_centre_stage_block, generate_end_stage_block
from ...models import EventType, Seat, StageLayout, Venue
from .schemas import (
    AddSeatBlockInput,
    CreateVenueInput,
    SeatBlockResult,
    SeatCount,
    SeatOut,
    SectionOut,
    UpdateVenueInput,
    VenueBase,
    VenueDetail,
    VenueSummary,
)


def _assert_capabilities_coherent(stage_layout: StageLayout, allowed: list[EventType]) -> None:
    """
    A centre-stage venue may not allow MOVIE.

    Nobody projects a film in the round, and refusing it here beats discovering
    it when a cinema's seat map renders as a circle.
    """
    if stage_layout is StageLayout.CENTRE_STAGE and EventType.MOVIE in allowed:
        raise ApiError.bad_request(
            "CENTRE_STAGE_CANNOT_SHOW_MOVIES",
            "A centre-stage venue surrounds the stage, so it cannot host a film. "
            "Allow CONCERT only, or use END_STAGE.",
        )


def _venue_base(venue: Venue) -> VenueBase:
    return VenueBase(
        id=venue.id,
        name=venue.name,
        address=venue.address,
        stageLayout=venue.stage_layout,
        allowedEventTypes=list(venue.allowed_event_types),
        turnaroundMinutes=venue.turnaround_minutes,
    )


async def list_venues() -> list[VenueSummary]:
    async with Session() as session:
        rows = (
            await session.execute(
                select(Venue, func.count(Seat.id))
                .outerjoin(Seat, Seat.venue_id == Venue.id)
                .group_by(Venue.id)
                .order_by(Venue.name.asc())
            )
        ).all()
    return [
        VenueSummary(
            id=venue.id,
            name=venue.name,
            address=venue.address,
            stageLayout=venue.stage_layout,
            allowedEventTypes=list(venue.allowed_event_types),
            turnaroundMinutes=venue.turnaround_minutes,
            count=SeatCount(seats=seats),
        )
        for venue, seats in rows
    ]


async def get_venue(venue_id: str) -> VenueDetail:
    async with Session() as session:
        venue = (await session.execute(select(Venue).where(Venue.id == venue_id))).scalars().first()
        if venue is None:
            raise ApiError.not_found("VENUE_NOT_FOUND", "No venue with that id.")

        seats = (
            (
                await session.execute(
                    select(Seat)
                    .where(Seat.venue_id == venue_id)
                    .order_by(Seat.pos_y.asc(), Seat.pos_x.asc())
                )
            )
            .scalars()
            .all()
        )

    return VenueDetail(
        id=venue.id,
        name=venue.name,
        address=venue.address,
        stageLayout=venue.stage_layout,
        allowedEventTypes=list(venue.allowed_event_types),
        turnaroundMinutes=venue.turnaround_minutes,
        seats=[SeatOut.model_validate(s) for s in seats],
    )


async def create_venue(data: CreateVenueInput) -> VenueBase:
    _assert_capabilities_coherent(data.stageLayout, data.allowedEventTypes)
    venue = Venue(
        name=data.name,
        address=data.address,
        stage_layout=data.stageLayout,
        allowed_event_types=data.allowedEventTypes,
        turnaround_minutes=data.turnaroundMinutes,
    )
    async with Session() as session:
        session.add(venue)
        await session.commit()
        await session.refresh(venue)
    return _venue_base(venue)


async def update_venue(venue_id: str, data: UpdateVenueInput) -> VenueBase:
    async with Session() as session:
        venue = (await session.execute(select(Venue).where(Venue.id == venue_id))).scalars().first()
        if venue is None:  # 404 before anything else
            raise ApiError.not_found("VENUE_NOT_FOUND", "No venue with that id.")

        # Merge BEFORE checking, so changing only one half of the pair cannot
        # produce an incoherent venue.
        _assert_capabilities_coherent(
            data.stageLayout or venue.stage_layout,
            data.allowedEventTypes or list(venue.allowed_event_types),
        )

        if data.name is not None:
            venue.name = data.name
        if data.address is not None:
            venue.address = data.address
        if data.stageLayout is not None:
            venue.stage_layout = data.stageLayout
        if data.allowedEventTypes is not None:
            venue.allowed_event_types = data.allowedEventTypes
        if data.turnaroundMinutes is not None:
            venue.turnaround_minutes = data.turnaroundMinutes

        await session.commit()
        await session.refresh(venue)

    return _venue_base(venue)


async def add_seat_block(venue_id: str, data: AddSeatBlockInput) -> SeatBlockResult:
    """
    Generates a block of seats using whichever layout the venue was built for.

    A new block is always placed outside or below everything already there, so
    sections never overlap and the caller never computes an offset.
    """
    venue = await get_venue(venue_id)  # 404 before anything else

    async with Session() as session:
        if venue.stageLayout is StageLayout.CENTRE_STAGE:
            start = await _outermost_radius(session, venue_id)
            positions = generate_centre_stage_block(
                rows=data.rows,
                seats_per_row=data.seatsPerRow,
                start_radius=start + 2,
                arc_start_degrees=data.arcStartDegrees,
                arc_span_degrees=data.arcSpanDegrees,
            )
        else:
            start = await _lowest_row(session, venue_id)
            positions = generate_end_stage_block(
                rows=data.rows, seats_per_row=data.seatsPerRow, start_y=start + 2
            )

        seats = [
            Seat(
                venue_id=venue_id,
                section=data.section,
                row=p.row,
                number=p.number,
                pos_x=p.pos_x,
                pos_y=p.pos_y,
            )
            for p in positions
        ]
        session.add_all(seats)

        try:
            await session.commit()
        except IntegrityError as err:
            await session.rollback()
            # unique(venueId, section, row, number) — re-adding the same block.
            if isinstance(err.orig, UniqueViolation):
                raise ApiError.conflict(
                    "SEATS_ALREADY_EXIST",
                    f'Section "{data.section}" already has seats with those row and number labels.',
                ) from err
            raise

    return SeatBlockResult(created=len(seats), section=data.section, startY=start + 2)


async def _lowest_row(session: AsyncSession, venue_id: str) -> float:
    """Lowest occupied grid row, or -2 so the first block starts at y = 0."""
    lowest = await session.scalar(select(func.max(Seat.pos_y)).where(Seat.venue_id == venue_id))
    return -2.0 if lowest is None else float(lowest)


async def _outermost_radius(session: AsyncSession, venue_id: str) -> float:
    """
    Radius of the outermost existing seat, or 1 so the first ring starts at 3 —
    far enough out to leave room for the stage in the middle.
    """
    seats = (
        await session.execute(select(Seat.pos_x, Seat.pos_y).where(Seat.venue_id == venue_id))
    ).all()
    if not seats:
        return 1.0
    return max(math.hypot(float(x), float(y)) for x, y in seats)


async def list_sections(venue_id: str) -> list[SectionOut]:
    """
    Sections in a venue with their seat counts — what a category may claim, and
    how many seats a price will cover.

    The count matters: pricing a section blind is how an organiser discovers at
    show-creation time that "Balcony" was four hundred seats.
    """
    async with Session() as session:
        rows = (
            await session.execute(
                select(Seat.section, func.count(Seat.id))
                .where(Seat.venue_id == venue_id)
                .group_by(Seat.section)
                .order_by(Seat.section.asc())
            )
        ).all()
    return [SectionOut(name=name, seatCount=int(count)) for name, count in rows]
