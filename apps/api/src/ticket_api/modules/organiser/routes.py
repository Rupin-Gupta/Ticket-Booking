from __future__ import annotations

from fastapi import APIRouter

from ...deps import RequireOrganiser
from . import service
from .schemas import EventSummary

router = APIRouter(prefix="/organiser", tags=["organiser"])


@router.get("/events/{event_id}/summary", response_model=EventSummary)
async def event_summary(event_id: str, caller: RequireOrganiser) -> EventSummary:
    # Role gets you through the door; the service checks you own this event.
    return await service.event_summary(event_id, caller)
