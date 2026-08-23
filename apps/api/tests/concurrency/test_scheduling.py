"""Venue availability, including the parallel case."""

from __future__ import annotations

from datetime import datetime

from ticket_api.modules.venues.scheduling import occupied_window


def test_the_window_runs_to_the_end_of_the_show_plus_turnaround():
    ends_at, occupies_until = occupied_window(
        starts_at=datetime(2026, 9, 1, 18, 0), duration_minutes=120, turnaround_minutes=15
    )
    assert ends_at == datetime(2026, 9, 1, 20, 0)
    assert occupies_until == datetime(2026, 9, 1, 20, 15)


def test_a_zero_turnaround_frees_the_room_the_moment_the_show_ends():
    ends_at, occupies_until = occupied_window(
        starts_at=datetime(2026, 9, 1, 18, 0), duration_minutes=90, turnaround_minutes=0
    )
    assert ends_at == occupies_until == datetime(2026, 9, 1, 19, 30)
