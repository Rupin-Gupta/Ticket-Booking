from __future__ import annotations

from fastapi import APIRouter, Depends, status

from ...deps import CurrentUser
from ...errors import ApiError
from ...rate_limit import login_limiter, register_limiter
from . import service
from .schemas import AuthResult, LoginInput, MeResult, RegisterInput

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=AuthResult,
    dependencies=[Depends(register_limiter)],
)
async def register(body: RegisterInput) -> AuthResult:
    return await service.register(body)


@router.post("/login", response_model=AuthResult, dependencies=[Depends(login_limiter)])
async def login(body: LoginInput) -> AuthResult:
    return await service.login(body)


@router.get("/me", response_model=MeResult)
async def me(user: CurrentUser) -> MeResult:
    # The token is valid, but the account behind it may have been deleted since
    # it was issued — a JWT cannot be revoked before it expires.
    found = await service.get_by_id(user["sub"])
    if found is None:
        raise ApiError.unauthorized("Account no longer exists.")
    return MeResult(user=found)
