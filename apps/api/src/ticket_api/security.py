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
from argon2.exceptions import Argon2Error

from .config import require_env, settings
from .errors import ApiError

# ------------------------------------------------------------------ passwords

# Argon2id — OWASP's first choice in the Password Storage Cheat Sheet, with
# bcrypt listed only as the legacy fallback.
#
# The type is pinned rather than left to the library default so a dependency
# bump cannot silently move us onto argon2i or argon2d. The cost parameters are
# argon2-cffi's defaults, which happen to match the Node `argon2` package's
# defaults exactly (m=64MiB, t=3, p=4) — so hashes written by the retired
# TypeScript API still verify here. Both are above the OWASP floor of
# m=19MiB, t=2, p=1.
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


def verify_password(stored_hash: str | None, plain: str) -> bool:
    """Constant-ish time regardless of whether the account exists."""
    if stored_hash is None:
        # The verify still runs; only its (guaranteed) failure is discarded.
        # Burning the CPU is the entire point — skipping it would restore the
        # timing oracle this branch exists to close.
        with contextlib.suppress(Argon2Error):
            _hasher.verify(_DECOY_HASH, plain)
        return False
    try:
        return _hasher.verify(stored_hash, plain)
    except Argon2Error:
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
