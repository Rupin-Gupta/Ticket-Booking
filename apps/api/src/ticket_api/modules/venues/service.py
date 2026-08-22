from __future__ import annotations

from psycopg.errors import UniqueViolation
from sqlalchemy import distinct, func, select
from sqlalchemy.exc import IntegrityError

from ...db import Session
from ...errors import ApiError
from ...models import Seat, Venue
from .schemas import (
    AddSeatBlockInput,
    CreateVenueInput,
    SeatBlockResult,
    SeatCount,
    SeatOut,
    UpdateVenueInput,
    VenueBase,
    VenueDetail,
    VenueSummary,
)

ROW_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


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
            id=venue.id, name=venue.name, address=venue.address, count=SeatCount(seats=seats)
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
        seats=[SeatOut.model_validate(s) for s in seats],
    )


async def create_venue(data: CreateVenueInput) -> VenueBase:
    venue = Venue(name=data.name, address=data.address)
    async with Session() as session:
        session.add(venue)
        await session.commit()
        await session.refresh(venue)
    return VenueBase(id=venue.id, name=venue.name, address=venue.address)


async def update_venue(venue_id: str, data: UpdateVenueInput) -> VenueBase:
    # Drop keys the client omitted. A PATCH body that left out `address` must
    # not blank it — "absent" and "explicitly null" are different requests.
    changes = data.model_dump(exclude_none=True)

    async with Session() as session:
        venue = (await session.execute(select(Venue).where(Venue.id == venue_id))).scalars().first()
        if venue is None:  # 404 before anything else
            raise ApiError.not_found("VENUE_NOT_FOUND", "No venue with that id.")

        for key, value in changes.items():
            setattr(venue, key, value)
        await session.commit()
        await session.refresh(venue)

    return VenueBase(id=venue.id, name=venue.name, address=venue.address)


async def add_seat_block(venue_id: str, data: AddSeatBlockInput) -> SeatBlockResult:
    """
    Generates a rectangular block of seats and appends it below whatever the
    venue already has.

    posX / posY are grid coordinates, not pixels — the frontend decides how big
    a seat is. New blocks start two rows below the lowest existing seat so
    sections stack visually instead of overlapping, and the caller never has to
    work out an offset.
    """
    await get_venue(venue_id)  # 404 before anything else

    async with Session() as session:
        lowest = await session.scalar(select(func.max(Seat.pos_y)).where(Seat.venue_id == venue_id))
        start_y = 0.0 if lowest is None else float(lowest) + 2

        seats = [
            Seat(
                venue_id=venue_id,
                section=data.section,
                row=ROW_LABELS[r],
                number=n,
                # Centre each row on x = 0 so rows of different widths stay
                # aligned.
                pos_x=n - (data.seatsPerRow + 1) / 2,
                pos_y=start_y + r,
            )
            for r in range(data.rows)
            for n in range(1, data.seatsPerRow + 1)
        ]
        session.add_all(seats)

        try:
            await session.commit()
        except IntegrityError as err:
            await session.rollback()
            # @@unique([venueId, section, row, number]) — re-adding the same block.
            if isinstance(err.orig, UniqueViolation):
                raise ApiError.conflict(
                    "SEATS_ALREADY_EXIST",
                    f'Section "{data.section}" already has seats with those row and number labels.',
                ) from err
            raise

    return SeatBlockResult(created=len(seats), section=data.section, startY=start_y)


async def list_sections(venue_id: str) -> list[str]:
    """Distinct section names in a venue — what a category is allowed to claim."""
    async with Session() as session:
        return list(
            (
                await session.execute(
                    select(distinct(Seat.section))
                    .where(Seat.venue_id == venue_id)
                    .order_by(Seat.section.asc())
                )
            )
            .scalars()
            .all()
        )
