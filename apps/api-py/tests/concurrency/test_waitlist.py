"""
The waitlist under contention.

The companion to the hold race: cancelling several booked seats at once must
hand each one to a distinct waiting customer, never the same person twice, and
never the same seat to two people.
"""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import select

from ticket_api.db import Session
from ticket_api.models import SeatStatus, ShowSeat, WaitlistEntry, WaitlistStatus


async def test_several_freed_seats_go_to_distinct_customers(
    live_server, make_show, make_user, auth
):
    show = await make_show(seats=3)
    _, buyer = await make_user(name="buyer")

    async with httpx.AsyncClient(base_url=live_server, timeout=60.0) as client:
        await client.post(
            f"/api/v1/shows/{show['show_id']}/holds",
            json={"seatIds": show["seat_ids"]},
            headers=auth(buyer),
        )
        booking = await client.post(
            "/api/v1/bookings",
            json={"showId": show["show_id"], "seatIds": show["seat_ids"]},
            headers=auth(buyer),
        )
        assert booking.status_code == 201, booking.text

        # Three waiting, three seats about to free.
        for i in range(3):
            _, token = await make_user(name=f"waiter{i}")
            r = await client.post(
                f"/api/v1/shows/{show['show_id']}/waitlist",
                json={"categoryId": show["category_id"]},
                headers=auth(token),
            )
            assert r.status_code == 201, r.text
            await asyncio.sleep(0.01)

        r = await client.post(
            f"/api/v1/bookings/{booking.json()['booking']['id']}/cancel",
            headers=auth(buyer),
        )
        assert r.status_code == 200, r.text
        assert r.json()["seatsReleased"] == 3
        assert r.json()["offeredToWaitlist"] == 3

    async with Session() as session:
        entries = (
            (
                await session.execute(
                    select(WaitlistEntry).where(WaitlistEntry.show_id == show["show_id"])
                )
            )
            .scalars()
            .all()
        )
        seats = (
            (await session.execute(select(ShowSeat).where(ShowSeat.show_id == show["show_id"])))
            .scalars()
            .all()
        )

    offered = [e for e in entries if e.status is WaitlistStatus.OFFERED]
    assert len(offered) == 3
    # One seat each, and three different seats.
    assert len({e.offered_seat_id for e in offered}) == 3
    assert len({e.offer_token for e in offered}) == 3
    assert all(s.status is SeatStatus.OFFERED for s in seats)


async def test_two_customers_racing_to_accept_the_same_offer(
    live_server, make_show, make_user, auth
):
    """
    Only the customer the offer was made to may accept, and only once. Firing
    both at a real listener means the identity check and the seat lock are
    exercised concurrently rather than in sequence.
    """
    show = await make_show(seats=1)
    _, buyer = await make_user(name="buyer")
    _, waiter = await make_user(name="waiter")
    _, intruder = await make_user(name="intruder")

    async with httpx.AsyncClient(base_url=live_server, timeout=60.0) as client:
        await client.post(
            f"/api/v1/shows/{show['show_id']}/holds",
            json={"seatIds": show["seat_ids"]},
            headers=auth(buyer),
        )
        booking = await client.post(
            "/api/v1/bookings",
            json={"showId": show["show_id"], "seatIds": show["seat_ids"]},
            headers=auth(buyer),
        )
        await client.post(
            f"/api/v1/shows/{show['show_id']}/waitlist",
            json={"categoryId": show["category_id"]},
            headers=auth(waiter),
        )
        await client.post(
            f"/api/v1/bookings/{booking.json()['booking']['id']}/cancel", headers=auth(buyer)
        )

        entries = (await client.get("/api/v1/waitlist/me", headers=auth(waiter))).json()["entries"]
        offer = entries[0]["offerToken"]

        rightful, thief = await asyncio.gather(
            client.post(f"/api/v1/waitlist/offers/{offer}/accept", headers=auth(waiter)),
            client.post(f"/api/v1/waitlist/offers/{offer}/accept", headers=auth(intruder)),
        )

    assert rightful.status_code == 201, rightful.text
    # 403 if the intruder reached the row first and failed the identity check;
    # 404 if the rightful owner had already committed and cleared the token, so
    # there was no row left to find. Which one happens depends on lock ordering,
    # so asserting either specifically would make this test flaky. What matters
    # is that the intruder is refused and never gets a seat.
    assert thief.status_code in (403, 404), thief.text

    async with Session() as session:
        seat = (
            (await session.execute(select(ShowSeat).where(ShowSeat.id == show["seat_ids"][0])))
            .scalars()
            .one()
        )
    assert seat.status is SeatStatus.BOOKED


async def test_the_same_customer_double_clicking_accept_books_once(
    live_server, make_show, make_user, auth
):
    show = await make_show(seats=1)
    _, buyer = await make_user(name="buyer")
    _, waiter = await make_user(name="waiter")

    async with httpx.AsyncClient(base_url=live_server, timeout=60.0) as client:
        await client.post(
            f"/api/v1/shows/{show['show_id']}/holds",
            json={"seatIds": show["seat_ids"]},
            headers=auth(buyer),
        )
        booking = await client.post(
            "/api/v1/bookings",
            json={"showId": show["show_id"], "seatIds": show["seat_ids"]},
            headers=auth(buyer),
        )
        await client.post(
            f"/api/v1/shows/{show['show_id']}/waitlist",
            json={"categoryId": show["category_id"]},
            headers=auth(waiter),
        )
        await client.post(
            f"/api/v1/bookings/{booking.json()['booking']['id']}/cancel", headers=auth(buyer)
        )
        offer = (await client.get("/api/v1/waitlist/me", headers=auth(waiter))).json()["entries"][
            0
        ]["offerToken"]

        first, second = await asyncio.gather(
            client.post(f"/api/v1/waitlist/offers/{offer}/accept", headers=auth(waiter)),
            client.post(f"/api/v1/waitlist/offers/{offer}/accept", headers=auth(waiter)),
        )

    codes = sorted([first.status_code, second.status_code])
    # One booking; the loser is refused, not served a duplicate.
    assert codes[0] == 201, (first.text, second.text)
    assert codes[1] in (404, 410), codes

    from ticket_api.models import Booking

    async with Session() as session:
        bookings = (
            (await session.execute(select(Booking).where(Booking.show_id == show["show_id"])))
            .scalars()
            .all()
        )
    # The buyer's original (now cancelled) plus exactly one from the offer.
    assert len(bookings) == 2
