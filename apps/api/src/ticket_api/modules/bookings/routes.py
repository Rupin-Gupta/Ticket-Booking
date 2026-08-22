from __future__ import annotations

from fastapi import APIRouter, status

from ...deps import CurrentUser
from . import service
from .schemas import (
    BookingListResult,
    BookingResult,
    CancelResult,
    CreateBookingInput,
    VerifyResult,
)

router = APIRouter(prefix="/bookings", tags=["bookings"])

#: Mounted at the API root: the QR encodes {WEB_URL}/verify/{token}.
verify_router = APIRouter(prefix="/verify", tags=["bookings"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=BookingResult)
async def create_booking(body: CreateBookingInput, user: CurrentUser) -> BookingResult:
    return BookingResult(booking=await service.create_booking(body.showId, body.seatIds, user))


@router.get("", response_model=BookingListResult)
async def list_my_bookings(user: CurrentUser) -> BookingListResult:
    return BookingListResult(bookings=await service.list_my_bookings(user))


@router.get("/{booking_id}", response_model=BookingResult)
async def get_booking(booking_id: str, user: CurrentUser) -> BookingResult:
    return BookingResult(booking=await service.get_booking(booking_id, user))


@router.post("/{booking_id}/cancel", response_model=CancelResult)
async def cancel_booking(booking_id: str, user: CurrentUser) -> CancelResult:
    return await service.cancel_booking(booking_id, user)


# Public: the person scanning at the door is not logged in.
@verify_router.get("/{qr_token}", response_model=VerifyResult)
async def verify_ticket(qr_token: str) -> VerifyResult:
    return VerifyResult(ticket=await service.verify_ticket(qr_token))
