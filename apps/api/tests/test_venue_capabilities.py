from __future__ import annotations

import pytest

from ticket_api.models import Role

VENUES = "/api/v1/venues"


@pytest.fixture
async def admin(make_user):
    return await make_user(Role.ADMIN, "admin")


async def test_a_venue_defaults_to_end_stage_allowing_both_types(client, auth, admin):
    r = await client.post(VENUES, json={"name": "Default", "address": "x"}, headers=auth(admin[1]))
    assert r.status_code == 201, r.text
    venue = r.json()["venue"]
    assert venue["stageLayout"] == "END_STAGE"
    assert sorted(venue["allowedEventTypes"]) == ["CONCERT", "MOVIE"]
    assert venue["turnaroundMinutes"] == 15


async def test_an_explicit_centre_stage_concert_venue_is_accepted(client, auth, admin):
    r = await client.post(
        VENUES,
        json={
            "name": "Round",
            "address": "x",
            "stageLayout": "CENTRE_STAGE",
            "allowedEventTypes": ["CONCERT"],
            "turnaroundMinutes": 45,
        },
        headers=auth(admin[1]),
    )
    assert r.status_code == 201, r.text
    venue = r.json()["venue"]
    assert venue["stageLayout"] == "CENTRE_STAGE"
    assert venue["turnaroundMinutes"] == 45


async def test_a_centre_stage_venue_may_not_allow_movies(client, auth, admin):
    """Nobody projects a film in the round."""
    r = await client.post(
        VENUES,
        json={
            "name": "Absurd",
            "address": "x",
            "stageLayout": "CENTRE_STAGE",
            "allowedEventTypes": ["MOVIE", "CONCERT"],
        },
        headers=auth(admin[1]),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "CENTRE_STAGE_CANNOT_SHOW_MOVIES"


async def test_a_venue_must_allow_at_least_one_event_type(client, auth, admin):
    r = await client.post(
        VENUES,
        json={"name": "Nothing", "address": "x", "allowedEventTypes": []},
        headers=auth(admin[1]),
    )
    assert r.status_code == 400


async def test_patching_one_half_cannot_produce_an_incoherent_venue(client, auth, admin):
    """
    An END_STAGE venue allowing MOVIE, then flipped to CENTRE_STAGE, must be
    refused — the update has to check the merged result, not just its own body.
    """
    venue = (
        await client.post(VENUES, json={"name": "Flip", "address": "x"}, headers=auth(admin[1]))
    ).json()["venue"]["id"]

    r = await client.patch(
        f"{VENUES}/{venue}", json={"stageLayout": "CENTRE_STAGE"}, headers=auth(admin[1])
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "CENTRE_STAGE_CANNOT_SHOW_MOVIES"


async def test_turnaround_is_bounded(client, auth, admin):
    for minutes in (-1, 241):
        r = await client.post(
            VENUES,
            json={"name": f"T{minutes}", "address": "x", "turnaroundMinutes": minutes},
            headers=auth(admin[1]),
        )
        assert r.status_code == 400, minutes


async def test_capabilities_appear_on_read(client, auth, admin):
    venue = (
        await client.post(
            VENUES,
            json={
                "name": "Readable",
                "address": "x",
                "stageLayout": "CENTRE_STAGE",
                "allowedEventTypes": ["CONCERT"],
            },
            headers=auth(admin[1]),
        )
    ).json()["venue"]["id"]

    detail = (await client.get(f"{VENUES}/{venue}")).json()["venue"]
    assert detail["stageLayout"] == "CENTRE_STAGE"
    assert detail["allowedEventTypes"] == ["CONCERT"]

    listed = next(v for v in (await client.get(VENUES)).json()["venues"] if v["id"] == venue)
    assert listed["stageLayout"] == "CENTRE_STAGE"


import math  # noqa: E402 - grouped with the radial tests below


async def _make_venue(client, auth, admin, **caps) -> str:
    body = {"name": f"V{len(caps)}", "address": "x", **caps}
    r = await client.post(VENUES, json=body, headers=auth(admin[1]))
    assert r.status_code == 201, r.text
    return r.json()["venue"]["id"]


async def test_an_end_stage_venue_produces_a_grid(client, auth, admin):
    venue = await _make_venue(client, auth, admin)
    r = await client.post(
        f"{VENUES}/{venue}/seats",
        json={"section": "Stalls", "rows": 2, "seatsPerRow": 4},
        headers=auth(admin[1]),
    )
    assert r.status_code == 201, r.text

    seats = (await client.get(f"{VENUES}/{venue}")).json()["venue"]["seats"]
    assert len(seats) == 8
    # A grid has exactly as many distinct posY values as it has rows.
    assert len({s["posY"] for s in seats}) == 2


async def test_a_centre_stage_venue_places_every_seat_on_its_row_radius(client, auth, admin):
    venue = await _make_venue(
        client, auth, admin, stageLayout="CENTRE_STAGE", allowedEventTypes=["CONCERT"]
    )
    r = await client.post(
        f"{VENUES}/{venue}/seats",
        json={
            "section": "Ring A",
            "rows": 2,
            "seatsPerRow": 8,
            "arcStartDegrees": 0,
            "arcSpanDegrees": 360,
        },
        headers=auth(admin[1]),
    )
    assert r.status_code == 201, r.text

    seats = (await client.get(f"{VENUES}/{venue}")).json()["venue"]["seats"]
    assert len(seats) == 16

    radii = {round(math.hypot(s["posX"], s["posY"]), 6) for s in seats}
    # Two rows means two distinct radii...
    assert len(radii) == 2
    # ...and a ring is not a grid: many distinct posY values, not two.
    assert len({round(s["posY"], 6) for s in seats}) > 2


async def test_a_second_centre_stage_block_sits_outside_the_first(client, auth, admin):
    venue = await _make_venue(
        client, auth, admin, stageLayout="CENTRE_STAGE", allowedEventTypes=["CONCERT"]
    )
    for section in ("Inner", "Outer"):
        r = await client.post(
            f"{VENUES}/{venue}/seats",
            json={"section": section, "rows": 1, "seatsPerRow": 6},
            headers=auth(admin[1]),
        )
        assert r.status_code == 201, r.text

    seats = (await client.get(f"{VENUES}/{venue}")).json()["venue"]["seats"]

    def radius(section: str) -> float:
        s = next(x for x in seats if x["section"] == section)
        return math.hypot(s["posX"], s["posY"])

    assert radius("Outer") > radius("Inner")


async def test_the_arc_fields_are_ignored_for_an_end_stage_venue(client, auth, admin):
    """They apply only to CENTRE_STAGE; sending them must not corrupt a grid."""
    venue = await _make_venue(client, auth, admin)
    await client.post(
        f"{VENUES}/{venue}/seats",
        json={
            "section": "Stalls",
            "rows": 1,
            "seatsPerRow": 4,
            "arcStartDegrees": 90,
            "arcSpanDegrees": 45,
        },
        headers=auth(admin[1]),
    )
    seats = (await client.get(f"{VENUES}/{venue}")).json()["venue"]["seats"]
    assert sum(s["posX"] for s in seats) == 0
    assert len({s["posY"] for s in seats}) == 1
