from __future__ import annotations

from datetime import timedelta

import pytest

from ticket_api.models import Role, utcnow

VENUES = "/api/v1/venues"
EVENTS = "/api/v1/events"


@pytest.fixture
async def admin(make_user):
    return await make_user(Role.ADMIN, "admin")


@pytest.fixture
async def organiser(make_user):
    return await make_user(Role.ORGANISER, "organiser")


# ------------------------------------------------------------------- venues


async def test_only_an_admin_can_create_a_venue(client, auth, admin, organiser):
    payload = {"name": "Hall", "address": "1 Test Street"}

    assert (await client.post(VENUES, json=payload)).status_code == 401
    assert (await client.post(VENUES, json=payload, headers=auth(organiser[1]))).status_code == 403

    r = await client.post(VENUES, json=payload, headers=auth(admin[1]))
    assert r.status_code == 201, r.text
    assert r.json()["venue"]["name"] == "Hall"


async def test_seat_blocks_stack_instead_of_overlapping(client, auth, admin):
    venue = (
        await client.post(VENUES, json={"name": "Hall", "address": "x"}, headers=auth(admin[1]))
    ).json()["venue"]["id"]

    first = await client.post(
        f"{VENUES}/{venue}/seats",
        json={"section": "Front", "rows": 2, "seatsPerRow": 5},
        headers=auth(admin[1]),
    )
    second = await client.post(
        f"{VENUES}/{venue}/seats",
        json={"section": "Back", "rows": 3, "seatsPerRow": 4},
        headers=auth(admin[1]),
    )

    assert first.json()["created"] == 10
    assert second.json()["created"] == 12
    assert second.json()["startY"] > first.json()["startY"]


async def test_rows_are_centred_on_zero(client, auth, admin):
    """Rows of different widths have to stay aligned in the seat map."""
    venue = (
        await client.post(VENUES, json={"name": "Hall", "address": "x"}, headers=auth(admin[1]))
    ).json()["venue"]["id"]
    await client.post(
        f"{VENUES}/{venue}/seats",
        json={"section": "Main", "rows": 1, "seatsPerRow": 4},
        headers=auth(admin[1]),
    )

    seats = (await client.get(f"{VENUES}/{venue}")).json()["venue"]["seats"]
    assert sum(s["posX"] for s in seats) == 0


async def test_re_adding_the_same_block_conflicts(client, auth, admin):
    venue = (
        await client.post(VENUES, json={"name": "Hall", "address": "x"}, headers=auth(admin[1]))
    ).json()["venue"]["id"]
    block = {"section": "Main", "rows": 1, "seatsPerRow": 2}

    await client.post(f"{VENUES}/{venue}/seats", json=block, headers=auth(admin[1]))
    r = await client.post(f"{VENUES}/{venue}/seats", json=block, headers=auth(admin[1]))

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "SEATS_ALREADY_EXIST"


async def test_venue_list_keeps_the_count_shape_the_frontend_reads(client, auth, admin):
    venue = (
        await client.post(VENUES, json={"name": "Hall", "address": "x"}, headers=auth(admin[1]))
    ).json()["venue"]["id"]
    await client.post(
        f"{VENUES}/{venue}/seats",
        json={"section": "Main", "rows": 2, "seatsPerRow": 3},
        headers=auth(admin[1]),
    )

    listed = (await client.get(VENUES)).json()["venues"]
    assert listed[0]["_count"]["seats"] == 6


