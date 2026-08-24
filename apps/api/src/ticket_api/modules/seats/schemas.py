from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...config import settings
from ...models import SeatStatus


class SeatView(BaseModel):
    """
    One seat as the browser sees it.

    Field names are camelCase because this is the wire format the React app
    already consumes — the port does not get to rename them.

    `heldByUserId` is absent by construction, not by omission (RULE 8). Showing
    *that* a seat is held is the product; showing *who* holds it leaks who is
    buying what.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    section: str
    row: str
    number: int
    posX: float  # noqa: N815 - wire format
    posY: float  # noqa: N815 - wire format
    categoryId: str  # noqa: N815 - wire format
    categoryName: str  # noqa: N815 - wire format
    price: str  # Decimal as a string, never a float — money must not round.
    status: SeatStatus
    heldByMe: bool  # noqa: N815 - wire format
    #: The countdown is the holder's business alone; null for everyone else.
    holdExpiresAt: str | None = None  # noqa: N815 - wire format
    #: How often this seat is picked up and put back down, relative to its own
    #: row. Null unless the organiser published signals for this event AND the
    #: seat has enough outcomes to say anything — never a number computed from
    #: three data points.
    hesitation: dict[str, float | int] | None = None


class SeatMapResult(BaseModel):
    seats: list[SeatView]


class HoldSeatsInput(BaseModel):
    seatIds: list[str] = Field(  # noqa: N815 - wire format
        min_length=1,
        # Capped so one request cannot lock the whole venue in a single call.
        max_length=settings.MAX_SEATS_PER_HOLD,
    )

    @field_validator("seatIds")
    @classmethod
    def _no_duplicates(cls, v: list[str]) -> list[str]:
        # Duplicates would inflate the count past the cap and make the lock set
        # lie about how many rows it is protecting: `len(rows) != len(seat_ids)`
        # is what proves every requested seat was found and locked, and a
        # repeated id breaks that equality for a request that is otherwise fine.
        if len(set(v)) != len(v):
            raise ValueError("Duplicate seat in request.")
        if any(not s for s in v):
            raise ValueError("Seat id must not be blank.")
        return v


class HoldResult(BaseModel):
    showId: str  # noqa: N815 - wire format
    seatIds: list[str]  # noqa: N815 - wire format
    holdExpiresAt: str  # noqa: N815 - wire format


class ReleaseResult(BaseModel):
    released: int
    #: When the seats actually become bookable by anyone else.
    freeAt: str  # noqa: N815 - wire format


class ExtendResult(BaseModel):
    holdExpiresAt: str  # noqa: N815 - wire format
    seats: int


class MyHold(BaseModel):
    showSeatId: str  # noqa: N815 - wire format
    showId: str  # noqa: N815 - wire format
    holdExpiresAt: str | None  # noqa: N815 - wire format
    label: str
    section: str
    category: str
    price: str
    eventTitle: str  # noqa: N815 - wire format
    eventId: str  # noqa: N815 - wire format
    startsAt: str  # noqa: N815 - wire format


class MyHoldsResult(BaseModel):
    holds: list[MyHold]
