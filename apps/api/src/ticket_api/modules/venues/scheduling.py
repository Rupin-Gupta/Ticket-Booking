"""
Venue availability.

The window a show occupies is longer than the show: the room has to empty, be
cleaned, and be reset before anybody else can use it. Turnaround is a venue
property because a stadium needs longer than a screening room.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...errors import ApiError


def occupied_window(
    *, starts_at: datetime, duration_minutes: int, turnaround_minutes: int
) -> tuple[datetime, datetime]:
    """Returns (ends_at, occupies_until)."""
    ends_at = starts_at + timedelta(minutes=duration_minutes)
    return ends_at, ends_at + timedelta(minutes=turnaround_minutes)


# Locks the venue's scheduled shows before checking, so two simultaneous
# organisers serialise here rather than both passing the check and racing to
# insert.
_CLASHING_SHOWS = text(
    """
    SELECT id, "startsAt", "occupiesUntil"
    FROM "Show"
    WHERE "venueId" = :venue_id
      AND status = 'SCHEDULED'
      AND "startsAt" < :occupies_until
      AND "occupiesUntil" > :starts_at
    ORDER BY "startsAt"
    FOR UPDATE
    """
)


async def assert_venue_free(
    session: AsyncSession,
    *,
    venue_id: str,
    starts_at: datetime,
    occupies_until: datetime,
) -> None:
    """
    Refuses to schedule a show that overlaps another in the same venue.

    Runs inside the caller's transaction. The exclusion constraint underneath is
    the real guarantee; this exists to turn a database error into a message that
    names the clashing show and says when the room actually frees.
    """
    clash = (
        (
            await session.execute(
                _CLASHING_SHOWS,
                {
                    "venue_id": venue_id,
                    "starts_at": starts_at,
                    "occupies_until": occupies_until,
                },
            )
        )
        .mappings()
        .first()
    )
    if clash is not None:
        raise ApiError.conflict(
            "VENUE_DOUBLE_BOOKED",
            f"This venue is already booked from {clash['startsAt'].isoformat()} "
            f"until {clash['occupiesUntil'].isoformat()}, including turnaround.",
        )
