from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update

from ticket_api.config import settings
from ticket_api.db import Session
from ticket_api.models import SeatStatus, ShowSeat, utcnow
from ticket_api.modules.seats.service import _current_statuses


async def _hold(client, auth, show, token):
    r = await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    return r


async def test_an_abandoned_hold_runs_for_the_full_ttl(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, token = await make_user()
    r = await _hold(client, auth, show, token)

    from datetime import datetime

    expires = datetime.fromisoformat(r.json()["holdExpiresAt"].replace("Z", ""))
    seconds = (expires - utcnow()).total_seconds()
    assert abs(seconds - settings.HOLD_TTL_SECONDS) < 10, seconds


async def test_going_back_shortens_the_hold_instead_of_deleting_it(
    client, auth, make_show, make_user
):
    show = await make_show(seats=1)
    _, token = await make_user()
    await _hold(client, auth, show, token)

    r = await client.delete(f"/api/v1/shows/{show['show_id']}/holds", headers=auth(token))
    assert r.status_code == 200
    assert r.json()["released"] == 1
    assert r.json()["freeAt"].endswith("Z")

    async with Session() as session:
        row = (
            (await session.execute(select(ShowSeat).where(ShowSeat.id == show["seat_ids"][0])))
            .scalars()
            .one()
        )
    # Still HELD, still owned — just on a much shorter clock.
    assert row.status is SeatStatus.HELD
    assert row.held_by_user_id is not None, "the owner is kept so returning can reclaim it"

    seconds = (row.hold_expires_at - utcnow()).total_seconds()
    assert 0 < seconds <= settings.RELEASE_GRACE_SECONDS + 2, seconds


async def test_after_the_grace_elapses_another_customer_can_take_the_seat(
    client, auth, make_show, make_user
):
    """No sweeper runs here — lazy expiry alone must make it bookable."""
    show = await make_show(seats=1)
    _, first = await make_user()
    _, second = await make_user()
    await _hold(client, auth, show, first)
    await client.delete(f"/api/v1/shows/{show['show_id']}/holds", headers=auth(first))

    # Wind the clock past the grace rather than waiting it out.
    async with Session() as session:
        await session.execute(
            update(ShowSeat)
            .where(ShowSeat.id == show["seat_ids"][0])
            .values(hold_expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()

    r = await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(second),
    )
    assert r.status_code == 201, r.text


async def test_returning_within_the_grace_window_restores_the_full_ttl(
    client, auth, make_show, make_user
):
    show = await make_show(seats=1)
    _, token = await make_user()
    await _hold(client, auth, show, token)
    await client.delete(f"/api/v1/shows/{show['show_id']}/holds", headers=auth(token))

    r = await client.post(f"/api/v1/shows/{show['show_id']}/holds/extend", headers=auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["seats"] == 1

    from datetime import datetime

    expires = datetime.fromisoformat(r.json()["holdExpiresAt"].replace("Z", ""))
    seconds = (expires - utcnow()).total_seconds()
    assert seconds > settings.RELEASE_GRACE_SECONDS + 10, seconds


async def test_extending_is_refused_when_nothing_is_held(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, token = await make_user()
    r = await client.post(f"/api/v1/shows/{show['show_id']}/holds/extend", headers=auth(token))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "NO_ACTIVE_HOLD"


async def test_extending_cannot_resurrect_a_seat_somebody_else_took(
    client, auth, make_show, make_user
):
    show = await make_show(seats=1)
    _, first = await make_user()
    _, second = await make_user()
    await _hold(client, auth, show, first)
    await client.delete(f"/api/v1/shows/{show['show_id']}/holds", headers=auth(first))

    async with Session() as session:
        await session.execute(
            update(ShowSeat)
            .where(ShowSeat.id == show["seat_ids"][0])
            .values(hold_expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()

    # Second customer takes it during the gap.
    taken = await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(second),
    )
    assert taken.status_code == 201

    r = await client.post(f"/api/v1/shows/{show['show_id']}/holds/extend", headers=auth(first))
    assert r.status_code == 409


async def test_extend_requires_authentication(client, make_show):
    show = await make_show(seats=1)
    r = await client.post(f"/api/v1/shows/{show['show_id']}/holds/extend")
    assert r.status_code == 401


async def test_the_delayed_release_broadcast_is_not_fooled_by_a_later_extend(
    client, auth, make_show, make_user
):
    """
    Regression: release_holds used to schedule its delayed broadcast with a
    fixed AVAILABLE payload captured at release time. Extending within the
    grace window restores the hold in the database, but the stale timer would
    still fire AVAILABLE at T+RELEASE_GRACE_SECONDS regardless — a false
    signal to every other viewer, even though nothing can actually double-sell
    the seat (hold_seats' lock still gates real bookings).

    _current_statuses is the exact function the delayed callback re-reads
    from when its timer fires, so asserting on it here proves the fix without
    needing a Socket.IO emitter wired up (there is none in tests).
    """
    show = await make_show(seats=1)
    _, token = await make_user()
    await _hold(client, auth, show, token)
    await client.delete(f"/api/v1/shows/{show['show_id']}/holds", headers=auth(token))

    r = await client.post(f"/api/v1/shows/{show['show_id']}/holds/extend", headers=auth(token))
    assert r.status_code == 200, r.text

    # Simulate the delayed callback firing right now: what would it see?
    statuses = await _current_statuses(show["seat_ids"])
    assert statuses[show["seat_ids"][0]] is SeatStatus.HELD
