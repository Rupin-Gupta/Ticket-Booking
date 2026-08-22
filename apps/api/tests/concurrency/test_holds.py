"""
The race this project is graded on.

Runs against a REAL uvicorn listener over TCP, not httpx's in-process ASGI
transport. That distinction is the entire point: an in-process transport can
serialise every request through a single task, so a race run against it would
pass even if the lock did nothing.

Every change touching holds or bookings must keep this green.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import select

from ticket_api.db import Session
from ticket_api.models import SeatStatus, ShowSeat

CONTENDERS = 20


async def test_one_seat_twenty_simultaneous_customers(live_server, make_show, make_user, auth):
    show = await make_show(seats=1)
    seat_id = show["seat_ids"][0]

    contenders = [await make_user() for _ in range(CONTENDERS)]

    async with httpx.AsyncClient(base_url=live_server, timeout=60.0) as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    f"/api/v1/shows/{show['show_id']}/holds",
                    json={"seatIds": [seat_id]},
                    headers=auth(token),
                )
                for _, token in contenders
            ),
            return_exceptions=True,
        )

    codes = [r.status_code if isinstance(r, httpx.Response) else 0 for r in responses]

    assert codes.count(201) == 1, f"expected exactly one winner, got {codes.count(201)}: {codes}"
    assert codes.count(409) == CONTENDERS - 1, f"expected clean refusals, got {codes}"
    assert not [c for c in codes if c == 0 or c >= 500], f"nothing should error: {codes}"

    # The HTTP codes could be right while the database is wrong.
    async with Session() as session:
        row = (
            (await session.execute(select(ShowSeat).where(ShowSeat.id == seat_id))).scalars().one()
        )
    assert row.status is SeatStatus.HELD
    assert row.held_by_user_id is not None
    assert row.hold_expires_at is not None


async def test_losers_are_told_which_seat_went(live_server, make_show, make_user, auth):
    show = await make_show(seats=1)
    a, b = await make_user(), await make_user()

    async with httpx.AsyncClient(base_url=live_server, timeout=60.0) as client:
        first, second = await asyncio.gather(
            client.post(
                f"/api/v1/shows/{show['show_id']}/holds",
                json={"seatIds": show["seat_ids"]},
                headers=auth(a[1]),
            ),
            client.post(
                f"/api/v1/shows/{show['show_id']}/holds",
                json={"seatIds": show["seat_ids"]},
                headers=auth(b[1]),
            ),
        )

    loser = first if first.status_code == 409 else second
    assert loser.status_code == 409
    body = loser.json()["error"]
    assert body["code"] == "SEAT_UNAVAILABLE"
    # Naming the seat is the difference between a useful message and "conflict".
    assert "A1" in body["message"], body["message"]


async def test_two_customers_opposite_seat_order_do_not_deadlock(
    live_server, make_show, make_user, auth
):
    """
    Without ORDER BY in the locking query, {A,B} and {B,A} deadlock — and
    Postgres resolves a deadlock by killing a transaction, turning a clean 409
    into a 500. This asserts on the absence of 500s specifically.
    """
    show = await make_show(seats=2)
    forward = show["seat_ids"]
    backward = list(reversed(forward))
    users = [await make_user() for _ in range(8)]

    async with httpx.AsyncClient(base_url=live_server, timeout=60.0) as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    f"/api/v1/shows/{show['show_id']}/holds",
                    json={"seatIds": forward if i % 2 == 0 else backward},
                    headers=auth(token),
                )
                for i, (_, token) in enumerate(users)
            ),
            return_exceptions=True,
        )

    codes = [r.status_code if isinstance(r, httpx.Response) else 0 for r in responses]
    assert not [c for c in codes if c == 0 or c >= 500], f"deadlock surfaced as 5xx: {codes}"
    assert codes.count(201) == 1, codes


@pytest.mark.parametrize("seats", [1, 3])
async def test_hold_response_shape(live_server, make_show, make_user, auth, seats):
    show = await make_show(seats=seats)
    _, token = await make_user()

    async with httpx.AsyncClient(base_url=live_server, timeout=60.0) as client:
        r = await client.post(
            f"/api/v1/shows/{show['show_id']}/holds",
            json={"seatIds": show["seat_ids"]},
            headers=auth(token),
        )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["showId"] == show["show_id"]
    assert sorted(body["seatIds"]) == sorted(show["seat_ids"])
    # The browser parses this with `new Date(...)`; without the Z it would read
    # a naive timestamp as local time and the countdown would be hours out.
    assert body["holdExpiresAt"].endswith("Z"), body["holdExpiresAt"]
