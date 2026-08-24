from __future__ import annotations

from fastapi import APIRouter

from ...deps import RequireAdmin
from . import service
from .schemas import RaceInput, RaceResponse

router = APIRouter(prefix="/lab", tags=["lab"])


@router.post("/race", response_model=RaceResponse)
async def race(body: RaceInput, _admin: RequireAdmin) -> RaceResponse:
    """
    Admin-only: it holds real seats on a real show, briefly, and a public
    endpoint that fires fifty concurrent transactions on request is a free
    denial-of-service lever.
    """
    return RaceResponse(
        race=await service.race_for_one_seat(body.showId, body.seatId, body.attempts)
    )
