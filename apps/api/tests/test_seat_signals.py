from __future__ import annotations

import pytest
from sqlalchemy import func, select

from ticket_api.db import Session
from ticket_api.models import Role, SeatEvent, SeatEventKind
from ticket_api.modules.seats.service import sweep_expired_holds

SHOWS = "/api/v1/shows"


@pytest.fixture
async def admin(make_user):
    return await make_user(Role.ADMIN, "admin")


async def _count(kind: SeatEventKind) -> int:
    async with Session() as session:
        return int(
            await session.scalar(select(func.count(SeatEvent.id)).where(SeatEvent.kind == kind))
            or 0
        )


# ------------------------------------------------------------- capture


async def test_a_hold_and_a_release_each_write_one_event(client, auth, make_show, make_user):
    show = await make_show(seats=2)
    _, customer = await make_user(Role.CUSTOMER, "customer")
    seat = show["seat_ids"][0]

    await client.post(
        f"{SHOWS}/{show['show_id']}/holds", json={"seatIds": [seat]}, headers=auth(customer)
    )
    assert await _count(SeatEventKind.HELD) == 1

    await client.delete(f"{SHOWS}/{show['show_id']}/holds", headers=auth(customer))
    assert await _count(SeatEventKind.RELEASED) == 1


async def test_a_booking_writes_a_booked_event(client, auth, make_show, make_user):
    show = await make_show(seats=2)
    _, customer = await make_user(Role.CUSTOMER, "customer")
    seat = show["seat_ids"][0]

    await client.post(
        f"{SHOWS}/{show['show_id']}/holds", json={"seatIds": [seat]}, headers=auth(customer)
    )
    r = await client.post(
        "/api/v1/bookings",
        json={"showId": show["show_id"], "seatIds": [seat]},
        headers=auth(customer),
    )
    assert r.status_code == 201, r.text
    assert await _count(SeatEventKind.BOOKED) == 1


async def test_a_rejected_hold_writes_nothing(client, auth, make_show, make_user):
    """
    Events are written after the transaction commits. A hold that never
    committed must leave no trace, or the signal counts attempts rather than
    outcomes.
    """
    show = await make_show(seats=1)
    _, first = await make_user(Role.CUSTOMER, "first")
    _, second = await make_user(Role.CUSTOMER, "second")
    seat = show["seat_ids"][0]

    await client.post(
        f"{SHOWS}/{show['show_id']}/holds", json={"seatIds": [seat]}, headers=auth(first)
    )
    before = await _count(SeatEventKind.HELD)

    losing = await client.post(
        f"{SHOWS}/{show['show_id']}/holds", json={"seatIds": [seat]}, headers=auth(second)
    )
    assert losing.status_code == 409

    assert await _count(SeatEventKind.HELD) == before, "a refused hold recorded an outcome"


async def test_the_sweeper_records_expiries(client, auth, make_show, make_user, monkeypatch):
    from ticket_api import config

    show = await make_show(seats=2)
    _, customer = await make_user(Role.CUSTOMER, "customer")
    monkeypatch.setattr(config.settings, "HOLD_TTL_SECONDS", -1)

    await client.post(
        f"{SHOWS}/{show['show_id']}/holds",
        json={"seatIds": [show["seat_ids"][0]]},
        headers=auth(customer),
    )
    await sweep_expired_holds()

    assert await _count(SeatEventKind.EXPIRED) == 1


# --------------------------------------------------------- aggregation


async def test_below_the_sample_threshold_no_signal_is_offered(client, auth, make_show, make_user):
    """One abandonment is not "100% rejected"."""
    from ticket_api.modules.signals.service import hesitation_by_seat

    show = await make_show(seats=2)
    _, customer = await make_user(Role.CUSTOMER, "customer")
    seat = show["seat_ids"][0]

    for _ in range(2):
        await client.post(
            f"{SHOWS}/{show['show_id']}/holds", json={"seatIds": [seat]}, headers=auth(customer)
        )
        await client.delete(f"{SHOWS}/{show['show_id']}/holds", headers=auth(customer))

    assert await hesitation_by_seat(show["venue_id"]) == {}


async def test_hesitation_is_absent_from_the_map_unless_the_organiser_publishes(
    client, auth, make_show, make_user
):
    show = await make_show(seats=2)
    _, customer = await make_user(Role.CUSTOMER, "customer")
    seat, peer = show["seat_ids"][0], show["seat_ids"][1]

    # Six outcomes on one seat, all of them "put it back": past the threshold.
    for _ in range(6):
        await client.post(
            f"{SHOWS}/{show['show_id']}/holds", json={"seatIds": [seat]}, headers=auth(customer)
        )
        await client.delete(f"{SHOWS}/{show['show_id']}/holds", headers=auth(customer))

    # A neighbour in the same row that sells, so the row has a baseline. Without
    # a peer there is no comparison to make and nothing should be claimed.
    for _ in range(3):
        await client.post(
            f"{SHOWS}/{show['show_id']}/holds", json={"seatIds": [peer]}, headers=auth(customer)
        )
        booked = await client.post(
            "/api/v1/bookings",
            json={"showId": show["show_id"], "seatIds": [peer]},
            headers=auth(customer),
        )
        await client.post(
            f"/api/v1/bookings/{booked.json()['booking']['id']}/cancel", headers=auth(customer)
        )

    off = (await client.get(f"{SHOWS}/{show['show_id']}/seats")).json()["seats"]
    assert all(s["hesitation"] is None for s in off), "signals leaked before being published"

    patched = await client.patch(
        f"/api/v1/events/{show['event_id']}",
        json={"publishSeatSignals": True},
        headers=auth(show["organiser_token"]),
    )
    assert patched.status_code == 200, patched.text

    on = (await client.get(f"{SHOWS}/{show['show_id']}/seats")).json()["seats"]
    signalled = [s for s in on if s["hesitation"] is not None]
    assert signalled, "publishing changed nothing"
    assert signalled[0]["hesitation"]["sample"] >= 5
    assert signalled[0]["hesitation"]["ratio"] > 0


async def test_the_organiser_sees_signals_even_while_unpublished(
    client, auth, make_show, make_user
):
    """
    The toggle governs what CUSTOMERS see. The person selling the seats may
    always look at their own inventory.
    """
    show = await make_show(seats=2)
    _, customer = await make_user(Role.CUSTOMER, "customer")
    seat = show["seat_ids"][0]

    for _ in range(6):
        await client.post(
            f"{SHOWS}/{show['show_id']}/holds", json={"seatIds": [seat]}, headers=auth(customer)
        )
        await client.delete(f"{SHOWS}/{show['show_id']}/holds", headers=auth(customer))

    summary = await client.get(
        f"/api/v1/organiser/events/{show['event_id']}/summary",
        headers=auth(show["organiser_token"]),
    )
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["publishSeatSignals"] is False
    assert len(body["seatSignals"]) >= 1
    assert body["seatSignals"][0]["sample"] >= 5


# ------------------------------------------------------ the regression guard


async def test_the_twenty_way_race_still_has_exactly_one_winner(client, auth, admin, make_show):
    """
    The whole design rests on capture never touching the hold transaction. If
    signals ever move inside the lock, this is what fails.
    """
    show = await make_show(seats=2)

    r = await client.post(
        "/api/v1/lab/race",
        json={"showId": show["show_id"], "seatId": show["seat_ids"][0], "attempts": 20},
        headers=auth(admin[1]),
    )

    race = r.json()["race"]
    assert race["outcome"]["won"] == 1
    assert race["outcome"]["rejected"] == 19
    assert race["outcome"]["errors"] == 0
