from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...config import settings
from ...models import BookingStatus, EventType


class CreateBookingInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    showId: str = Field(min_length=1)  # noqa: N815 - wire format
    seatIds: list[str] = Field(  # noqa: N815 - wire format
        min_length=1, max_length=settings.MAX_SEATS_PER_HOLD
    )

    @field_validator("seatIds")
    @classmethod
    def _no_duplicates(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("Duplicate seat in request.")
        return v


class BookingShow(BaseModel):
    id: str
    startsAt: str  # noqa: N815 - wire format
    eventId: str  # noqa: N815 - wire format
    title: str
    type: EventType
    venue: str
    address: str


class BookingSeatOut(BaseModel):
    showSeatId: str  # noqa: N815 - wire format
    label: str
    section: str
    price: str


class BookingView(BaseModel):
    """
    `qrToken` is deliberately absent unless explicitly asked for.

    It is a bearer credential for entry, so it travels in the emailed QR and on
    the single booking a customer opens — never in a list.
    """

    id: str
    reference: str
    status: BookingStatus
    createdAt: str  # noqa: N815 - wire format
    cancelledAt: str | None  # noqa: N815 - wire format
    show: BookingShow
    seats: list[BookingSeatOut]
    total: str
    qrToken: str | None = None  # noqa: N815 - wire format


class BookingResult(BaseModel):
    booking: BookingView


class BookingListResult(BaseModel):
    bookings: list[BookingView]


class CancelResult(BaseModel):
    cancelled: bool
    seatsReleased: int  # noqa: N815 - wire format
    offeredToWaitlist: int  # noqa: N815 - wire format


class TicketView(BaseModel):
    valid: bool
    status: BookingStatus
    reference: str
    eventTitle: str  # noqa: N815 - wire format
    venue: str
    startsAt: str  # noqa: N815 - wire format
    seats: list[str]
    #: Null until somebody is admitted on it. The door needs to see this before
    #: deciding, which is why it is on the public read.
    checkedInAt: str | None = None  # noqa: N815 - wire format


class VerifyResult(BaseModel):
    ticket: TicketView


class CheckInResult(BaseModel):
    ticket: TicketView
    #: True only for the scan that actually admitted them. A second scan is a
    #: refusal, not a success.
    admitted: bool
