from __future__ import annotations

from fastapi import APIRouter, Depends, status

from ...deps import CurrentUser, OptionalUser
from ...rate_limit import hold_limiter
from . import service
from .schemas import (
    HoldResult,
    HoldSeatsInput,
    MyHoldsResult,
    ReleaseResult,
    SeatMapResult,
)

#: Mounted alongside the existing show routes at /shows.
show_router = APIRouter(prefix="/shows", tags=["seats"])

#: Mounted at /holds.
hold_router = APIRouter(prefix="/holds", tags=["seats"])


@show_router.get("/{show_id}/seats", response_model=SeatMapResult)
async def seat_map(show_id: str, viewer: OptionalUser) -> SeatMapResult:
    return SeatMapResult(
        seats=await service.get_seat_map(show_id, viewer["sub"] if viewer else None)
    )


@show_router.post(
    "/{show_id}/holds",
    status_code=status.HTTP_201_CREATED,
    response_model=HoldResult,
    dependencies=[Depends(hold_limiter)],
)
async def place_hold(show_id: str, body: HoldSeatsInput, user: CurrentUser) -> HoldResult:
    return await service.hold_seats(show_id, body, user["sub"])


@show_router.delete("/{show_id}/holds", response_model=ReleaseResult)
async def release(show_id: str, user: CurrentUser) -> ReleaseResult:
    return ReleaseResult(released=await service.release_holds(show_id, user["sub"]))


@hold_router.get("/me", response_model=MyHoldsResult)
async def my_holds(user: CurrentUser) -> MyHoldsResult:
    return MyHoldsResult(holds=await service.list_my_holds(user["sub"]))
