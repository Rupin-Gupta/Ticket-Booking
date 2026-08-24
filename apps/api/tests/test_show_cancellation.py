from __future__ import annotations

import pytest
from sqlalchemy import select

from ticket_api.db import Session
from ticket_api.models import (
    Booking,
    BookingSeat,
    BookingStatus,
    Role,
    ShowSeat,
    WaitlistEntry,
    WaitlistStatus,
)

SHOWS = "/api/v1/shows"


@pytest.fixture
async def admin(make_user):
    return await make_user(Role.ADMIN, "admin")


async def _book(client, auth, show, token, seat_index=0):
    """Hold then book one seat, returning the booking payload."""
    seat = show["seat_ids"][seat_index]
    await client.post(
        f"{SHOWS}/{show['show_id']}/holds", json={"seatIds": [seat]}, headers=auth(token)
    )
    r = await client.post(
        "/api/v1/bookings",
        json={"showId": show["show_id"], "seatIds": [seat]},
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["booking"]


async def test_cancelling_an_empty_show_marks_it_cancelled(client, auth, make_show):
    show = await make_show()

    r = await client.post(
        f"{SHOWS}/{show['show_id']}/cancel", headers=auth(show["organiser_token"])
    )

    assert r.status_code == 200, r.text
    body = r.json()["show"]
    assert body["status"] == "CANCELLED"
    assert body["bookingsCancelled"] == 0
    assert body["customersNotified"] == 0


async def test_cancelling_cancels_every_confirmed_booking_and_releases_its_seats(
    client, auth, make_show, make_user
):
    show = await make_show(seats=4)
    _, alice = await make_user(Role.CUSTOMER, "alice")
    _, bob = await make_user(Role.CUSTOMER, "bob")
    booking_a = await _book(client, auth, show, alice, 0)
    await _book(client, auth, show, bob, 1)

    r = await client.post(
        f"{SHOWS}/{show['show_id']}/cancel", headers=auth(show["organiser_token"])
    )

    assert r.status_code == 200, r.text
    assert r.json()["show"]["bookingsCancelled"] == 2
    assert r.json()["show"]["customersNotified"] == 2

    async with Session() as session:
        statuses = (
            (
                await session.execute(
                    select(Booking.status).where(Booking.show_id == show["show_id"])
                )
            )
            .scalars()
            .all()
        )
        assert set(statuses) == {BookingStatus.CANCELLED}

        # The rows survive for revenue history; only the claim is released.
        unreleased = await session.scalar(
            select(BookingSeat.id)
            .join(Booking, Booking.id == BookingSeat.booking_id)
            .where(Booking.show_id == show["show_id"], BookingSeat.released_at.is_(None))
        )
        assert unreleased is None

        booked = await session.scalar(
            select(ShowSeat.id).where(
                ShowSeat.show_id == show["show_id"], ShowSeat.status != "AVAILABLE"
            )
        )
        assert booked is None, "a cancelled show must not leave a seat reading as claimed"

    # The customer's own history still shows what happened to them.
    mine = await client.get("/api/v1/bookings", headers=auth(alice))
    assert mine.json()["bookings"][0]["id"] == booking_a["id"]
    assert mine.json()["bookings"][0]["status"] == "CANCELLED"


async def test_cancelling_closes_the_waitlist_without_offering_anyone_a_seat(
    client, auth, make_show, make_user
):
    """
    Every other path that frees a seat calls advance_waitlist(). This one must
    not: the seat belongs to a show that is not happening, and handing it on
    would email somebody an offer for a cancelled performance.
    """
    show = await make_show(seats=1)
    _, buyer = await make_user(Role.CUSTOMER, "buyer")
    _, waiter = await make_user(Role.CUSTOMER, "waiter")
    await _book(client, auth, show, buyer, 0)

    joined = await client.post(
        f"{SHOWS}/{show['show_id']}/waitlist",
        json={"categoryId": show["category_id"]},
        headers=auth(waiter),
    )
    assert joined.status_code == 201, joined.text

    r = await client.post(
        f"{SHOWS}/{show['show_id']}/cancel", headers=auth(show["organiser_token"])
    )
    assert r.json()["show"]["waitlistClosed"] == 1

    async with Session() as session:
        entry = (
            (
                await session.execute(
                    select(WaitlistEntry).where(WaitlistEntry.show_id == show["show_id"])
                )
            )
            .scalars()
            .one()
        )
        assert entry.status is WaitlistStatus.CANCELLED
        assert entry.status is not WaitlistStatus.OFFERED
        # A bearer token for a seat at a show that no longer exists.
        assert entry.offer_token is None
        assert entry.offer_expires_at is None


async def test_a_cancelled_show_refuses_new_holds_and_bookings(client, auth, make_show, make_user):
    show = await make_show(seats=2)
    _, customer = await make_user(Role.CUSTOMER, "customer")
    await client.post(f"{SHOWS}/{show['show_id']}/cancel", headers=auth(show["organiser_token"]))

    held = await client.post(
        f"{SHOWS}/{show['show_id']}/holds",
        json={"seatIds": [show["seat_ids"][0]]},
        headers=auth(customer),
    )
    assert held.status_code == 409, held.text
    assert held.json()["error"]["code"] == "SHOW_CANCELLED"

    # Booking is guarded separately: cancelling resets seats to AVAILABLE, so
    # the seat map looks perfectly bookable.
    booked = await client.post(
        "/api/v1/bookings",
        json={"showId": show["show_id"], "seatIds": [show["seat_ids"][0]]},
        headers=auth(customer),
    )
    assert booked.status_code == 409, booked.text
    assert booked.json()["error"]["code"] == "SHOW_CANCELLED"


async def test_a_cancelled_show_disappears_from_the_public_event_page(client, auth, make_show):
    show = await make_show()
    before = await client.get(f"/api/v1/events/{show['event_id']}")
    assert [s["id"] for s in before.json()["event"]["shows"]] == [show["show_id"]]

    await client.post(f"{SHOWS}/{show['show_id']}/cancel", headers=auth(show["organiser_token"]))

    after = await client.get(f"/api/v1/events/{show['event_id']}")
    assert after.json()["event"]["shows"] == []


async def test_cancelling_frees_the_venue_slot_for_another_show(client, auth, make_show):
    """
    The exclusion constraint is partial on status = 'SCHEDULED', so flipping the
    status is the whole mechanism — no cleanup code, and the slot reopens.
    """
    show = await make_show()
    detail = (await client.get(f"{SHOWS}/{show['show_id']}")).json()["show"]

    clash = {"startsAt": detail["startsAt"], "durationMinutes": 120}
    refused = await client.post(
        f"/api/v1/events/{show['event_id']}/shows",
        json=clash,
        headers=auth(show["organiser_token"]),
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "VENUE_DOUBLE_BOOKED"

    await client.post(f"{SHOWS}/{show['show_id']}/cancel", headers=auth(show["organiser_token"]))

    accepted = await client.post(
        f"/api/v1/events/{show['event_id']}/shows",
        json=clash,
        headers=auth(show["organiser_token"]),
    )
    assert accepted.status_code == 201, accepted.text


async def test_cancelling_twice_is_refused(client, auth, make_show):
    show = await make_show()
    await client.post(f"{SHOWS}/{show['show_id']}/cancel", headers=auth(show["organiser_token"]))

    again = await client.post(
        f"{SHOWS}/{show['show_id']}/cancel", headers=auth(show["organiser_token"])
    )
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "SHOW_ALREADY_CANCELLED"


async def test_another_organiser_cannot_cancel_someone_elses_show(
    client, auth, make_show, make_user
):
    show = await make_show()
    _, other = await make_user(Role.ORGANISER, "other-organiser")

    r = await client.post(f"{SHOWS}/{show['show_id']}/cancel", headers=auth(other))
    assert r.status_code == 403


async def test_an_admin_may_cancel_any_show(client, auth, make_show, admin):
    show = await make_show()

    r = await client.post(f"{SHOWS}/{show['show_id']}/cancel", headers=auth(admin[1]))
    assert r.status_code == 200, r.text


async def test_a_customer_cannot_cancel_a_show(client, auth, make_show, make_user):
    show = await make_show()
    _, customer = await make_user(Role.CUSTOMER, "customer")

    r = await client.post(f"{SHOWS}/{show['show_id']}/cancel", headers=auth(customer))
    assert r.status_code == 403


async def test_cancelling_an_unknown_show_is_404(client, auth, make_show):
    show = await make_show()
    r = await client.post(f"{SHOWS}/does-not-exist/cancel", headers=auth(show["organiser_token"]))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "SHOW_NOT_FOUND"
