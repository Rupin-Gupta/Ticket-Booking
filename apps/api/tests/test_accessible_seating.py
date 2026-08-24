from __future__ import annotations

import pytest
from sqlalchemy import select

from ticket_api.db import Session
from ticket_api.models import Role, Seat, SeatAccessType, SeatStatus, ShowSeat

VENUES = "/api/v1/venues"
SHOWS = "/api/v1/shows"


@pytest.fixture
async def admin(make_user):
    return await make_user(Role.ADMIN, "admin")


@pytest.fixture
async def paired_show(client, auth, admin, make_show):
    """
    A show whose venue has one wheelchair space and its companion.

    Built by adding an accessible block to the fixture's venue, then scheduling
    a second show so the new seats get ShowSeat rows.
    """
    show = await make_show(seats=2, section="Main", price="100")

    # Its own section, not "Main": the builder numbers every block from A1, so
    # adding to a section that already has seats collides with them by design.
    added = await client.post(
        f"{VENUES}/{show['venue_id']}/seats",
        json={"section": "Access", "rows": 1, "seatsPerRow": 1, "accessType": "WHEELCHAIR_SPACE"},
        headers=auth(admin[1]),
    )
    assert added.status_code == 201, added.text
    # One space plus its generated companion.
    assert added.json()["created"] == 2

    # A show refuses to generate while any section is unpriced.
    priced = await client.post(
        f"/api/v1/events/{show['event_id']}/categories",
        json={"name": "Accessible", "price": "80", "sections": ["Access"]},
        headers=auth(show["organiser_token"]),
    )
    assert priced.status_code == 201, priced.text

    detail = (await client.get(f"{SHOWS}/{show['show_id']}")).json()["show"]
    later = await client.post(
        f"/api/v1/events/{show['event_id']}/shows",
        json={
            "startsAt": detail["startsAt"].replace("T09:", "T15:"),
            "durationMinutes": 60,
        },
        headers=auth(show["organiser_token"]),
    )
    assert later.status_code == 201, later.text
    new_show_id = later.json()["show"]["id"]

    async with Session() as session:
        rows = (
            await session.execute(
                select(ShowSeat.id, Seat.access_type)
                .join(Seat, Seat.id == ShowSeat.seat_id)
                .where(ShowSeat.show_id == new_show_id)
            )
        ).all()

    space = next(r.id for r in rows if r.access_type is SeatAccessType.WHEELCHAIR_SPACE)
    companion = next(r.id for r in rows if r.access_type is SeatAccessType.COMPANION)
    # `**show` first: spread last would let make_show's own show_id overwrite
    # the one built here, and every test would then hold this show's seats
    # against the wrong show.
    return {**show, "show_id": new_show_id, "space": space, "companion": companion}


