from __future__ import annotations

import pytest

from ticket_api.models import Role

VENUES = "/api/v1/venues"
EVENTS = "/api/v1/events"


@pytest.fixture
async def admin(make_user):
    return await make_user(Role.ADMIN, "admin")


# ------------------------------------------------------------------- venues


async def test_an_empty_venue_can_be_deleted(client, auth, admin):
    created = await client.post(
        VENUES, json={"name": "Disposable", "address": "x"}, headers=auth(admin[1])
    )
    venue_id = created.json()["venue"]["id"]

    r = await client.delete(f"{VENUES}/{venue_id}", headers=auth(admin[1]))
    assert r.status_code == 204, r.text

    assert (await client.get(f"{VENUES}/{venue_id}")).status_code == 404


async def test_deleting_a_venue_takes_its_seats_with_it(client, auth, admin):
    created = await client.post(
        VENUES, json={"name": "Seated", "address": "x"}, headers=auth(admin[1])
    )
    venue_id = created.json()["venue"]["id"]
    await client.post(
        f"{VENUES}/{venue_id}/seats",
        json={"section": "Stalls", "rows": 2, "seatsPerRow": 3},
        headers=auth(admin[1]),
    )

    assert (await client.delete(f"{VENUES}/{venue_id}", headers=auth(admin[1]))).status_code == 204
    # Gone rather than orphaned: the sections endpoint reads Seat directly.
    assert (await client.get(f"{VENUES}/{venue_id}/sections")).json()["sections"] == []


async def test_a_venue_with_an_event_is_refused_and_the_message_names_the_blocker(
    client, auth, admin, make_show
):
    show = await make_show()

    r = await client.delete(f"{VENUES}/{show['venue_id']}", headers=auth(admin[1]))

    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "VENUE_IN_USE"
    assert "1 event" in r.json()["error"]["message"]
    # Still there — a refused delete must not half-happen.
    assert (await client.get(f"{VENUES}/{show['venue_id']}")).status_code == 200


async def test_only_an_admin_may_delete_a_venue(client, auth, make_user, make_show):
    show = await make_show()
    _, customer_token = await make_user(Role.CUSTOMER, "customer")

    r = await client.delete(f"{VENUES}/{show['venue_id']}", headers=auth(customer_token))
    assert r.status_code == 403


# ------------------------------------------------------------------- events


async def test_an_event_with_shows_but_no_bookings_can_be_deleted(client, auth, make_show):
    show = await make_show()

    r = await client.delete(f"{EVENTS}/{show['event_id']}", headers=auth(show["organiser_token"]))
    assert r.status_code == 204, r.text

    assert (await client.get(f"{EVENTS}/{show['event_id']}")).status_code == 404
    # The show went with it, not just the event row.
    assert (await client.get(f"/api/v1/shows/{show['show_id']}")).status_code == 404


async def test_an_event_with_a_booking_is_refused(client, auth, make_show, make_user):
    show = await make_show()
    _, customer_token = await make_user(Role.CUSTOMER, "customer")
    seat = show["seat_ids"][0]

    await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": [seat]},
        headers=auth(customer_token),
    )
    booked = await client.post(
        "/api/v1/bookings",
        json={"showId": show["show_id"], "seatIds": [seat]},
        headers=auth(customer_token),
    )
    assert booked.status_code == 201, booked.text

    r = await client.delete(f"{EVENTS}/{show['event_id']}", headers=auth(show["organiser_token"]))

    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "EVENT_HAS_BOOKINGS"
    assert "1 booking" in r.json()["error"]["message"]
    assert (await client.get(f"{EVENTS}/{show['event_id']}")).status_code == 200


async def test_a_cancelled_booking_still_blocks_deletion(client, auth, make_show, make_user):
    """
    Cancelling frees the seat; it does not erase that somebody paid. The ticket
    history is the reason this endpoint refuses at all.
    """
    show = await make_show()
    _, customer_token = await make_user(Role.CUSTOMER, "customer")
    seat = show["seat_ids"][0]

    await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": [seat]},
        headers=auth(customer_token),
    )
    booking = (
        await client.post(
            "/api/v1/bookings",
            json={"showId": show["show_id"], "seatIds": [seat]},
            headers=auth(customer_token),
        )
    ).json()["booking"]
    await client.post(f"/api/v1/bookings/{booking['id']}/cancel", headers=auth(customer_token))

    r = await client.delete(f"{EVENTS}/{show['event_id']}", headers=auth(show["organiser_token"]))
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "EVENT_HAS_BOOKINGS"


async def test_an_organiser_cannot_delete_another_organisers_event(
    client, auth, make_show, make_user
):
    show = await make_show()
    _, other_token = await make_user(Role.ORGANISER, "other-organiser")

    r = await client.delete(f"{EVENTS}/{show['event_id']}", headers=auth(other_token))
    assert r.status_code == 403


async def test_an_admin_may_delete_any_organisers_event(client, auth, make_show, make_user):
    show = await make_show()
    _, admin_token = await make_user(Role.ADMIN, "admin")

    r = await client.delete(f"{EVENTS}/{show['event_id']}", headers=auth(admin_token))
    assert r.status_code == 204, r.text


async def test_deleting_an_event_frees_the_venue_slot(client, auth, admin, make_show):
    """
    The exclusion constraint is partial on status, so a deleted show's row must
    actually be gone — otherwise the slot stays blocked by a phantom.
    """
    show = await make_show()
    await client.delete(f"{EVENTS}/{show['event_id']}", headers=auth(show["organiser_token"]))

    r = await client.delete(f"{VENUES}/{show['venue_id']}", headers=auth(admin[1]))
    assert r.status_code == 204, r.text
