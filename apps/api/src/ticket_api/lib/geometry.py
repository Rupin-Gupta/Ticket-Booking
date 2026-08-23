"""
Seat coordinate generation for both stage layouts.

Pure functions, no I/O — coordinates are the one part of venue building that can
be tested without a database, and a round trip per assertion would make those
tests slow for nothing.

pos_x / pos_y are grid units, not pixels. The frontend decides how big a seat is,
which is why a radial layout needs no renderer change: it writes the same two
numbers, just arranged in a circle.
"""

from __future__ import annotations

import math
from typing import NamedTuple

ROW_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class SeatPosition(NamedTuple):
    row: str
    number: int
    pos_x: float
    pos_y: float


def generate_end_stage_block(
    *, rows: int, seats_per_row: int, start_y: float
) -> list[SeatPosition]:
    """A rectangular block. Rows stack downwards, each centred on x = 0."""
    return [
        SeatPosition(
            row=ROW_LABELS[r],
            number=n,
            # Centring on zero keeps rows of different widths aligned.
            pos_x=n - (seats_per_row + 1) / 2,
            pos_y=start_y + r,
        )
        for r in range(rows)
        for n in range(1, seats_per_row + 1)
    ]


def generate_centre_stage_block(
    *,
    rows: int,
    seats_per_row: int,
    start_radius: float,
    arc_start_degrees: float,
    arc_span_degrees: float,
) -> list[SeatPosition]:
    """
    A block arranged around a central stage.

    Rows become radii and seats spread along an arc. Seats sit at the *centre* of
    their angular slot rather than on its edge, so a full 360-degree block does
    not put the first and last seat on top of each other.
    """
    seats: list[SeatPosition] = []
    for r in range(rows):
        radius = start_radius + r
        for n in range(1, seats_per_row + 1):
            degrees = arc_start_degrees + (arc_span_degrees * (n - 0.5)) / seats_per_row
            radians = math.radians(degrees)
            seats.append(
                SeatPosition(
                    row=ROW_LABELS[r],
                    number=n,
                    pos_x=radius * math.cos(radians),
                    pos_y=radius * math.sin(radians),
                )
            )
    return seats