async def test_unknown_venue_404s(client):
    r = await client.get(f"{VENUES}/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "VENUE_NOT_FOUND"


# ------------------------------------------------------------------- events


async def test_organiser_creates_an_event(client, auth, organiser, make_show):
    show = await make_show()
    r = await client.post(
        EVENTS,
        json={"venueId": show["venue_id"], "title": "Gig", "type": "CONCERT"},
        headers=auth(organiser[1]),
    )
    assert r.status_code == 201, r.text
    assert r.json()["event"]["title"] == "Gig"


async def test_event_on_an_unknown_venue_is_refused(client, auth, organiser):
    r = await client.post(
        EVENTS,
        json={"venueId": "nope", "title": "Gig", "type": "CONCERT"},
        headers=auth(organiser[1]),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VENUE_NOT_FOUND"


async def test_another_organiser_cannot_edit_your_event(client, auth, make_show, make_user):
    show = await make_show()
    _, intruder = await make_user(Role.ORGANISER, "intruder")

    r = await client.patch(
        f"{EVENTS}/{show['event_id']}", json={"title": "Hijacked"}, headers=auth(intruder)
    )
    assert r.status_code == 403


async def test_admin_can_edit_anyone_s_event(client, auth, make_show, admin):
    """An admin exists to fix things."""
    show = await make_show()
    r = await client.patch(
        f"{EVENTS}/{show['event_id']}", json={"title": "Corrected"}, headers=auth(admin[1])
    )
    assert r.status_code == 200
    assert r.json()["event"]["title"] == "Corrected"


async def test_mine_lists_only_your_own_events(client, auth, make_show, make_user):
    show = await make_show()
    _, other = await make_user(Role.ORGANISER, "other")

    ours = (await client.get(f"{EVENTS}/mine", headers=auth(show["organiser_token"]))).json()
    theirs = (await client.get(f"{EVENTS}/mine", headers=auth(other))).json()

    assert [e["id"] for e in ours["events"]] == [show["event_id"]]
    assert theirs["events"] == []


async def test_mine_is_not_matched_as_an_event_id(client, auth, organiser):
    """Route ordering: "/mine" must win over "/{event_id}"."""
    r = await client.get(f"{EVENTS}/mine", headers=auth(organiser[1]))
    assert r.status_code == 200
    assert "events" in r.json()


# --------------------------------------------------------------- categories


async def test_a_section_cannot_be_priced_twice(client, auth, make_show):
    show = await make_show()
    r = await client.post(
        f"{EVENTS}/{show['event_id']}/categories",
        json={"name": "Second", "price": "1", "sections": ["Main"]},
        headers=auth(show["organiser_token"]),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "SECTION_ALREADY_PRICED"


async def test_a_category_cannot_claim_a_section_the_venue_lacks(client, auth, make_show):
    show = await make_show()
    r = await client.post(
        f"{EVENTS}/{show['event_id']}/categories",
        json={"name": "Ghost", "price": "1", "sections": ["Nowhere"]},
        headers=auth(show["organiser_token"]),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "UNKNOWN_SECTION"


@pytest.mark.parametrize("price", ["-1", "abc", "NaN"])
async def test_prices_must_be_non_negative_numbers(client, auth, make_show, price):
    show = await make_show()
    r = await client.post(
        f"{EVENTS}/{show['event_id']}/categories",
        json={"name": "Odd", "price": price, "sections": ["Main"]},
        headers=auth(show["organiser_token"]),
    )
    assert r.status_code == 400


async def test_price_is_rendered_the_way_prisma_did(client, auth, admin, organiser):
    """
    decimal.js — which is what Prisma.Decimal was — strips trailing zeros:
    "250.50" comes back as "250.5", and "450.00" as "450".
    """
    venue = (
        await client.post(VENUES, json={"name": "H", "address": "x"}, headers=auth(admin[1]))
    ).json()["venue"]["id"]
    await client.post(
        f"{VENUES}/{venue}/seats",
        json={"section": "Main", "rows": 1, "seatsPerRow": 1},
        headers=auth(admin[1]),
    )
    event = (
        await client.post(
            EVENTS,
            json={"venueId": venue, "title": "E", "type": "MOVIE"},
            headers=auth(organiser[1]),
        )
    ).json()["event"]["id"]

    r = await client.post(
        f"{EVENTS}/{event}/categories",
        json={"name": "Main", "price": "250.50", "sections": ["Main"]},
        headers=auth(organiser[1]),
    )
    assert r.json()["category"]["price"] == "250.5"


# -------------------------------------------------------------------- shows


async def test_a_show_cannot_be_created_before_every_section_is_priced(
    client, auth, admin, organiser
):
    venue = (
        await client.post(VENUES, json={"name": "H", "address": "x"}, headers=auth(admin[1]))
    ).json()["venue"]["id"]
    await client.post(
        f"{VENUES}/{venue}/seats",
        json={"section": "Priced", "rows": 1, "seatsPerRow": 1},
        headers=auth(admin[1]),
    )
    await client.post(
        f"{VENUES}/{venue}/seats",
        json={"section": "Unpriced", "rows": 1, "seatsPerRow": 1},
        headers=auth(admin[1]),
    )
    event = (
        await client.post(
            EVENTS,
            json={"venueId": venue, "title": "E", "type": "MOVIE"},
            headers=auth(organiser[1]),
        )
    ).json()["event"]["id"]
    await client.post(
        f"{EVENTS}/{event}/categories",
        json={"name": "P", "price": "10", "sections": ["Priced"]},
        headers=auth(organiser[1]),
    )

    r = await client.post(
        f"{EVENTS}/{event}/shows",
        json={"startsAt": (utcnow() + timedelta(days=5)).isoformat()},
        headers=auth(organiser[1]),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "SECTION_NOT_PRICED"
    # A half-built show is worse than none: the whole thing rolls back.
    assert (await client.get(f"{EVENTS}/{event}")).json()["event"]["shows"] == []


async def test_creating_a_show_instantiates_one_seat_per_venue_seat(client, auth, make_show):
    show = await make_show(seats=4)
    r = await client.post(
        f"{EVENTS}/{show['event_id']}/shows",
        json={"startsAt": (utcnow() + timedelta(days=6)).isoformat()},
        headers=auth(show["organiser_token"]),
    )
    assert r.status_code == 201, r.text
    assert r.json()["show"]["seatCount"] == 4
    assert r.json()["show"]["startsAt"].endswith("Z")


async def test_a_show_must_start_in_the_future(client, auth, make_show):
    show = await make_show()
    r = await client.post(
        f"{EVENTS}/{show['event_id']}/shows",
        json={"startsAt": (utcnow() - timedelta(days=1)).isoformat()},
        headers=auth(show["organiser_token"]),
    )
    assert r.status_code == 400


async def test_show_detail_carries_venue_and_seat_count(client, make_show):
    show = await make_show(seats=3)
    r = await client.get(f"/api/v1/shows/{show['show_id']}")
    assert r.status_code == 200
    body = r.json()["show"]
    assert body["_count"]["showSeats"] == 3
    assert body["event"]["venue"]["id"] == show["venue_id"]
