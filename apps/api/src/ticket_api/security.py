"""
Password hashing and access tokens.

Both are places where a plausible-looking default is wrong, so both pin their
parameters explicitly rather than trusting the library.
"""

from __future__ import annotations

import contextlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import Argon2Error, InvalidHashError

from .config import require_env, settings
from .errors import ApiError

# ------------------------------------------------------------------ passwords

# InvalidHashError is NOT a subclass of Argon2Error, so both have to be named.
# Catching only Argon2Error let a malformed hash in the database raise straight
# through as a 500 instead of a failed login — the property the retired
# TypeScript API explicitly guarded, and which this port lost until a test
# caught it.
_VERIFY_ERRORS = (Argon2Error, InvalidHashError)

# Argon2id — OWASP's first choice in the Password Storage Cheat Sheet, with
# bcrypt listed only as the legacy fallback.
#
# The type is pinned rather than left to the library default so a dependency
# bump cannot silently move us onto argon2i or argon2d. The cost parameters are
# argon2-cffi's defaults and happen to match the Node `argon2` package's exactly
# (m=64MiB, t=3, p=4), both above the OWASP floor of m=19MiB, t=2, p=1.
#
# Matching parameters is NOT the same as a readable hash, which I originally got
# wrong: Node encodes them as `m,p,t` and argon2-cffi only decodes `m,t,p`, so
# every pre-port hash failed to parse and every pre-port account was locked out.
# `_normalise_encoding` below is what actually makes them verify.
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    type=Type.ID,
)


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


# Wrong-but-well-formed hash, used to burn the same CPU on a login for an email
# that does not exist as on one that does. Without it, "no such user" returns in
# microseconds while a real user costs ~50ms, and that difference is a working
# account-enumeration oracle.
#
# Generated once at import rather than hard-coded, so it always matches the cost
# parameters above.
_DECOY_HASH = _hasher.hash("a password nobody has: " + secrets.token_hex(16))


def _normalise_encoding(encoded: str) -> str:
    """
    Reorder Argon2 parameters to the order argon2-cffi's decoder accepts.

    The Node `argon2` package that wrote every pre-port hash encodes them as
    `m=...,p=...,t=...`; argon2-cffi requires `m=...,t=...,p=...` and raises
    "Decoding failed" otherwise. Same algorithm, same cost parameters, same
    salt and digest — only the order of three key/value pairs differs.

    This mattered more than it sounds: `verify_password` catches every
    exception and returns False, so a hash that could not be *parsed* was
    indistinguishable from a wrong password, and every account created before
    the port silently stopped being able to log in.

    A no-op on hashes this application wrote, so it is safe to run on all of
    them. ponytail: no transparent rehash on login. It would let this shim be
    deleted eventually, but it puts a database write on the login path, and a
    failed write there must not fail an otherwise valid login. Ten lines that
    never run for new users is the cheaper trade.
    """
    parts = encoded.split("$")
    if len(parts) < 4 or "," not in parts[3]:
        return encoded
    try:
        params = dict(kv.split("=", 1) for kv in parts[3].split(","))
    except ValueError:
        return encoded  # not a shape we recognise; let the verifier reject it
    if set(params) != {"m", "t", "p"}:
        return encoded
    parts[3] = f"m={params['m']},t={params['t']},p={params['p']}"
    return "$".join(parts)


def verify_password(stored_hash: str | None, plain: str) -> bool:
    """Constant-ish time regardless of whether the account exists."""
    if stored_hash is not None:
        stored_hash = _normalise_encoding(stored_hash)
    if stored_hash is None:
        # The verify still runs; only its (guaranteed) failure is discarded.
        # Burning the CPU is the entire point — skipping it would restore the
        # timing oracle this branch exists to close.
        with contextlib.suppress(*_VERIFY_ERRORS):
            _hasher.verify(_DECOY_HASH, plain)
        return False
    try:
        return _hasher.verify(stored_hash, plain)
    except _VERIFY_ERRORS:
        # Wrong password, or a malformed hash in the database. Both are a failed
        # login, never a 500.
        return False


# --------------------------------------------------------------------- tokens

ALGORITHM: Literal["HS256"] = "HS256"


class TokenPayload(TypedDict):
    sub: str
    role: str


_DURATION = re.compile(r"^(\d+)\s*([smhd])?$")


def _expires_in_seconds(spec: str) -> int:
    """
    Parse jsonwebtoken-style durations ("15m", "1h", "7d", "3600").

    Kept compatible on purpose: JWT_EXPIRES_IN is already set to `15m` in every
    deployed environment, and quietly reinterpreting that as 15 seconds would be
    a silent, security-relevant change.
    """
    match = _DURATION.match(spec.strip())
    if not match:
        raise RuntimeError(f"JWT_EXPIRES_IN is not a valid duration: {spec!r}")
    amount = int(match.group(1))
    return amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2) or "s"]


def sign_access_token(payload: TokenPayload) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            **payload,
            "iat": now,
            "exp": now + timedelta(seconds=_expires_in_seconds(settings.JWT_EXPIRES_IN)),
        },
        require_env("JWT_SECRET"),
        algorithm=ALGORITHM,
    )


def verify_access_token(token: str) -> TokenPayload:
    """
    HS256 is pinned explicitly on BOTH sign and verify.

    Never let the library infer the algorithm from the token header: an attacker
    controls that header, and a verifier that trusts it accepts `alg: none` or a
    token signed with a different scheme entirely. Pinning on verify is the half
    that actually matters.
    """
    try:
        decoded = jwt.decode(
            token,
            require_env("JWT_SECRET"),
            algorithms=[ALGORITHM],
        )
    except jwt.PyJWTError as err:
        # Expired, wrong signature, wrong algorithm — all the same to the
        # client. Saying which would tell an attacker whether they had the
        # right secret.
        raise ApiError.unauthorized("Invalid or expired token.") from err

    sub = decoded.get("sub")
    role = decoded.get("role")
    if not isinstance(sub, str) or not isinstance(role, str):
        raise ApiError.unauthorized("Invalid or expired token.")
    return {"sub": sub, "role": role}


def random_token() -> str:
    """
    RULE 10 — `offerToken` and `qrToken` are 32 random bytes, hex encoded.

    Never `random.random()`, never counter-derived. Both are bearer credentials
    for a real seat. `secrets` is the CSPRNG; `random` is not.
    """
    return secrets.token_hex(32)
