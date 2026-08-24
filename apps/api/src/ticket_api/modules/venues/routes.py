from __future__ import annotations

from fastapi import APIRouter, status

from ...deps import RequireAdmin
from . import service
from .schemas import (
    AddSeatBlockInput,
    CreateVenueInput,
    SeatBlockResult,
    SectionsResult,
    UpdateVenueInput,
    VenueDetailResult,
    VenueListResult,
    VenueResult,
)

router = APIRouter(prefix="/venues", tags=["venues"])


# Reading a venue is public — the seat layout is on the ticket page anyway.
@router.get("", response_model=VenueListResult)
async def list_venues() -> VenueListResult:
    return VenueListResult(venues=await service.list_venues())


@router.get("/{venue_id}", response_model=VenueDetailResult)
async def get_venue(venue_id: str) -> VenueDetailResult:
    return VenueDetailResult(venue=await service.get_venue(venue_id))


@router.get("/{venue_id}/sections", response_model=SectionsResult)
async def list_sections(venue_id: str) -> SectionsResult:
    return SectionsResult(sections=await service.list_sections(venue_id))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=VenueResult)
async def create_venue(body: CreateVenueInput, _admin: RequireAdmin) -> VenueResult:
    return VenueResult(venue=await service.create_venue(body))


@router.patch("/{venue_id}", response_model=VenueResult)
async def update_venue(venue_id: str, body: UpdateVenueInput, _admin: RequireAdmin) -> VenueResult:
    return VenueResult(venue=await service.update_venue(venue_id, body))


@router.delete("/{venue_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_venue(venue_id: str, _admin: RequireAdmin) -> None:
    await service.delete_venue(venue_id)


@router.post(
    "/{venue_id}/seats", status_code=status.HTTP_201_CREATED, response_model=SeatBlockResult
)
async def add_seat_block(
    venue_id: str, body: AddSeatBlockInput, _admin: RequireAdmin
) -> SeatBlockResult:
    return await service.add_seat_block(venue_id, body)
