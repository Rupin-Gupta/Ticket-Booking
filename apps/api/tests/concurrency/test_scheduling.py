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


import asyncio  # noqa: E402
from datetime import timedelta  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from ticket_api.db import Session  # noqa: E402
from ticket_api.models import Role, Show, ShowStatus, utcnow  # noqa: E402


@pytest.fixture
async def shared_venue(client, auth, make_user, make_show):
    """
    Two organisers, two events, one venue. Each event is fully priced, so show
    creation is never blocked by pricing.
    """
    first = await make_show(seats=2)
    _, other_token = await make_user(Role.ORGANISER, "other")

    second = await client.post(
        "/api/v1/events",
        json={"venueId": first["venue_id"], "title": "Rival", "type": "CONCERT"},
        headers=auth(other_token),
    )
    assert second.status_code == 201, second.text
    second_event = second.json()["event"]["id"]

    priced = await client.post(
        f"/api/v1/events/{second_event}/categories",
        json={"name": "Main", "price": "100", "sections": ["Main"]},
        headers=auth(other_token),
    )
    assert priced.status_code == 201, priced.text

    return {
        "venue_id": first["venue_id"],
        "a_event": first["event_id"],
        "a_token": first["organiser_token"],
        "b_event": second_event,
        "b_token": other_token,
    }


def at(day_offset: int, hour: int) -> str:
    d = utcnow() + timedelta(days=30 + day_offset)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


async def _schedule(client, auth, event, token, starts_at, minutes):
    return await client.post(
        f"/api/v1/events/{event}/shows",
        json={"startsAt": starts_at, "durationMinutes": minutes},
        headers=auth(token),
    )


async def test_a_second_overlapping_show_is_refused(client, auth, shared_venue):
    v = shared_venue
    first = await _schedule(client, auth, v["a_event"], v["a_token"], at(0, 18), 120)
    assert first.status_code == 201, first.text

    # Starts an hour in, while the first show is still running.
    clash = await _schedule(client, auth, v["b_event"], v["b_token"], at(0, 19), 60)
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "VENUE_DOUBLE_BOOKED"


async def test_a_show_starting_inside_the_turnaround_is_refused(client, auth, shared_venue):
    v = shared_venue
    await _schedule(client, auth, v["a_event"], v["a_token"], at(1, 18), 60)
    # Ends 19:00; the default 15-minute turnaround runs to 19:15.
    too_soon = await _schedule(client, auth, v["b_event"], v["b_token"], at(1, 19), 60)
    assert too_soon.status_code == 409


async def test_a_show_starting_after_the_turnaround_is_accepted(client, auth, shared_venue):
    v = shared_venue
    await _schedule(client, auth, v["a_event"], v["a_token"], at(2, 18), 60)
    # Ends 19:00, free from 19:15. 20:00 is clear.
    later = await _schedule(client, auth, v["b_event"], v["b_token"], at(2, 20), 60)
    assert later.status_code == 201, later.text


async def test_the_same_organiser_cannot_double_book_either(client, auth, shared_venue):
    """The constraint is about the room, not about who is asking."""
    v = shared_venue
    await _schedule(client, auth, v["a_event"], v["a_token"], at(3, 18), 120)
    clash = await _schedule(client, auth, v["a_event"], v["a_token"], at(3, 19), 60)
    assert clash.status_code == 409


async def test_cancelling_a_show_frees_its_slot(client, auth, shared_venue):
    """
    The constraint is partial on status, so a cancelled show stops blocking with
    no cleanup code anywhere.
    """
    v = shared_venue
    created = await _schedule(client, auth, v["a_event"], v["a_token"], at(4, 18), 60)
    show_id = created.json()["show"]["id"]

    blocked = await _schedule(client, auth, v["b_event"], v["b_token"], at(4, 18), 60)
    assert blocked.status_code == 409

    async with Session() as session:
        show = (await session.execute(select(Show).where(Show.id == show_id))).scalars().one()
        show.status = ShowStatus.CANCELLED
        await session.commit()

    freed = await _schedule(client, auth, v["b_event"], v["b_token"], at(4, 18), 60)
    assert freed.status_code == 201, freed.text


async def test_the_database_refuses_an_overlap_even_bypassing_the_application(
    client, auth, shared_venue
):
    v = shared_venue
    starts = at(5, 18)
    created = await _schedule(client, auth, v["a_event"], v["a_token"], starts, 60)
    assert created.status_code == 201, created.text

    from datetime import datetime

    slot = datetime.fromisoformat(starts)
    async with Session() as session:
        session.add(
            Show(
                event_id=v["b_event"],
                venue_id=v["venue_id"],
                starts_at=slot,
                duration_minutes=60,
                ends_at=slot + timedelta(minutes=60),
                occupies_until=slot + timedelta(minutes=75),
            )
        )
        with pytest.raises(Exception):  # noqa: B017 - any DB refusal is the point
            await session.commit()


async def test_two_organisers_racing_for_one_slot_exactly_one_wins(
    live_server, client, auth, shared_venue
):
    v = shared_venue
    starts = at(6, 18)

    async with httpx.AsyncClient(base_url=live_server, timeout=60.0) as http:
        a, b = await asyncio.gather(
            http.post(
                f"/api/v1/events/{v['a_event']}/shows",
                json={"startsAt": starts, "durationMinutes": 90},
                headers=auth(v["a_token"]),
            ),
            http.post(
                f"/api/v1/events/{v['b_event']}/shows",
                json={"startsAt": starts, "durationMinutes": 90},
                headers=auth(v["b_token"]),
            ),
        )

    assert sorted([a.status_code, b.status_code]) == [201, 409], (a.text, b.text)

    # Scoped to the contested slot, not the whole venue: shared_venue's own
    # fixture show already occupies a different, non-overlapping time in this
    # venue, so counting every SCHEDULED show here would always be 2.
    async with Session() as session:
        count = len(
            (
                await session.execute(
                    select(Show).where(
                        Show.venue_id == v["venue_id"],
                        Show.status == ShowStatus.SCHEDULED,
                        Show.starts_at == datetime.fromisoformat(starts),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert count == 1, "two shows were scheduled in one venue at one time"
