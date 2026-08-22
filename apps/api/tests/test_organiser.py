from __future__ import annotations

from sqlalchemy import update

from ticket_api.db import Session
from ticket_api.models import Role, SeatCategory

BOOKINGS = "/api/v1/bookings"


def summary_url(event_id: str) -> str:
    return f"/api/v1/organiser/events/{event_id}/summary"


async def _book_all(client, auth, show, token):
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


async def test_summary_needs_more_than_a_role(client, auth, make_show, make_user):
    """Role gets you through the door; ownership is checked in the service."""
    show = await make_show()
    _, other_organiser = await make_user(Role.ORGANISER, "other")

    assert (await client.get(summary_url(show["event_id"]))).status_code == 401

    _, customer = await make_user()
    assert (
        await client.get(summary_url(show["event_id"]), headers=auth(customer))
    ).status_code == 403

    r = await client.get(summary_url(show["event_id"]), headers=auth(other_organiser))
    assert r.status_code == 403
    assert "another organiser" in r.json()["error"]["message"]


async def test_the_owner_sees_an_empty_summary_before_any_sales(client, auth, make_show):
    show = await make_show(seats=4, price="100")
    r = await client.get(summary_url(show["event_id"]), headers=auth(show["organiser_token"]))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["capacity"] == 4
    assert body["totals"]["seatsSold"] == 0
    assert body["totals"]["revenue"] == "0"
    assert body["totals"]["percentSold"] == 0


async def test_revenue_counts_confirmed_sales(client, auth, make_show, make_user):
    show = await make_show(seats=4, price="100")
    _, buyer = await make_user()
    await _book_all(client, auth, show, buyer)

    body = (
        await client.get(summary_url(show["event_id"]), headers=auth(show["organiser_token"]))
    ).json()

    assert body["totals"]["seatsSold"] == 4
    assert body["totals"]["revenue"] == "400"
    assert body["totals"]["percentSold"] == 100
    assert body["totals"]["bookings"] == 1
    assert body["categories"][0]["revenue"] == "400"
    assert body["shows"][0]["seatsSold"] == 4


async def test_cancelled_bookings_are_excluded_from_revenue(client, auth, make_show, make_user):
    show = await make_show(seats=2, price="100")
    _, buyer = await make_user()
    booking_id = await _book_all(client, auth, show, buyer)
    await client.post(f"{BOOKINGS}/{booking_id}/cancel", headers=auth(buyer))

    body = (
        await client.get(summary_url(show["event_id"]), headers=auth(show["organiser_token"]))
    ).json()

    assert body["totals"]["revenue"] == "0"
    assert body["totals"]["seatsSold"] == 0
    assert body["totals"]["cancelled"] == 1


async def test_revenue_uses_the_price_paid_not_the_current_price(
    client, auth, make_show, make_user
):
    """
    Those diverge the moment an organiser re-prices anything, and the number the
    customer actually paid is the one on the row.
    """
    show = await make_show(seats=2, price="100")
    _, buyer = await make_user()
    await _book_all(client, auth, show, buyer)

    async with Session() as session:
        await session.execute(
            update(SeatCategory).where(SeatCategory.id == show["category_id"]).values(price="500")
        )
        await session.commit()

    body = (
        await client.get(summary_url(show["event_id"]), headers=auth(show["organiser_token"]))
    ).json()

    assert body["totals"]["revenue"] == "200"  # what was paid
    assert body["categories"][0]["currentPrice"] == "500"  # what is charged now


async def test_waiting_customers_are_counted(client, auth, make_show, make_user):
    show = await make_show(seats=1, price="100")
    _, buyer = await make_user()
    await _book_all(client, auth, show, buyer)

    _, waiter = await make_user()
    await client.post(
        f"/api/v1/shows/{show['show_id']}/waitlist",
        json={"categoryId": show["category_id"]},
        headers=auth(waiter),
    )

    body = (
        await client.get(summary_url(show["event_id"]), headers=auth(show["organiser_token"]))
    ).json()
    assert body["totals"]["waiting"] == 1
    assert body["categories"][0]["waiting"] == 1


async def test_an_admin_can_read_any_summary(client, auth, make_show, make_user):
    show = await make_show()
    _, admin = await make_user(Role.ADMIN)
    assert (await client.get(summary_url(show["event_id"]), headers=auth(admin))).status_code == 200


async def test_unknown_event_404s(client, auth, make_user):
    _, organiser = await make_user(Role.ORGANISER)
    r = await client.get(summary_url("nope"), headers=auth(organiser))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "EVENT_NOT_FOUND"
