from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from ...db import Session
from ...errors import ApiError
from ...models import (
    Booking,
    BookingSeat,
    BookingStatus,
    Event,
    Role,
    SeatCategory,
    Show,
    ShowSeat,
    Venue,
    WaitlistEntry,
    WaitlistStatus,
    iso,
    money,
)
from ...security import TokenPayload
from .schemas import (
    CategorySummary,
    EventRef,
    EventSummary,
    ShowSummary,
    Totals,
)

ZERO = Decimal(0)


async def event_summary(event_id: str, caller: TokenPayload) -> EventSummary:
    """
    Revenue and sales for one event.

    Money is summed from `BookingSeat.priceAtBooking`, never from the category's
    current price. Those are different numbers the moment an organiser re-prices
    anything, and the one the customer actually paid is the one on the row.

    Cancelled bookings are excluded by filtering on the booking's status rather
    than on `releasedAt`: status is the authoritative record of whether money was
    kept, and `releasedAt` exists to free the seat, which is a related but
    separate fact.
    """
    async with Session() as session:
        event = (await session.execute(select(Event).where(Event.id == event_id))).scalars().first()
        if event is None:
            raise ApiError.not_found("EVENT_NOT_FOUND", "No event with that id.")

        # Role says "some organiser"; this says "the organiser who owns this
        # event". Without it any organiser could read any other's revenue.
        if caller["role"] != Role.ADMIN and event.organiser_id != caller["sub"]:
            raise ApiError.forbidden("This event belongs to another organiser.")

        venue = (
            (await session.execute(select(Venue).where(Venue.id == event.venue_id))).scalars().one()
        )
        categories = (
            (
                await session.execute(
                    select(SeatCategory)
                    .where(SeatCategory.event_id == event_id)
                    .order_by(SeatCategory.price.desc())
                )
            )
            .scalars()
            .all()
        )
        shows = (
            (
                await session.execute(
                    select(Show).where(Show.event_id == event_id).order_by(Show.starts_at.asc())
                )
            )
            .scalars()
            .all()
        )
        show_ids = [s.id for s in shows]

        sold_rows: list[tuple[str, str, Decimal]] = []
        capacity_rows: list[tuple[str, str, int]] = []
        booking_rows: list[tuple[str, BookingStatus, int]] = []
        waiting_rows: list[tuple[str, int]] = []

        if show_ids:
            # ponytail: one GROUP BY per fact rather than a single wide join.
            # An event has hundreds of seats, not millions, and four small
            # aggregates read far more clearly than one query with three
            # left joins and a CASE.
            sold_rows = list(
                (
                    await session.execute(
                        select(
                            ShowSeat.show_id,
                            ShowSeat.category_id,
                            func.sum(BookingSeat.price_at_booking),
                            func.count(BookingSeat.id),
                        )
                        .join(BookingSeat, BookingSeat.show_seat_id == ShowSeat.id)
                        .join(Booking, Booking.id == BookingSeat.booking_id)
                        .where(
                            Booking.show_id.in_(show_ids),
                            Booking.status == BookingStatus.CONFIRMED,
                        )
                        .group_by(ShowSeat.show_id, ShowSeat.category_id)
                    )
                ).all()
            )
            capacity_rows = list(
                (
                    await session.execute(
                        select(ShowSeat.show_id, ShowSeat.category_id, func.count(ShowSeat.id))
                        .where(ShowSeat.show_id.in_(show_ids))
                        .group_by(ShowSeat.show_id, ShowSeat.category_id)
                    )
                ).all()
            )
            booking_rows = list(
                (
                    await session.execute(
                        select(Booking.show_id, Booking.status, func.count(Booking.id))
                        .where(Booking.show_id.in_(show_ids))
                        .group_by(Booking.show_id, Booking.status)
                    )
                ).all()
            )
            waiting_rows = list(
                (
                    await session.execute(
                        select(WaitlistEntry.category_id, func.count(WaitlistEntry.id))
                        .where(
                            WaitlistEntry.show_id.in_(show_ids),
                            WaitlistEntry.status == WaitlistStatus.WAITING,
                        )
                        .group_by(WaitlistEntry.category_id)
                    )
                ).all()
            )

    sold_by_cell = {
        (show_id, category_id): (int(count), Decimal(revenue or 0))
        for show_id, category_id, revenue, count in sold_rows
    }
    capacity_by_cell = {
        (show_id, category_id): int(count) for show_id, category_id, count in capacity_rows
    }
    waiting_by_category = {category_id: int(n) for category_id, n in waiting_rows}

    per_category: list[CategorySummary] = []
    for category in categories:
        seats = seats_sold = 0
        # Decimal arithmetic throughout. A float cannot hold 0.10, and revenue
        # that is off by a cent is revenue somebody will ask about.
        revenue = ZERO
        for show in shows:
            cell = (show.id, category.id)
            seats += capacity_by_cell.get(cell, 0)
            if cell in sold_by_cell:
                count, amount = sold_by_cell[cell]
                seats_sold += count
                revenue += amount
        per_category.append(
            CategorySummary(
                id=category.id,
                name=category.name,
                currentPrice=money(category.price),
                capacity=seats,
                seatsSold=seats_sold,
                revenue=money(revenue),
                waiting=waiting_by_category.get(category.id, 0),
            )
        )

    per_show: list[ShowSummary] = []
    for show in shows:
        seats = seats_sold = 0
        revenue = ZERO
        for category in categories:
            cell = (show.id, category.id)
            seats += capacity_by_cell.get(cell, 0)
            if cell in sold_by_cell:
                count, amount = sold_by_cell[cell]
                seats_sold += count
                revenue += amount
        counts = {status: n for sid, status, n in booking_rows if sid == show.id}
        per_show.append(
            ShowSummary(
                id=show.id,
                startsAt=iso(show.starts_at) or "",
                capacity=seats,
                seatsSold=seats_sold,
                revenue=money(revenue),
                bookings=counts.get(BookingStatus.CONFIRMED, 0),
                cancelled=counts.get(BookingStatus.CANCELLED, 0),
            )
        )

    total_revenue = sum((Decimal(s.revenue) for s in per_show), ZERO)
    total_capacity = sum(s.capacity for s in per_show)
    total_sold = sum(s.seatsSold for s in per_show)

    return EventSummary(
        event=EventRef(id=event.id, title=event.title, type=event.type, venue=venue.name),
        totals=Totals(
            revenue=money(total_revenue),
            capacity=total_capacity,
            seatsSold=total_sold,
            # Guarded: an event with no shows yet has no capacity, and x/0 is
            # not a number a dashboard should ever render.
            percentSold=0 if total_capacity == 0 else round(total_sold / total_capacity * 100),
            bookings=sum(s.bookings for s in per_show),
            cancelled=sum(s.cancelled for s in per_show),
            waiting=sum(c.waiting for c in per_category),
        ),
        categories=per_category,
        shows=per_show,
    )
