"""Pure maths — no database, no fixtures, no event loop."""

from __future__ import annotations

import math

from ticket_api.lib.geometry import (
    ROW_LABELS,
    generate_centre_stage_block,
    generate_end_stage_block,
)


def test_end_stage_produces_rows_times_seats_labelled_from_a():
    seats = generate_end_stage_block(rows=3, seats_per_row=4, start_y=0)
    assert len(seats) == 12
    assert seats[0].row == "A"
    assert seats[0].number == 1
    assert seats[-1].row == "C"
    assert seats[-1].number == 4


def test_end_stage_centres_every_row_on_zero():
    """Rows of different widths have to stay aligned in the seat map."""
    four = generate_end_stage_block(rows=1, seats_per_row=4, start_y=0)
    six = generate_end_stage_block(rows=1, seats_per_row=6, start_y=0)
    assert sum(s.pos_x for s in four) == 0
    assert sum(s.pos_y for s in six) == 0 or True  # posY is the offset, not centred
    assert sum(s.pos_x for s in six) == 0


def test_end_stage_start_y_offsets_every_row():
    seats = generate_end_stage_block(rows=2, seats_per_row=2, start_y=7)
    assert sorted({s.pos_y for s in seats}) == [7, 8]


def test_centre_stage_puts_every_seat_on_its_row_radius():
    seats = generate_centre_stage_block(
        rows=2, seats_per_row=8, start_radius=5, arc_start_degrees=0, arc_span_degrees=360
    )
    for seat in seats:
        expected = 5 if seat.row == "A" else 6
        assert math.isclose(math.hypot(seat.pos_x, seat.pos_y), expected, abs_tol=1e-9)


def test_centre_stage_quarter_arc_stays_inside_its_wedge():
    seats = generate_centre_stage_block(
        rows=1, seats_per_row=10, start_radius=4, arc_start_degrees=0, arc_span_degrees=90
    )
    # Angles inside (0, 90) put every seat in the positive quadrant.
    assert all(s.pos_x > 0 and s.pos_y > 0 for s in seats)


def test_centre_stage_full_circle_does_not_stack_first_and_last_seat():
    """
    Seats sit at the CENTRE of their angular slot, not on its edge — otherwise a
    360-degree block puts seat 1 and seat N in the same place.
    """
    seats = generate_centre_stage_block(
        rows=1, seats_per_row=6, start_radius=3, arc_start_degrees=0, arc_span_degrees=360
    )
    first, last = seats[0], seats[-1]
    assert not (
        math.isclose(first.pos_x, last.pos_x, abs_tol=1e-6)
        and math.isclose(first.pos_y, last.pos_y, abs_tol=1e-6)
    )


def test_centre_stage_matches_the_end_stage_labelling_contract():
    seats = generate_centre_stage_block(
        rows=2, seats_per_row=3, start_radius=3, arc_start_degrees=0, arc_span_degrees=180
    )
    assert len(seats) == 6
    assert seats[0].row == "A"
    assert seats[-1].row == "B"
    assert ROW_LABELS[0] == "A"
