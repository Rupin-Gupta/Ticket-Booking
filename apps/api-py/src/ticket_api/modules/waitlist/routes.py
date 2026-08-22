from __future__ import annotations

from fastapi import APIRouter, status

from ...deps import CurrentUser
from ...jobs.email_queue import enqueue_email
from ..bookings.schemas import BookingResult
from . import service
from .schemas import (
    JoinInput,
    MyWaitlistResult,
    OfferResult,
    WaitlistJoined,
    WaitlistLeft,
)

#: Mounted alongside the other show routes at /shows.
show_router = APIRouter(prefix="/shows", tags=["waitlist"])

#: Mounted at /waitlist.
router = APIRouter(prefix="/waitlist", tags=["waitlist"])


@show_router.post(
    "/{show_id}/waitlist", status_code=status.HTTP_201_CREATED, response_model=WaitlistJoined
)
async def join(show_id: str, body: JoinInput, user: CurrentUser) -> WaitlistJoined:
    return await service.join(show_id, body.categoryId, user)


@router.get("/me", response_model=MyWaitlistResult)
async def list_mine(user: CurrentUser) -> MyWaitlistResult:
    return MyWaitlistResult(entries=await service.list_mine(user))


@router.delete("/{entry_id}", response_model=WaitlistLeft)
async def leave(entry_id: str, user: CurrentUser) -> WaitlistLeft:
    result, pending = await service.leave(entry_id, user)
    # Giving up an offer hands the seat straight on; the next person is told
    # after the transaction that created their offer has committed.
    if pending is not None:
        await enqueue_email({"kind": "waitlist-offer", "entryId": pending.entry_id})
    return result


# Public: the customer follows this link from an email, possibly on a phone that
# is not signed in yet. Reading the offer is safe; accepting is not.
@router.get("/offers/{token}", response_model=OfferResult)
async def get_offer(token: str) -> OfferResult:
    return OfferResult(offer=await service.get_offer(token))


@router.post(
    "/offers/{token}/accept", status_code=status.HTTP_201_CREATED, response_model=BookingResult
)
async def accept_offer(token: str, user: CurrentUser) -> BookingResult:
    return BookingResult(booking=await service.accept_offer(token, user))
