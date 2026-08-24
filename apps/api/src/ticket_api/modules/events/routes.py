from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from ...deps import RequireOrganiser
from . import service
from .schemas import (
    CategoryResult,
    CreateCategoryInput,
    CreateEventInput,
    CreateShowInput,
    EventDetailResult,
    EventListResult,
    EventWrittenResult,
    ListEventsQuery,
    OwnEventsResult,
    ShowCreatedResult,
    ShowDetailResult,
    UpdateEventInput,
)

event_router = APIRouter(prefix="/events", tags=["events"])
show_router = APIRouter(prefix="/shows", tags=["events"])


# --- Public browsing. No auth — an event listing nobody can see sells nothing.


@event_router.get("", response_model=EventListResult)
async def list_events(query: Annotated[ListEventsQuery, Depends()]) -> EventListResult:
    return await service.list_events(query)


# Declared before "/{event_id}" so "mine" is not matched as an id — the same
# ordering trap Express had, and FastAPI resolves it the same way: first match
# wins.
@event_router.get("/mine", response_model=OwnEventsResult)
async def list_own_events(caller: RequireOrganiser) -> OwnEventsResult:
    return OwnEventsResult(events=await service.list_own_events(caller))


@event_router.get("/{event_id}", response_model=EventDetailResult)
async def get_event(event_id: str) -> EventDetailResult:
    return EventDetailResult(event=await service.get_event(event_id))


# --- Organiser-owned writes. Every one is ownership-checked in the service.


@event_router.post("", status_code=status.HTTP_201_CREATED, response_model=EventWrittenResult)
async def create_event(body: CreateEventInput, caller: RequireOrganiser) -> EventWrittenResult:
    return EventWrittenResult(event=await service.create_event(body, caller))


@event_router.patch("/{event_id}", response_model=EventWrittenResult)
async def update_event(
    event_id: str, body: UpdateEventInput, caller: RequireOrganiser
) -> EventWrittenResult:
    return EventWrittenResult(event=await service.update_event(event_id, body, caller))


@event_router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(event_id: str, caller: RequireOrganiser) -> None:
    await service.delete_event(event_id, caller)


@event_router.post(
    "/{event_id}/categories", status_code=status.HTTP_201_CREATED, response_model=CategoryResult
)
async def create_category(
    event_id: str, body: CreateCategoryInput, caller: RequireOrganiser
) -> CategoryResult:
    return CategoryResult(category=await service.create_category(event_id, body, caller))


@event_router.post(
    "/{event_id}/shows", status_code=status.HTTP_201_CREATED, response_model=ShowCreatedResult
)
async def create_show(
    event_id: str, body: CreateShowInput, caller: RequireOrganiser
) -> ShowCreatedResult:
    return ShowCreatedResult(show=await service.create_show(event_id, body, caller))


# --- Shows are addressed on their own path; the seat map hangs off this too.


@show_router.get("/{show_id}", response_model=ShowDetailResult)
async def get_show(show_id: str) -> ShowDetailResult:
    return ShowDetailResult(show=await service.get_show(show_id))
