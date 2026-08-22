from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update

from ticket_api.db import Session
from ticket_api.models import (
    Booking,
    BookingSeat,
    BookingStatus,
    Role,
    SeatStatus,
    Show,
    ShowSeat,
    utcnow,
)

BOOKINGS = "/api/v1/bookings"


async def _hold_and_book(client, auth, show, token, seat_ids=None):
    seat_ids = seat_ids or show["seat_ids"]
    await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": seat_ids},
        headers=auth(token),
    )
    return await client.post(
        BOOKINGS, json={"showId": show["show_id"], "seatIds": seat_ids}, headers=auth(token)
    )


async def test_booking_a_held_seat_confirms_it(client, auth, make_show, make_user):
    show = await make_show(seats=2)
    _, token = await make_user()

    r = await _hold_and_book(client, auth, show, token)
    assert r.status_code == 201, r.text

    booking = r.json()["booking"]
    assert booking["reference"].startswith("BK-")
    assert booking["status"] == "CONFIRMED"
    assert booking["total"] == "200"
    assert {s["label"] for s in booking["seats"]} == {"A1", "A2"}
    assert booking["createdAt"].endswith("Z")


async def test_the_qr_token_is_not_in_the_create_response(client, auth, make_show, make_user):
    """A bearer credential for entry travels in the emailed QR, not in a body
    that a proxy or a log might keep."""
    show = await make_show(seats=1)
    _, token = await make_user()
    r = await _hold_and_book(client, auth, show, token)
    assert r.json()["booking"]["qrToken"] is None


