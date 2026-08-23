"""
Venue availability.

The window a show occupies is longer than the show: the room has to empty, be
cleaned, and be reset before anybody else can use it. Turnaround is a venue
property because a stadium needs longer than a screening room.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def occupied_window(
    *, starts_at: datetime, duration_minutes: int, turnaround_minutes: int
) -> tuple[datetime, datetime]:
    """Returns (ends_at, occupies_until)."""
    ends_at = starts_at + timedelta(minutes=duration_minutes)
    return ends_at, ends_at + timedelta(minutes=turnaround_minutes)
