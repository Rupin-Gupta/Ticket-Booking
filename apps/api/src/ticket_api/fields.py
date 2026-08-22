"""
Reusable validated field types.

Shared across modules so "what counts as an email" is decided once.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, Field

# Zod's `.email()` regex, ported verbatim.
#
# Deliberately NOT pydantic's EmailStr. EmailStr defers to `email-validator`,
# which rejects reserved TLDs like `.test` as undeliverable — stricter than the
# validator being replaced, and every existing test address is
# `someone@example.test`. Tightening validation during a port whose whole value
# is provable equivalence would be a silent behaviour change: addresses the
# deployed API accepts today would start returning 400.
#
# Syntax only, no deliverability check. Whether an address receives mail is
# answered by sending to it, not by a regex.
_EMAIL = re.compile(
    r"^(?!\.)(?!.*\.\.)([A-Z0-9_'+\-.]*)[A-Z0-9_+-]@([A-Z0-9][A-Z0-9\-]*\.)+[A-Z]{2,}$",
    re.IGNORECASE,
)


def _normalise_email(v: object) -> object:
    return v.strip().lower() if isinstance(v, str) else v


def _valid_email(v: str) -> str:
    if not _EMAIL.match(v):
        raise ValueError("value is not a valid email address")
    return v


Email = Annotated[
    str,
    BeforeValidator(_normalise_email),
    Field(max_length=254),
    AfterValidator(_valid_email),
]


def _trimmed(v: object) -> object:
    return v.strip() if isinstance(v, str) else v


def trimmed_str(*, min_length: int = 1, max_length: int) -> object:
    """A string trimmed before length checks, so "   " fails a min_length of 1."""
    return Annotated[
        str,
        BeforeValidator(_trimmed),
        Field(min_length=min_length, max_length=max_length),
    ]
