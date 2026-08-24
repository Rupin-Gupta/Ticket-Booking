from __future__ import annotations

import pytest

from ticket_api.models import Role


@pytest.fixture
async def admin(make_user):
    return await make_user(Role.ADMIN, "admin")


async def _booking(client, auth, show, token, seat_index=0):
    """
    Books a seat and returns the booking *including* its qrToken.

    The create response withholds the token on purpose — it is a bearer
    credential for entry and only travels on the single-booking read and in the
    emailed QR — so the token has to be fetched separately.
    """
    seat = show["seat_ids"][seat_index]
    await client.post(
        f"/api/v1/shows/{show['show_id']}/holds", json={"seatIds": [seat]}, headers=auth(token)
    )
    r = await client.post(
        "/api/v1/bookings",
        json={"showId": show["show_id"], "seatIds": [seat]},
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    booking_id = r.json()["booking"]["id"]

    full = await client.get(f"/api/v1/bookings/{booking_id}", headers=auth(token))
    assert full.status_code == 200, full.text
    assert full.json()["booking"]["qrToken"], "the single-booking read must carry the QR token"
    return full.json()["booking"]


# --------------------------------------------------------------- check-in


async def test_a_ticket_is_admitted_once_and_the_second_scan_says_when(
    client, auth, make_show, make_user
):
    show = await make_show()
    _, customer = await make_user(Role.CUSTOMER, "customer")
    booking = await _booking(client, auth, show, customer)
    token = booking["qrToken"]

    first = await client.post(
        f"/api/v1/verify/{token}/check-in", headers=auth(show["organiser_token"])
    )
    assert first.status_code == 200, first.text
    assert first.json()["admitted"] is True
    assert first.json()["ticket"]["checkedInAt"] is not None

    second = await client.post(
        f"/api/v1/verify/{token}/check-in", headers=auth(show["organiser_token"])
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "ALREADY_CHECKED_IN"
    # The door needs the time, not just a refusal.
    assert "Already admitted at" in second.json()["error"]["message"]


async def test_the_public_read_shows_admission_state_but_never_admits(
    client, auth, make_show, make_user
):
    show = await make_show()
    _, customer = await make_user(Role.CUSTOMER, "customer")
    token = (await _booking(client, auth, show, customer))["qrToken"]

    before = await client.get(f"/api/v1/verify/{token}")
    assert before.status_code == 200
    assert before.json()["ticket"]["checkedInAt"] is None

    await client.post(f"/api/v1/verify/{token}/check-in", headers=auth(show["organiser_token"]))

    after = await client.get(f"/api/v1/verify/{token}")
    assert after.json()["ticket"]["checkedInAt"] is not None


async def test_admitting_requires_authentication(client, auth, make_show, make_user):
    """
    The read is public; the write must not be. A QR is photographed and
    forwarded — anyone able to burn a stranger's ticket could get them turned
    away at the door holding a valid one.
    """
    show = await make_show()
    _, customer = await make_user(Role.CUSTOMER, "customer")
    token = (await _booking(client, auth, show, customer))["qrToken"]

    anonymous = await client.post(f"/api/v1/verify/{token}/check-in")
    assert anonymous.status_code == 401

    as_customer = await client.post(f"/api/v1/verify/{token}/check-in", headers=auth(customer))
    assert as_customer.status_code == 403


async def test_another_organiser_cannot_admit_on_someone_elses_event(
    client, auth, make_show, make_user
):
    show = await make_show()
    _, customer = await make_user(Role.CUSTOMER, "customer")
    _, other = await make_user(Role.ORGANISER, "other-organiser")
    token = (await _booking(client, auth, show, customer))["qrToken"]

    r = await client.post(f"/api/v1/verify/{token}/check-in", headers=auth(other))
    assert r.status_code == 403


async def test_a_cancelled_booking_is_not_admitted(client, auth, make_show, make_user):
    show = await make_show()
    _, customer = await make_user(Role.CUSTOMER, "customer")
    booking = await _booking(client, auth, show, customer)
    await client.post(f"/api/v1/bookings/{booking['id']}/cancel", headers=auth(customer))

    r = await client.post(
        f"/api/v1/verify/{booking['qrToken']}/check-in", headers=auth(show["organiser_token"])
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "TICKET_NOT_VALID"


async def test_an_unknown_token_is_not_admitted(client, auth, make_show):
    show = await make_show()
    r = await client.post(
        "/api/v1/verify/not-a-real-token/check-in", headers=auth(show["organiser_token"])
    )
    assert r.status_code == 404


# ------------------------------------------------------- concurrency lab


async def test_the_lab_race_produces_exactly_one_winner(client, auth, admin, make_show):
    show = await make_show(seats=4)

    r = await client.post(
        "/api/v1/lab/race",
        json={"showId": show["show_id"], "seatId": show["seat_ids"][0], "attempts": 20},
        headers=auth(admin[1]),
    )

    assert r.status_code == 200, r.text
    race = r.json()["race"]
    assert race["attempts"] == 20
    assert race["outcome"]["won"] == 1
    assert race["outcome"]["rejected"] == 19
    assert race["outcome"]["errors"] == 0
    assert race["errorCodes"] == []
    assert race["passed"] is True


async def test_the_lab_leaves_the_seat_free_so_it_can_be_run_twice(client, auth, admin, make_show):
    show = await make_show(seats=2)
    body = {"showId": show["show_id"], "seatId": show["seat_ids"][0], "attempts": 5}

    first = await client.post("/api/v1/lab/race", json=body, headers=auth(admin[1]))
    second = await client.post("/api/v1/lab/race", json=body, headers=auth(admin[1]))

    assert first.json()["race"]["passed"] is True
    assert second.json()["race"]["passed"] is True, "the lab left its winner's hold behind"

    seats = (await client.get(f"/api/v1/shows/{show['show_id']}/seats")).json()["seats"]
    assert all(s["status"] == "AVAILABLE" for s in seats)


async def test_the_lab_picks_a_seat_when_none_is_named(client, auth, admin, make_show):
    show = await make_show(seats=3)

    r = await client.post(
        "/api/v1/lab/race",
        json={"showId": show["show_id"], "attempts": 4},
        headers=auth(admin[1]),
    )

    assert r.status_code == 200, r.text
    assert r.json()["race"]["seatId"] in show["seat_ids"]


async def test_the_lab_is_admin_only(client, auth, make_show, make_user):
    """Fifty concurrent transactions on request is a denial-of-service lever."""
    show = await make_show()
    _, customer = await make_user(Role.CUSTOMER, "customer")

    assert (
        await client.post("/api/v1/lab/race", json={"showId": show["show_id"]})
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/lab/race", json={"showId": show["show_id"]}, headers=auth(customer)
        )
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/lab/race",
            json={"showId": show["show_id"]},
            headers=auth(show["organiser_token"]),
        )
    ).status_code == 403


async def test_the_lab_refuses_a_silly_number_of_contenders(client, auth, admin, make_show):
    show = await make_show()

    r = await client.post(
        "/api/v1/lab/race",
        json={"showId": show["show_id"], "attempts": 5000},
        headers=auth(admin[1]),
    )
    assert r.status_code == 400
