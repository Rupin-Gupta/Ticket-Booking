"""
Argon2 hashes written before the Python port must still verify.

The Node `argon2` package encodes its parameters as `m,p,t`; argon2-cffi requires
`m,t,p` and raises "Decoding failed" on anything else. Because verify_password
swallows exceptions into False, that surfaced as "Incorrect email or password"
for every account created before the port — including the owner's own.
"""

from __future__ import annotations

import pytest

from ticket_api.security import _normalise_encoding, hash_password, verify_password

# A real hash lifted from the production database, written by the Node argon2
# package. The password is "password123". Kept verbatim rather than generated,
# because the whole point is the exact byte layout another library produced.
LEGACY_HASH = (
    "$argon2id$v=19$m=65536,p=4,t=3"
    "$+NDOnFtvwx53KWNfOYim1w$pqPqDGHRa6fwtxH53bn7rxlatCTC8jD9PCnIdBMD4oU"
)
LEGACY_PASSWORD = "password123"


def test_a_node_written_hash_verifies():
    assert verify_password(LEGACY_HASH, LEGACY_PASSWORD) is True


def test_a_node_written_hash_still_rejects_the_wrong_password():
    """The shim must not turn into 'accept anything'."""
    assert verify_password(LEGACY_HASH, "not-the-password") is False


def test_normalising_reorders_only_the_parameters():
    fixed = _normalise_encoding(LEGACY_HASH)
    assert "m=65536,t=3,p=4" in fixed
    # Salt and digest are untouched — only the parameter segment moves.
    assert fixed.split("$")[4:] == LEGACY_HASH.split("$")[4:]
    assert fixed.split("$")[1:3] == LEGACY_HASH.split("$")[1:3]


def test_normalising_is_a_no_op_on_our_own_hashes():
    ours = hash_password("something")
    assert _normalise_encoding(ours) == ours
    assert verify_password(ours, "something") is True


@pytest.mark.parametrize(
    "junk",
    ["", "not-a-hash", "$argon2id$", "$argon2id$v=19$m=65536$salt$digest", "$$$$"],
)
def test_malformed_hashes_are_rejected_not_crashed(junk):
    """A malformed hash in the database is a failed login, never a 500."""
    assert verify_password(junk, "anything") is False


async def test_a_legacy_user_can_log_in_through_the_api(client, password):
    """End to end: the shim has to work through the real login path."""
    from sqlalchemy import update

    from ticket_api.db import Session
    from ticket_api.models import User

    email = "legacy@example.test"
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "name": "Legacy"},
    )
    assert r.status_code == 201

    # Rewrite their hash into the Node encoding, as if it predated the port.
    async with Session() as session:
        await session.execute(
            update(User).where(User.email == email).values(password_hash=LEGACY_HASH)
        )
        await session.commit()

    ok = await client.post("/api/v1/auth/login", json={"email": email, "password": LEGACY_PASSWORD})
    assert ok.status_code == 200, ok.text
    assert ok.json()["user"]["email"] == email

    bad = await client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
    assert bad.status_code == 401
