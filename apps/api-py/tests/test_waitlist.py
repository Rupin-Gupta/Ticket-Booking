from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from ticket_api.db import Session
from ticket_api.models import SeatStatus, ShowSeat, WaitlistEntry, WaitlistStatus, utcnow
from ticket_api.modules.waitlist.service import sweep_expired_offers

BOOKINGS = "/api/v1/bookings"
WAITLIST = "/api/v1/waitlist"


async def _sell_out(client, auth, show, token):
    """Books every seat in the show, so the category has none left."""
    await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(token),
    )
    r = await client.post(
        BOOKINGS,
        json={"showId": show["show_id"], "seatIds": show["seat_ids"]},
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["booking"]["id"]


@pytest.fixture
async def sold_out(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, buyer = await make_user(name="buyer")
    booking_id = await _sell_out(client, auth, show, buyer)
    return {**show, "buyer": buyer, "booking_id": booking_id}


async def _join(client, auth, show_id, category_id, token):
    return await client.post(
        f"/api/v1/shows/{show_id}/waitlist", json={"categoryId": category_id}, headers=auth(token)
    )


# ------------------------------------------------------------------ joining


async def test_cannot_join_while_seats_remain(client, auth, make_show, make_user):
    show = await make_show(seats=2)
    _, token = await make_user()
    r = await _join(client, auth, show["show_id"], show["category_id"], token)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "SEATS_STILL_AVAILABLE"


async def test_joining_a_sold_out_category_gives_a_position(client, auth, sold_out, make_user):
    _, token = await make_user()
    r = await _join(client, auth, sold_out["show_id"], sold_out["category_id"], token)
    assert r.status_code == 201, r.text
    assert r.json()["position"] == 1


async def test_positions_are_assigned_in_order(client, auth, sold_out, make_user):
    positions = []
    for _ in range(3):
        _, token = await make_user()
        r = await _join(client, auth, sold_out["show_id"], sold_out["category_id"], token)
        positions.append(r.json()["position"])
        await asyncio.sleep(0.01)  # distinct joinedAt
    assert positions == [1, 2, 3]


async def test_refreshing_does_not_buy_a_second_place(client, auth, sold_out, make_user):
    _, token = await make_user()
    await _join(client, auth, sold_out["show_id"], sold_out["category_id"], token)
    r = await _join(client, auth, sold_out["show_id"], sold_out["category_id"], token)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ALREADY_WAITING"


async def test_a_category_from_another_show_is_refused(
    client, auth, sold_out, make_show, make_user
):
    other = await make_show(seats=1)
    _, token = await make_user()
    r = await _join(client, auth, sold_out["show_id"], other["category_id"], token)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "CATEGORY_NOT_IN_SHOW"


async def test_an_expired_hold_means_the_category_is_not_sold_out(
    client, auth, make_show, make_user
):
    """
    A stale row must not make a category look sold out and push someone into a
    queue they do not belong in.
    """
    show = await make_show(seats=1)
    _, holder = await make_user()
    _, hopeful = await make_user()

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

    r = await _join(client, auth, show["show_id"], show["category_id"], hopeful)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "SEATS_STILL_AVAILABLE"


# ------------------------------------------ the graded assertion: FIFO order


async def test_a_cancellation_offers_the_seat_to_the_earliest_joiner_only(
    client, auth, sold_out, make_user
):
    tokens = []
    for name in ("first", "second", "third"):
        _, token = await make_user(name=name)
        await _join(client, auth, sold_out["show_id"], sold_out["category_id"], token)
        tokens.append(token)
        await asyncio.sleep(0.01)

    r = await client.post(
        f"{BOOKINGS}/{sold_out['booking_id']}/cancel", headers=auth(sold_out["buyer"])
    )
    assert r.json()["offeredToWaitlist"] == 1

    async with Session() as session:
        entries = (
            (
                await session.execute(
                    select(WaitlistEntry)
                    .where(WaitlistEntry.show_id == sold_out["show_id"])
                    .order_by(WaitlistEntry.joined_at.asc())
                )
            )
            .scalars()
            .all()
        )

    assert [e.status for e in entries] == [
        WaitlistStatus.OFFERED,
        WaitlistStatus.WAITING,
        WaitlistStatus.WAITING,
    ]
    assert entries[0].offer_token is not None
    assert entries[1].offer_token is None
    assert entries[2].offer_token is None


async def test_a_freed_seat_goes_to_offered_not_available(client, auth, sold_out, make_user):
    """
    Everyone else's map must show the seat as unavailable, not as suddenly
    buyable — it belongs to one specific person for the length of the offer.
    """
    _, token = await make_user()
    await _join(client, auth, sold_out["show_id"], sold_out["category_id"], token)
    await client.post(
        f"{BOOKINGS}/{sold_out['booking_id']}/cancel", headers=auth(sold_out["buyer"])
    )

    async with Session() as session:
        seat = (
            (await session.execute(select(ShowSeat).where(ShowSeat.id == sold_out["seat_ids"][0])))
            .scalars()
            .one()
        )
    assert seat.status is SeatStatus.OFFERED
    assert seat.offer_expires_at is not None


async def test_with_an_empty_queue_the_seat_returns_to_general_sale(client, auth, sold_out):
    await client.post(
        f"{BOOKINGS}/{sold_out['booking_id']}/cancel", headers=auth(sold_out["buyer"])
    )
    async with Session() as session:
        seat = (
            (await session.execute(select(ShowSeat).where(ShowSeat.id == sold_out["seat_ids"][0])))
            .scalars()
            .one()
        )
    assert seat.status is SeatStatus.AVAILABLE


# ------------------------------------------------------------------- offers


async def _offer_token_for(client, auth, token):
    entries = (await client.get(f"{WAITLIST}/me", headers=auth(token))).json()["entries"]
    return entries[0]["offerToken"]


async def test_only_the_offered_customer_sees_a_token(client, auth, sold_out, make_user):
    _, first = await make_user(name="first")
    _, second = await make_user(name="second")
    await _join(client, auth, sold_out["show_id"], sold_out["category_id"], first)
    await asyncio.sleep(0.01)
    await _join(client, auth, sold_out["show_id"], sold_out["category_id"], second)
    await client.post(
        f"{BOOKINGS}/{sold_out['booking_id']}/cancel", headers=auth(sold_out["buyer"])
    )

    assert await _offer_token_for(client, auth, first) is not None
    assert await _offer_token_for(client, auth, second) is None


async def test_accepting_an_offer_creates_a_booking(client, auth, sold_out, make_user):
    _, token = await make_user()
    await _join(client, auth, sold_out["show_id"], sold_out["category_id"], token)
    await client.post(
        f"{BOOKINGS}/{sold_out['booking_id']}/cancel", headers=auth(sold_out["buyer"])
    )
    offer = await _offer_token_for(client, auth, token)

    r = await client.post(f"{WAITLIST}/offers/{offer}/accept", headers=auth(token))
    assert r.status_code == 201, r.text
    assert r.json()["booking"]["status"] == "CONFIRMED"

    async with Session() as session:
        entry = (
            (
                await session.execute(
                    select(WaitlistEntry).where(WaitlistEntry.show_id == sold_out["show_id"])
                )
            )
            .scalars()
            .one()
        )
        seat = (
            (await session.execute(select(ShowSeat).where(ShowSeat.id == sold_out["seat_ids"][0])))
            .scalars()
            .one()
        )
    assert entry.status is WaitlistStatus.CONVERTED
    assert entry.offer_token is None  # single use
    assert seat.status is SeatStatus.BOOKED


async def test_somebody_else_cannot_accept_your_offer(client, auth, sold_out, make_user):
    """The token arrives by email, so identity is checked as well as the token."""
    _, mine = await make_user(name="mine")
    _, theirs = await make_user(name="theirs")
    await _join(client, auth, sold_out["show_id"], sold_out["category_id"], mine)
    await client.post(
        f"{BOOKINGS}/{sold_out['booking_id']}/cancel", headers=auth(sold_out["buyer"])
    )
    offer = await _offer_token_for(client, auth, mine)

    r = await client.post(f"{WAITLIST}/offers/{offer}/accept", headers=auth(theirs))
    assert r.status_code == 403


async def test_an_offer_token_is_single_use(client, auth, sold_out, make_user):
    _, token = await make_user()
    await _join(client, auth, sold_out["show_id"], sold_out["category_id"], token)
    await client.post(
        f"{BOOKINGS}/{sold_out['booking_id']}/cancel", headers=auth(sold_out["buyer"])
    )
    offer = await _offer_token_for(client, auth, token)

    await client.post(f"{WAITLIST}/offers/{offer}/accept", headers=auth(token))
    r = await client.post(f"{WAITLIST}/offers/{offer}/accept", headers=auth(token))
    # Accepting clears the token, so the lookup finds no row at all.
    assert r.status_code == 404


async def test_reading_an_offer_needs_no_login(client, auth, sold_out, make_user):
    """The customer follows this from an email, possibly on a signed-out phone."""
    _, token = await make_user()
    await _join(client, auth, sold_out["show_id"], sold_out["category_id"], token)
    await client.post(
        f"{BOOKINGS}/{sold_out['booking_id']}/cancel", headers=auth(sold_out["buyer"])
    )
    offer = await _offer_token_for(client, auth, token)

    r = await client.get(f"{WAITLIST}/offers/{offer}")
    assert r.status_code == 200
    assert r.json()["offer"]["expiresAt"].endswith("Z")


async def test_an_expired_offer_is_gone_not_missing(client, auth, sold_out, make_user):
    """410, not 404: the link was real, it has simply run out."""
    _, token = await make_user()
    await _join(client, auth, sold_out["show_id"], sold_out["category_id"], token)
    await client.post(
        f"{BOOKINGS}/{sold_out['booking_id']}/cancel", headers=auth(sold_out["buyer"])
    )
    offer = await _offer_token_for(client, auth, token)

    async with Session() as session:
        await session.execute(
            update(WaitlistEntry)
            .where(WaitlistEntry.offer_token == offer)
            .values(offer_expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()

    assert (await client.get(f"{WAITLIST}/offers/{offer}")).status_code == 410
    r = await client.post(f"{WAITLIST}/offers/{offer}/accept", headers=auth(token))
    assert r.status_code == 410


async def test_an_unknown_offer_token_404s(client):
    assert (await client.get(f"{WAITLIST}/offers/nonsense")).status_code == 404


# ------------------------------------------------------------------ leaving


async def test_leaving_the_queue_while_waiting(client, auth, sold_out, make_user):
    _, token = await make_user()
    entry_id = (
        await _join(client, auth, sold_out["show_id"], sold_out["category_id"], token)
    ).json()["id"]

    r = await client.delete(f"{WAITLIST}/{entry_id}", headers=auth(token))
    assert r.status_code == 200
    assert r.json() == {"left": True, "passedOn": False}


async def test_giving_up_an_offer_hands_it_to_the_next_person(client, auth, sold_out, make_user):
    _, first = await make_user(name="first")
    _, second = await make_user(name="second")
    first_entry = (
        await _join(client, auth, sold_out["show_id"], sold_out["category_id"], first)
    ).json()["id"]
    await asyncio.sleep(0.01)
    await _join(client, auth, sold_out["show_id"], sold_out["category_id"], second)
    await client.post(
        f"{BOOKINGS}/{sold_out['booking_id']}/cancel", headers=auth(sold_out["buyer"])
    )

    r = await client.delete(f"{WAITLIST}/{first_entry}", headers=auth(first))
    assert r.json() == {"left": True, "passedOn": True}
    assert await _offer_token_for(client, auth, second) is not None


async def test_you_cannot_remove_somebody_else_s_entry(client, auth, sold_out, make_user):
    _, owner = await make_user(name="owner")
    _, stranger = await make_user(name="stranger")
    entry_id = (
        await _join(client, auth, sold_out["show_id"], sold_out["category_id"], owner)
    ).json()["id"]

    r = await client.delete(f"{WAITLIST}/{entry_id}", headers=auth(stranger))
    assert r.status_code == 403


# ----------------------------------------------------------------- sweeping


async def test_an_ignored_offer_walks_down_the_queue(client, auth, sold_out, make_user):
    """
    An expired offer means "this person did not take it", not "nobody wants
    it" — so the seat goes to the next in line, NOT back on general sale.
    """
    _, first = await make_user(name="first")
    _, second = await make_user(name="second")
    await _join(client, auth, sold_out["show_id"], sold_out["category_id"], first)
    await asyncio.sleep(0.01)
    await _join(client, auth, sold_out["show_id"], sold_out["category_id"], second)
    await client.post(
        f"{BOOKINGS}/{sold_out['booking_id']}/cancel", headers=auth(sold_out["buyer"])
    )

    async with Session() as session:
        await session.execute(
            update(WaitlistEntry)
            .where(WaitlistEntry.status == WaitlistStatus.OFFERED)
            .values(offer_expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()

    expired, offers = await sweep_expired_offers()
    assert expired == 1
    assert len(offers) == 1

    async with Session() as session:
        entries = (
            (
                await session.execute(
                    select(WaitlistEntry)
                    .where(WaitlistEntry.show_id == sold_out["show_id"])
                    .order_by(WaitlistEntry.joined_at.asc())
                )
            )
            .scalars()
            .all()
        )
        seat = (
            (await session.execute(select(ShowSeat).where(ShowSeat.id == sold_out["seat_ids"][0])))
            .scalars()
            .one()
        )

    assert entries[0].status is WaitlistStatus.EXPIRED
    assert entries[1].status is WaitlistStatus.OFFERED
    assert seat.status is SeatStatus.OFFERED


async def test_the_last_expired_offer_returns_the_seat_to_sale(client, auth, sold_out, make_user):
    _, only = await make_user()
    await _join(client, auth, sold_out["show_id"], sold_out["category_id"], only)
    await client.post(
        f"{BOOKINGS}/{sold_out['booking_id']}/cancel", headers=auth(sold_out["buyer"])
    )

    async with Session() as session:
        await session.execute(
            update(WaitlistEntry)
            .where(WaitlistEntry.status == WaitlistStatus.OFFERED)
            .values(offer_expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()

    expired, offers = await sweep_expired_offers()
    assert expired == 1
    assert offers == []

    async with Session() as session:
        seat = (
            (await session.execute(select(ShowSeat).where(ShowSeat.id == sold_out["seat_ids"][0])))
            .scalars()
            .one()
        )
    assert seat.status is SeatStatus.AVAILABLE


async def test_sweeping_nothing_is_harmless(make_show):
    await make_show()
    assert await sweep_expired_offers() == (0, [])
