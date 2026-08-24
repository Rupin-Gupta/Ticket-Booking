from __future__ import annotations

import pytest
from sqlalchemy import select, update

from ticket_api.db import Session
from ticket_api.models import OfferLog, Role
from ticket_api.modules.waitlist import fairness

SHOWS = "/api/v1/shows"
WAITLIST = "/api/v1/waitlist"


@pytest.fixture
async def admin(make_user):
    return await make_user(Role.ADMIN, "admin")


async def _sold_out_with_queue(client, auth, make_show, make_user, waiters=1):
    """One seat, sold, with `waiters` customers queued behind it."""
    show = await make_show(seats=1)
    _, buyer = await make_user(Role.CUSTOMER, "buyer")
    seat = show["seat_ids"][0]

    await client.post(
        f"{SHOWS}/{show['show_id']}/holds", json={"seatIds": [seat]}, headers=auth(buyer)
    )
    booking = (
        await client.post(
            "/api/v1/bookings",
            json={"showId": show["show_id"], "seatIds": [seat]},
            headers=auth(buyer),
        )
    ).json()["booking"]

    joins = []
    for i in range(waiters):
        _, waiter = await make_user(Role.CUSTOMER, f"waiter{i}")
        r = await client.post(
            f"{SHOWS}/{show['show_id']}/waitlist",
            json={"categoryId": show["category_id"]},
            headers=auth(waiter),
        )
        assert r.status_code == 201, r.text
        joins.append((waiter, r.json()))

    return show, buyer, booking, joins


# ------------------------------------------------------------- receipts


async def test_joining_hands_back_a_signed_receipt(client, auth, make_show, make_user):
    _, _, _, joins = await _sold_out_with_queue(client, auth, make_show, make_user)
    _, joined = joins[0]

    receipt = joined["receipt"]
    assert receipt["payload"]["position"] == joined["position"]
    assert receipt["payload"]["entryId"] == joined["id"]
    assert len(receipt["signature"]) == 64

    check = await client.post(f"{WAITLIST}/receipt/verify", json=receipt)
    assert check.status_code == 200, check.text
    assert check.json()["valid"] is True


async def test_a_receipt_with_an_improved_position_does_not_verify(
    client, auth, make_show, make_user
):
    """
    The point of signing it: neither side can quietly change the facts that
    decide the queue.
    """
    # Two waiters, so the second one has a position worth forging. With one
    # waiter, "improving" position 1 to position 1 changes nothing and the
    # signature legitimately still matches.
    _, _, _, joins = await _sold_out_with_queue(client, auth, make_show, make_user, waiters=2)
    receipt = joins[1][1]["receipt"]
    assert receipt["payload"]["position"] == 2

    forged = {**receipt, "payload": {**receipt["payload"], "position": 1}}

    check = await client.post(f"{WAITLIST}/receipt/verify", json=forged)
    assert check.json()["valid"] is False


async def test_a_receipt_from_a_different_secret_does_not_verify():
    payload = fairness.receipt_payload(
        entry_id="e", show_id="s", category_id="c", joined_at=None, position=3
    )
    assert fairness.verify(payload, "0" * 64) is False


# ------------------------------------------------------- the offer chain


async def test_an_offer_appends_a_link_to_the_chain(client, auth, make_show, make_user):
    show, buyer, booking, _ = await _sold_out_with_queue(client, auth, make_show, make_user)

    # Cancelling frees the seat, which offers it to the person waiting.
    await client.post(f"/api/v1/bookings/{booking['id']}/cancel", headers=auth(buyer))

    log = await client.get(f"{WAITLIST}/log/{show['show_id']}")
    assert log.status_code == 200, log.text
    body = log.json()

    assert len(body["rows"]) == 1
    assert body["rows"][0]["seq"] == 1
    assert body["rows"][0]["prevHash"] == fairness.GENESIS
    assert body["intact"] is True


async def test_the_log_is_public_and_names_no_customer(client, auth, make_show, make_user):
    show, buyer, booking, _ = await _sold_out_with_queue(client, auth, make_show, make_user)
    await client.post(f"/api/v1/bookings/{booking['id']}/cancel", headers=auth(buyer))

    # No Authorization header at all.
    log = await client.get(f"{WAITLIST}/log/{show['show_id']}")
    assert log.status_code == 200

    body = str(log.json())
    assert "customer" not in body.lower(), "the public log leaked who is waiting"
    assert "@" not in body, "the public log leaked an email address"


async def test_tampering_with_an_earlier_link_is_detected(client, auth, make_show, make_user):
    """
    The whole reason for chaining. Rewriting a row breaks every hash after it,
    so a quiet re-ordering leaves evidence anyone can find.
    """
    show, buyer, booking, _ = await _sold_out_with_queue(client, auth, make_show, make_user)
    await client.post(f"/api/v1/bookings/{booking['id']}/cancel", headers=auth(buyer))

    before = (await client.get(f"{WAITLIST}/log/{show['show_id']}")).json()
    assert before["intact"] is True

    # Somebody edits the database directly to claim a different seat was offered.
    async with Session() as session:
        await session.execute(
            update(OfferLog)
            .where(OfferLog.show_id == show["show_id"])
            .values(show_seat_id="a-seat-that-was-never-offered")
        )
        await session.commit()

    after = (await client.get(f"{WAITLIST}/log/{show['show_id']}")).json()
    assert after["intact"] is False
    assert after["brokenAt"] == 1


async def test_a_customer_can_replay_the_chain_without_the_secret(
    client, auth, make_show, make_user
):
    """
    Verification that needs a secret is verification only the operator can do.
    """
    show, buyer, booking, _ = await _sold_out_with_queue(client, auth, make_show, make_user)
    await client.post(f"/api/v1/bookings/{booking['id']}/cancel", headers=auth(buyer))

    async with Session() as session:
        rows = list(
            (await session.execute(select(OfferLog).where(OfferLog.show_id == show["show_id"])))
            .scalars()
            .all()
        )

    intact, broken = fairness.replay(rows)
    assert intact is True
    assert broken is None
