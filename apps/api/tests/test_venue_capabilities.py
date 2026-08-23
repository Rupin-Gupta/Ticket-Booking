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