async def test_the_owner_can_read_their_qr_token(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, token = await make_user()
    booking_id = (await _hold_and_book(client, auth, show, token)).json()["booking"]["id"]

    r = await client.get(f"{BOOKINGS}/{booking_id}", headers=auth(token))
    assert r.status_code == 200
    assert len(r.json()["booking"]["qrToken"]) == 64  # 32 bytes, hex


async def test_a_stranger_cannot_read_a_booking(client, auth, make_show, make_user):
    """Booking ids are uuids, but "hard to guess" is not an access control."""
    show = await make_show(seats=1)
    _, owner = await make_user()
    _, stranger = await make_user()
    booking_id = (await _hold_and_book(client, auth, show, owner)).json()["booking"]["id"]

    r = await client.get(f"{BOOKINGS}/{booking_id}", headers=auth(stranger))
    assert r.status_code == 403


async def test_an_admin_can_read_any_booking(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, owner = await make_user()
    _, admin = await make_user(Role.ADMIN)
    booking_id = (await _hold_and_book(client, auth, show, owner)).json()["booking"]["id"]

    assert (await client.get(f"{BOOKINGS}/{booking_id}", headers=auth(admin))).status_code == 200


async def test_you_cannot_book_a_seat_somebody_else_is_holding(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, holder = await make_user()
    _, thief = await make_user()

    await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(holder),
    )
    r = await client.post(
        BOOKINGS,
        json={"showId": show["show_id"], "seatIds": show["seat_ids"]},
        headers=auth(thief),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "HOLD_NOT_VALID"


async def test_you_cannot_book_on_an_expired_hold(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, token = await make_user()
    await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(token),
    )

    async with Session() as session:
        await session.execute(
            update(ShowSeat)
            .where(ShowSeat.id == show["seat_ids"][0])
            .values(hold_expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()

    r = await client.post(
        BOOKINGS,
        json={"showId": show["show_id"], "seatIds": show["seat_ids"]},
        headers=auth(token),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "HOLD_NOT_VALID"


async def test_price_is_frozen_at_booking_time(client, auth, make_show, make_user):
    """
    An organiser re-pricing a category next week must not rewrite what this
    booking was worth.
    """
    from ticket_api.models import SeatCategory

    show = await make_show(seats=1, price="100")
    _, token = await make_user()
    booking_id = (await _hold_and_book(client, auth, show, token)).json()["booking"]["id"]

    async with Session() as session:
        await session.execute(
            update(SeatCategory).where(SeatCategory.id == show["category_id"]).values(price="999")
        )
        await session.commit()

    r = await client.get(f"{BOOKINGS}/{booking_id}", headers=auth(token))
    assert r.json()["booking"]["total"] == "100"


async def test_my_bookings_are_newest_first(client, auth, make_show, make_user):
    _, token = await make_user()
    first = await make_show(seats=1)
    second = await make_show(seats=1)
    await _hold_and_book(client, auth, first, token)
    await _hold_and_book(client, auth, second, token)

    r = await client.get(BOOKINGS, headers=auth(token))
    assert r.status_code == 200
    bookings = r.json()["bookings"]
    assert len(bookings) == 2
    assert bookings[0]["createdAt"] >= bookings[1]["createdAt"]
    # A list is not the place for a bearer credential.
    assert all(b["qrToken"] is None for b in bookings)


# ------------------------------------------------------------- cancellation


async def test_cancelling_releases_the_seat(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, token = await make_user()
    booking_id = (await _hold_and_book(client, auth, show, token)).json()["booking"]["id"]

    r = await client.post(f"{BOOKINGS}/{booking_id}/cancel", headers=auth(token))
    assert r.status_code == 200
    assert r.json() == {"cancelled": True, "seatsReleased": 1, "offeredToWaitlist": 0}

    seats = (await client.get(f"/api/v1/shows/{show['show_id']}/seats")).json()["seats"]
    assert seats[0]["status"] == "AVAILABLE"


async def test_a_cancelled_seat_can_be_sold_again(client, auth, make_show, make_user):
    """
    ADR-020 — a plain unique on showSeatId made this impossible, because the
    cancelled booking's row still claimed the seat forever.
    """
    show = await make_show(seats=1)
    _, first = await make_user()
    _, second = await make_user()

    booking_id = (await _hold_and_book(client, auth, show, first)).json()["booking"]["id"]
    await client.post(f"{BOOKINGS}/{booking_id}/cancel", headers=auth(first))

    r = await _hold_and_book(client, auth, show, second)
    assert r.status_code == 201, r.text


async def test_cancelling_keeps_the_row_for_history(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, token = await make_user()
    booking_id = (await _hold_and_book(client, auth, show, token)).json()["booking"]["id"]
    await client.post(f"{BOOKINGS}/{booking_id}/cancel", headers=auth(token))

    async with Session() as session:
        row = (
            (await session.execute(select(BookingSeat).where(BookingSeat.booking_id == booking_id)))
            .scalars()
            .one()
        )
        booking = (
            (await session.execute(select(Booking).where(Booking.id == booking_id))).scalars().one()
        )

    assert row.released_at is not None  # claim released
    assert row.price_at_booking is not None  # revenue history kept
    assert booking.status is BookingStatus.CANCELLED
    assert booking.cancelled_at is not None


async def test_double_cancellation_conflicts(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, token = await make_user()
    booking_id = (await _hold_and_book(client, auth, show, token)).json()["booking"]["id"]

    await client.post(f"{BOOKINGS}/{booking_id}/cancel", headers=auth(token))
    r = await client.post(f"{BOOKINGS}/{booking_id}/cancel", headers=auth(token))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ALREADY_CANCELLED"


async def test_a_stranger_cannot_cancel_your_booking(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, owner = await make_user()
    _, stranger = await make_user()
    booking_id = (await _hold_and_book(client, auth, show, owner)).json()["booking"]["id"]

    r = await client.post(f"{BOOKINGS}/{booking_id}/cancel", headers=auth(stranger))
    assert r.status_code == 403


async def test_a_started_show_cannot_be_cancelled(client, auth, make_show, make_user):
    """Putting a seat back on sale for a show already under way helps nobody."""
    show = await make_show(seats=1)
    _, token = await make_user()
    booking_id = (await _hold_and_book(client, auth, show, token)).json()["booking"]["id"]

    async with Session() as session:
        await session.execute(
            update(Show)
            .where(Show.id == show["show_id"])
            .values(starts_at=utcnow() - timedelta(minutes=1))
        )
        await session.commit()

    r = await client.post(f"{BOOKINGS}/{booking_id}/cancel", headers=auth(token))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "SHOW_ALREADY_STARTED"


# ------------------------------------------------------------- verification


async def test_verify_confirms_a_real_ticket(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, token = await make_user()
    booking_id = (await _hold_and_book(client, auth, show, token)).json()["booking"]["id"]
    qr = (await client.get(f"{BOOKINGS}/{booking_id}", headers=auth(token))).json()["booking"][
        "qrToken"
    ]

    r = await client.get(f"/api/v1/verify/{qr}")  # public — the door is not logged in
    assert r.status_code == 200
    ticket = r.json()["ticket"]
    assert ticket["valid"] is True
    assert ticket["seats"] == ["A1"]


async def test_verify_reveals_no_customer_identity(client, auth, make_show, make_user):
    """A QR code is a thing people photograph and forward."""
    show = await make_show(seats=1)
    user_id, token = await make_user()
    booking_id = (await _hold_and_book(client, auth, show, token)).json()["booking"]["id"]
    qr = (await client.get(f"{BOOKINGS}/{booking_id}", headers=auth(token))).json()["booking"][
        "qrToken"
    ]

    body = (await client.get(f"/api/v1/verify/{qr}")).text
    assert "email" not in body
    assert user_id not in body


async def test_verify_marks_a_cancelled_ticket_invalid(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, token = await make_user()
    booking_id = (await _hold_and_book(client, auth, show, token)).json()["booking"]["id"]
    qr = (await client.get(f"{BOOKINGS}/{booking_id}", headers=auth(token))).json()["booking"][
        "qrToken"
    ]
    await client.post(f"{BOOKINGS}/{booking_id}/cancel", headers=auth(token))

    ticket = (await client.get(f"/api/v1/verify/{qr}")).json()["ticket"]
    # A wrong token and a cancelled booking are different facts; door staff
    # need to tell them apart.
    assert ticket["valid"] is False
    assert ticket["status"] == "CANCELLED"


async def test_verify_404s_on_an_unknown_token(client):
    r = await client.get("/api/v1/verify/nonsense")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "TICKET_NOT_FOUND"


async def test_booked_seats_show_as_booked(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, token = await make_user()
    await _hold_and_book(client, auth, show, token)

    async with Session() as session:
        row = (
            (await session.execute(select(ShowSeat).where(ShowSeat.id == show["seat_ids"][0])))
            .scalars()
            .one()
        )
    assert row.status is SeatStatus.BOOKED
    assert row.held_by_user_id is None
