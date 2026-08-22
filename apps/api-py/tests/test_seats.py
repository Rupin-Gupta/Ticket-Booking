from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from ticket_api.config import settings
from ticket_api.db import Session
from ticket_api.models import SeatStatus, ShowSeat, utcnow
from ticket_api.modules.seats.service import sweep_expired_holds


async def test_seat_map_is_public(client, make_show):
    show = await make_show(seats=3)
    r = await client.get(f"/api/v1/shows/{show['show_id']}/seats")
    assert r.status_code == 200
    assert len(r.json()["seats"]) == 3
    assert {s["status"] for s in r.json()["seats"]} == {"AVAILABLE"}


async def test_seat_map_404s_for_an_unknown_show(client):
    r = await client.get("/api/v1/shows/nope/seats")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "SHOW_NOT_FOUND"


async def test_seat_map_never_exposes_who_holds_a_seat(client, auth, make_show, make_user):
    """RULE 8 — showing *that* a seat is held is the product; *who* is not."""
    show = await make_show(seats=2)
    _, holder = await make_user()
    _, onlooker = await make_user()

    await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": [show["seat_ids"][0]]},
        headers=auth(holder),
    )

    r = await client.get(f"/api/v1/shows/{show['show_id']}/seats", headers=auth(onlooker))
    assert "heldByUserId" not in r.text

    held = next(s for s in r.json()["seats"] if s["id"] == show["seat_ids"][0])
    assert held["status"] == "HELD"
    assert held["heldByMe"] is False
    # The countdown is the holder's business alone.
    assert held["holdExpiresAt"] is None


async def test_the_holder_sees_their_own_countdown(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, holder = await make_user()
    await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(holder),
    )

    seat = (
        await client.get(f"/api/v1/shows/{show['show_id']}/seats", headers=auth(holder))
    ).json()["seats"][0]
    assert seat["heldByMe"] is True
    assert seat["holdExpiresAt"].endswith("Z")


async def test_anonymous_viewer_owns_nothing(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, holder = await make_user()
    await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(holder),
    )
    seats = (await client.get(f"/api/v1/shows/{show['show_id']}/seats")).json()["seats"]
    assert [s for s in seats if s["heldByMe"]] == []


