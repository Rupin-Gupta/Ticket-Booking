"""
Demo data.

  python -m ticket_api.seed

Organiser and admin accounts exist only here — nothing in the API lets a client
choose its own role, which is the point (rule 7).

Idempotent: re-running updates rather than failing on unique constraints, so it
is safe against a database that already has data.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import func, select

from .db import Session, dispose, transaction
from .models import Event, Role, Seat, SeatCategory, Show, User, Venue, utcnow
from .modules.events.service import instantiate_show_seats
from .modules.venues.scheduling import occupied_window
from .security import hash_password

PASSWORD = "password123"

ACCOUNTS = [
    ("admin@ticket.dev", "Ada Admin", Role.ADMIN),
    ("organiser@ticket.dev", "Omar Organiser", Role.ORGANISER),
    ("customer@ticket.dev", "Cara Customer", Role.CUSTOMER),
    ("customer2@ticket.dev", "Cyrus Customer", Role.CUSTOMER),
]

ROW_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def seat_block(venue_id: str, section: str, rows: int, per_row: int, start_y: float) -> list[Seat]:
    """Same grid maths as the venues service, so seeded venues look like built ones."""
    return [
        Seat(
            venue_id=venue_id,
            section=section,
            row=ROW_LABELS[r],
            number=n,
            pos_x=n - (per_row + 1) / 2,
            pos_y=start_y + r,
        )
        for r in range(rows)
        for n in range(1, per_row + 1)
    ]


def days_from_now(days: int, hour: int) -> datetime:
    d = utcnow() + timedelta(days=days)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0)


async def main() -> None:
    # Hashed once: Argon2 is deliberately slow and four identical passwords do
    # not need four hashes.
    password_hash = hash_password(PASSWORD)

    async with Session() as session:
        for email, name, role in ACCOUNTS:
            user = (
                (await session.execute(select(User).where(User.email == email))).scalars().first()
            )
            if user is None:
                session.add(User(email=email, name=name, role=role, password_hash=password_hash))
            else:
                user.name, user.role, user.password_hash = name, role, password_hash
            print(f"  {role.value:<9} {email}")
        await session.commit()

        organiser = (
            (await session.execute(select(User).where(User.email == "organiser@ticket.dev")))
            .scalars()
            .one()
        )

        # --- venue --------------------------------------------------------
        venue = (
            (await session.execute(select(Venue).where(Venue.name == "The Regal")))
            .scalars()
            .first()
        )
        if venue is None:
            venue = Venue(name="The Regal", address="12 Marine Drive, Mumbai")
            session.add(venue)
            await session.flush()

        seat_count = (
            await session.scalar(
                select(func.count()).select_from(Seat).where(Seat.venue_id == venue.id)
            )
            or 0
        )
        if seat_count == 0:
            # Two sections, deliberately different widths — the seat map has to
            # handle rows that are not all the same length.
            seats = [
                *seat_block(venue.id, "Premium", 3, 10, 0),
                *seat_block(venue.id, "Standard", 5, 14, 5),
            ]
            session.add_all(seats)
            await session.flush()
            seat_count = len(seats)
        await session.commit()
        print(f"\n  Venue    {venue.name} — {seat_count} seats across Premium and Standard")

        # --- event + pricing ----------------------------------------------
        event = (
            (
                await session.execute(
                    select(Event).where(
                        Event.title == "Interstellar (re-release)",
                        Event.organiser_id == organiser.id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if event is None:
            event = Event(
                organiser_id=organiser.id,
                venue_id=venue.id,
                title="Interstellar (re-release)",
                type="MOVIE",
                description="Back on the big screen, in 70mm.",
            )
            session.add(event)
            await session.flush()

        for name, price, sections in [
            ("Premium", "450", ["Premium"]),
            ("Standard", "250", ["Standard"]),
        ]:
            category = (
                (
                    await session.execute(
                        select(SeatCategory).where(
                            SeatCategory.event_id == event.id, SeatCategory.name == name
                        )
                    )
                )
                .scalars()
                .first()
            )
            if category is None:
                session.add(
                    SeatCategory(event_id=event.id, name=name, price=price, sections=sections)
                )
            else:
                category.price, category.sections = price, sections
        await session.commit()
        print(f"  Event    {event.title} — Premium 450, Standard 250")

        event_id, venue_id = event.id, venue.id

    # --- shows, each with a full seat map ---------------------------------
    for starts_at in (days_from_now(3, 19), days_from_now(5, 21)):
        async with Session() as session:
            already = (
                (
                    await session.execute(
                        select(Show).where(Show.event_id == event_id, Show.starts_at == starts_at)
                    )
                )
                .scalars()
                .first()
            )
        if already is not None:
            print(f"  Show     {starts_at.isoformat()} (already seeded)")
            continue

        # Same transaction as createShow: a show whose seats failed to generate
        # is worse than no show at all.
        async with transaction() as session:
            ends_at, occupies_until = occupied_window(
                starts_at=starts_at,
                duration_minutes=169,  # Interstellar's actual runtime
                turnaround_minutes=15,
            )
            show = Show(
                event_id=event_id,
                venue_id=venue_id,
                starts_at=starts_at,
                duration_minutes=169,
                ends_at=ends_at,
                occupies_until=occupies_until,
            )
            session.add(show)
            await session.flush()
            count = await instantiate_show_seats(
                session, show_id=show.id, event_id=event_id, venue_id=venue_id
            )
        print(f"  Show     {starts_at.isoformat()} — {count} seats")

    print(f"\n  Every account's password is: {PASSWORD}\n")
    await dispose()


if __name__ == "__main__":
    asyncio.run(main())
