from __future__ import annotations

import jwt
import pytest
from sqlalchemy import select

from ticket_api.db import Session
from ticket_api.models import Role, User

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"


async def test_register_returns_a_public_user_and_a_token(client, password):
    r = await client.post(
        REGISTER, json={"email": "Ada@Example.test", "password": password, "name": "  Ada  "}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["accessToken"]
    assert body["user"]["role"] == "CUSTOMER"
    # Normalised on the way in, so "Ada@" and "ada@" are one account.
    assert body["user"]["email"] == "ada@example.test"
    assert body["user"]["name"] == "Ada"


async def test_register_never_returns_the_password_hash(client, password):
    r = await client.post(
        REGISTER, json={"email": "a@example.test", "password": password, "name": "A"}
    )
    assert "passwordHash" not in r.text
    assert "password_hash" not in r.text
    assert password not in r.text


async def test_client_supplied_role_is_ignored(client, password):
    """RULE 7 — mass assignment is a one-line privilege escalation otherwise."""
    r = await client.post(
        REGISTER,
        json={
            "email": "sneaky@example.test",
            "password": password,
            "name": "Sneaky",
            "role": "ADMIN",
        },
    )
    assert r.status_code == 201
    assert r.json()["user"]["role"] == "CUSTOMER"

    async with Session() as session:
        user = (
            (await session.execute(select(User).where(User.email == "sneaky@example.test")))
            .scalars()
            .one()
        )
    assert user.role is Role.CUSTOMER


async def test_duplicate_email_conflicts(client, password):
    payload = {"email": "dup@example.test", "password": password, "name": "Dup"}
    assert (await client.post(REGISTER, json=payload)).status_code == 201
    r = await client.post(REGISTER, json=payload)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EMAIL_TAKEN"


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": "longenough1", "name": "X"},
        {"email": "a@example.test", "password": "short", "name": "X"},
        {"email": "a@example.test", "password": "longenough1", "name": "   "},
        {"password": "longenough1", "name": "X"},
    ],
)
async def test_registration_validation(client, payload):
    r = await client.post(REGISTER, json=payload)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_login_succeeds_and_returns_a_token(client, password):
    await client.post(REGISTER, json={"email": "l@example.test", "password": password, "name": "L"})
    r = await client.post(LOGIN, json={"email": "l@example.test", "password": password})
    assert r.status_code == 200, r.text
    assert r.json()["accessToken"]


async def test_wrong_password_and_unknown_email_are_indistinguishable(client, password):
    """Distinguishing them tells an attacker which addresses have accounts."""
    await client.post(
        REGISTER, json={"email": "real@example.test", "password": password, "name": "R"}
    )
    wrong = await client.post(LOGIN, json={"email": "real@example.test", "password": "nope"})
    missing = await client.post(LOGIN, json={"email": "ghost@example.test", "password": "nope"})

    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()


async def test_me_returns_the_signed_in_user(client, password, auth):
    created = await client.post(
        REGISTER, json={"email": "me@example.test", "password": password, "name": "Me"}
    )
    token = created.json()["accessToken"]

    r = await client.get(ME, headers=auth(token))
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "me@example.test"


@pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer garbage", "Basic abc"])
async def test_me_rejects_bad_authorisation(client, header):
    headers = {} if header is None else {"Authorization": header}
    r = await client.get(ME, headers=headers)
    assert r.status_code == 401


async def test_alg_none_token_is_rejected(client, auth):
    """
    RULE 11 — the algorithm is pinned on verify, not inferred from the header.

    An attacker controls that header; a verifier that trusts it accepts a token
    with no signature at all.
    """
    forged = jwt.encode({"sub": "whoever", "role": "ADMIN"}, "", algorithm="none")
    r = await client.get(ME, headers=auth(forged))
    assert r.status_code == 401


async def test_token_signed_with_another_secret_is_rejected(client, auth):
    forged = jwt.encode({"sub": "whoever", "role": "ADMIN"}, "a" * 40, algorithm="HS256")
    r = await client.get(ME, headers=auth(forged))
    assert r.status_code == 401


async def test_me_401s_when_the_account_is_gone(client, password, auth, make_user):
    """A JWT cannot be revoked before it expires, so the account is re-checked."""
    user_id, token = await make_user()
    async with Session() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalars().one()
        await session.delete(user)
        await session.commit()

    r = await client.get(ME, headers=auth(token))
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "Account no longer exists."


async def test_unknown_route_has_the_standard_error_shape(client):
    r = await client.get("/api/v1/nowhere")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ROUTE_NOT_FOUND"


async def test_health_reports_the_database(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["database"] == "up"
    assert body["env"] == "test"
