"""
FastAPI dependencies replacing the Express auth middleware.

The important behavioural difference from Express: a dependency that raises
prevents the handler from ever running, so there is no equivalent of the
"route registered after the 404 handler is silently shadowed" bug that cost an
afternoon in the TypeScript version.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Annotated

from fastapi import Depends, Header

from .errors import ApiError
from .models import Role
from .security import TokenPayload, verify_access_token


async def current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> TokenPayload:
    """Rejects anything without a valid, unexpired bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError.unauthorized("Missing bearer token.")
    return verify_access_token(authorization.removeprefix("Bearer ").strip())


CurrentUser = Annotated[TokenPayload, Depends(current_user)]


def require_role(*roles: Role) -> Callable[..., TokenPayload]:
    """
    Coarse role gate. Layer it on top of authentication.

    This is only half of authorisation — it says "some organiser", never "the
    organiser who owns this event". Resource-ownership checks belong in the
    service, and without them any organiser can read any other organiser's
    revenue.
    """
    allowed: Sequence[Role] = roles

    def guard(user: CurrentUser) -> TokenPayload:
        if user["role"] not in allowed:
            raise ApiError.forbidden(f"Requires role: {' or '.join(allowed)}.")
        return user

    return guard


RequireAdmin = Annotated[TokenPayload, Depends(require_role(Role.ADMIN))]
RequireOrganiser = Annotated[TokenPayload, Depends(require_role(Role.ORGANISER, Role.ADMIN))]