async def test_an_expired_hold_is_free_without_any_sweeper(client, auth, make_show, make_user):
    """
    Lazy expiry is the correctness guarantee; the sweeper is only visibility.
    Nothing here runs the sweeper.
    """
    show = await make_show(seats=1)
    _, first = await make_user()
    _, second = await make_user()

    await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(first),
    )

    async with Session() as session:
        await session.execute(
            update(ShowSeat)
            .where(ShowSeat.id == show["seat_ids"][0])
            .values(hold_expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()

    # Reads it as free...
    seats = (await client.get(f"/api/v1/shows/{show['show_id']}/seats")).json()["seats"]
    assert seats[0]["status"] == "AVAILABLE"

    # ...and so does the hold transaction.
    r = await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(second),
    )
    assert r.status_code == 201, r.text


async def test_sweeper_frees_expired_holds(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, holder = await make_user()
    await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(holder),
    )

    async with Session() as session:
        await session.execute(
            update(ShowSeat)
            .where(ShowSeat.id == show["seat_ids"][0])
            .values(hold_expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()

    assert await sweep_expired_holds() == 1

    async with Session() as session:
        row = (
            (await session.execute(select(ShowSeat).where(ShowSeat.id == show["seat_ids"][0])))
            .scalars()
            .one()
        )
    assert row.status is SeatStatus.AVAILABLE
    assert row.held_by_user_id is None
    assert row.hold_expires_at is None


async def test_sweeper_is_idempotent(make_show):
    await make_show(seats=1)
    assert await sweep_expired_holds() == 0
    assert await sweep_expired_holds() == 0


async def test_release_frees_only_your_own_seats(client, auth, make_show, make_user):
    show = await make_show(seats=2)
    _, mine = await make_user()
    _, theirs = await make_user()

    await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": [show["seat_ids"][0]]},
        headers=auth(mine),
    )
    await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": [show["seat_ids"][1]]},
        headers=auth(theirs),
    )

    r = await client.delete(f"/api/v1/shows/{show['show_id']}/holds", headers=auth(mine))
    assert r.json()["released"] == 1

    seats = {
        s["id"]: s
        for s in (await client.get(f"/api/v1/shows/{show['show_id']}/seats")).json()["seats"]
    }
    assert seats[show["seat_ids"][0]]["status"] == "AVAILABLE"
    assert seats[show["seat_ids"][1]]["status"] == "HELD"


async def test_releasing_nothing_is_not_an_error(client, auth, make_show, make_user):
    show = await make_show()
    _, token = await make_user()
    r = await client.delete(f"/api/v1/shows/{show['show_id']}/holds", headers=auth(token))
    assert r.status_code == 200
    assert r.json()["released"] == 0


async def test_holds_require_authentication(client, make_show):
    show = await make_show()
    r = await client.post(
        f"/api/v1/shows/{show['show_id']}/holds", json={"seatIds": show["seat_ids"]}
    )
    assert r.status_code == 401


async def test_a_seat_from_another_show_is_refused(client, auth, make_show, make_user):
    a, b = await make_show(seats=1), await make_show(seats=1)
    _, token = await make_user()

    r = await client.post(
        f"/api/v1/shows/{a['show_id']}/holds",
        json={"seatIds": b["seat_ids"]},
        headers=auth(token),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "SEAT_NOT_FOUND"


async def test_duplicate_seat_ids_are_rejected(client, auth, make_show, make_user):
    """
    A repeated id would break `len(rows) == len(seat_ids)`, which is what proves
    every requested seat was found and locked.
    """
    show = await make_show(seats=1)
    _, token = await make_user()
    r = await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"] * 2},
        headers=auth(token),
    )
    assert r.status_code == 400


async def test_requesting_more_seats_than_the_cap_is_rejected(client, auth, make_show, make_user):
    show = await make_show(seats=settings.MAX_SEATS_PER_HOLD + 1)
    _, token = await make_user()
    r = await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(token),
    )
    assert r.status_code == 400


async def test_concurrent_hold_cap_counts_shows_not_seats(client, auth, make_show, make_user):
    """
    Holding six seats for one film is a family; holding one seat across twenty
    shows is denial of service.
    """
    _, token = await make_user()
    shows = [await make_show(seats=1) for _ in range(settings.MAX_ACTIVE_HOLDS_PER_USER + 1)]

    for show in shows[:-1]:
        r = await client.post(
            f"/api/v1/shows/{show['show_id']}/holds",
            json={"seatIds": show["seat_ids"]},
            headers=auth(token),
        )
        assert r.status_code == 201, r.text

    r = await client.post(
        f"/api/v1/shows/{shows[-1]['show_id']}/holds",
        json={"seatIds": shows[-1]["seat_ids"]},
        headers=auth(token),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "TOO_MANY_ACTIVE_HOLDS"


async def test_my_holds_lists_what_i_am_holding(client, auth, make_show, make_user):
    show = await make_show(seats=2)
    _, token = await make_user()
    await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(token),
    )

    r = await client.get("/api/v1/holds/me", headers=auth(token))
    assert r.status_code == 200
    holds = r.json()["holds"]
    assert len(holds) == 2
    assert {h["label"] for h in holds} == {"A1", "A2"}
    assert holds[0]["price"] == "100"


@pytest.mark.parametrize("body", [{}, {"seatIds": []}, {"seatIds": [""]}])
async def test_hold_validation(client, auth, make_show, make_user, body):
    show = await make_show()
    _, token = await make_user()
    r = await client.post(f"/api/v1/shows/{show['show_id']}/holds", json=body, headers=auth(token))
    assert r.status_code == 400