async def test_the_builder_links_each_companion_to_its_space(client, auth, admin, make_show):
    show = await make_show(seats=1)
    await client.post(
        f"{VENUES}/{show['venue_id']}/seats",
        json={"section": "Access", "rows": 2, "seatsPerRow": 1, "accessType": "WHEELCHAIR_SPACE"},
        headers=auth(admin[1]),
    )

    async with Session() as session:
        companions = (
            (
                await session.execute(
                    select(Seat).where(
                        Seat.venue_id == show["venue_id"],
                        Seat.access_type == SeatAccessType.COMPANION,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(companions) == 2
    assert all(c.companion_of_id is not None for c in companions), "a companion with no space"


async def test_holding_a_wheelchair_space_also_holds_its_companion(
    client, auth, paired_show, make_user
):
    _, customer = await make_user(Role.CUSTOMER, "customer")

    r = await client.post(
        f"{SHOWS}/{paired_show['show_id']}/holds",
        json={"seatIds": [paired_show["space"]]},
        headers=auth(customer),
    )

    assert r.status_code == 201, r.text
    assert sorted(r.json()["seatIds"]) == sorted([paired_show["space"], paired_show["companion"]])


async def test_the_companion_cannot_be_held_alone(client, auth, paired_show, make_user):
    """
    The invariant the feature exists for: nobody who needs assistance can be
    seated apart from the person providing it — including by asking for only
    half the pair.
    """
    _, customer = await make_user(Role.CUSTOMER, "customer")

    r = await client.post(
        f"{SHOWS}/{paired_show['show_id']}/holds",
        json={"seatIds": [paired_show["companion"]]},
        headers=auth(customer),
    )

    assert r.status_code == 201, r.text
    assert paired_show["space"] in r.json()["seatIds"], "the companion was held without its space"


async def test_booking_a_pair_produces_one_booking_with_both_seats(
    client, auth, paired_show, make_user
):
    _, customer = await make_user(Role.CUSTOMER, "customer")
    await client.post(
        f"{SHOWS}/{paired_show['show_id']}/holds",
        json={"seatIds": [paired_show["space"]]},
        headers=auth(customer),
    )

    booked = await client.post(
        "/api/v1/bookings",
        json={"showId": paired_show["show_id"], "seatIds": [paired_show["space"]]},
        headers=auth(customer),
    )

    assert booked.status_code == 201, booked.text
    assert len(booked.json()["booking"]["seats"]) == 2


async def test_cancelling_frees_both_halves(client, auth, paired_show, make_user):
    _, customer = await make_user(Role.CUSTOMER, "customer")
    await client.post(
        f"{SHOWS}/{paired_show['show_id']}/holds",
        json={"seatIds": [paired_show["space"]]},
        headers=auth(customer),
    )
    booking = (
        await client.post(
            "/api/v1/bookings",
            json={"showId": paired_show["show_id"], "seatIds": [paired_show["space"]]},
            headers=auth(customer),
        )
    ).json()["booking"]

    await client.post(f"/api/v1/bookings/{booking['id']}/cancel", headers=auth(customer))

    async with Session() as session:
        statuses = (
            (
                await session.execute(
                    select(ShowSeat.status).where(
                        ShowSeat.id.in_([paired_show["space"], paired_show["companion"]])
                    )
                )
            )
            .scalars()
            .all()
        )
    assert set(statuses) == {SeatStatus.AVAILABLE}, "a half-freed pair"


async def test_another_customer_racing_for_the_companion_alone_is_refused(
    client, auth, paired_show, make_user
):
    _, first = await make_user(Role.CUSTOMER, "first")
    _, second = await make_user(Role.CUSTOMER, "second")

    await client.post(
        f"{SHOWS}/{paired_show['show_id']}/holds",
        json={"seatIds": [paired_show["space"]]},
        headers=auth(first),
    )

    r = await client.post(
        f"{SHOWS}/{paired_show['show_id']}/holds",
        json={"seatIds": [paired_show["companion"]]},
        headers=auth(second),
    )
    assert r.status_code == 409, r.text


async def test_the_seat_map_names_both_halves_of_a_pair(client, paired_show):
    seats = (await client.get(f"{SHOWS}/{paired_show['show_id']}/seats")).json()["seats"]
    by_id = {s["id"]: s for s in seats}

    space = by_id[paired_show["space"]]
    companion = by_id[paired_show["companion"]]

    assert space["accessType"] == "WHEELCHAIR_SPACE"
    assert companion["accessType"] == "COMPANION"
    assert space["pairedWith"] == companion["id"]
    assert companion["pairedWith"] == space["id"]


async def test_expansion_does_not_break_the_race(client, auth, admin, paired_show):
    """Expansion happens before the lock, so the sorted lock set is unchanged."""
    r = await client.post(
        "/api/v1/lab/race",
        json={"showId": paired_show["show_id"], "seatId": paired_show["space"], "attempts": 20},
        headers=auth(admin[1]),
    )
    race = r.json()["race"]
    assert race["outcome"]["won"] == 1
    assert race["outcome"]["errors"] == 0
