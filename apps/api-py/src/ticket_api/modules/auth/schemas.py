"""
Request and response shapes for authentication.

Note what is absent: there is no `role` field, anywhere.

Pydantic ignores unknown keys by default, so a request body carrying
`"role": "ADMIN"` parses to a model without it and the service hard-codes
CUSTOMER regardless. Accepting a client-supplied role is a one-line
privilege-escalation hole, and the way to not have it is to never declare the
field in the first place. `extra="ignore"` is stated explicitly here rather
than inherited, because this is the one model where the default carries
security weight.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...fields import Email
from ...models import Role


class RegisterInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: Email
    # Upper bound is a denial-of-service guard: Argon2 on a megabyte of input
    # costs real CPU, and an attacker will happily send a megabyte.
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name", mode="before")
    @classmethod
    def _trim_name(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: Email
    password: str = Field(min_length=1, max_length=128)


class PublicUser(BaseModel):
    """Explicit field list. `passwordHash` must never reach a response body."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    role: Role


class AuthResult(BaseModel):
    user: PublicUser
    accessToken: str  # noqa: N815 - the wire format the frontend already reads


class MeResult(BaseModel):
    user: PublicUser
