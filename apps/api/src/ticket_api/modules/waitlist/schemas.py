from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ...models import WaitlistStatus


class JoinInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    categoryId: str = Field(min_length=1)  # noqa: N815 - wire format


class Receipt(BaseModel):
    """
    The facts that decide a customer's place, plus a signature over them.

    Handed over at join time so the queue is checkable rather than merely
    trustworthy: the customer cannot forge one, and the server cannot later
    rewrite the facts without the signature failing.
    """

    payload: dict[str, object]
    signature: str


class WaitlistJoined(BaseModel):
    id: str
    position: int
    receipt: Receipt


class LogRow(BaseModel):
    seq: int
    categoryId: str  # noqa: N815 - wire format
    entryId: str  # noqa: N815 - wire format
    showSeatId: str  # noqa: N815 - wire format
    position: int
    at: str
    prevHash: str  # noqa: N815 - wire format
    hash: str


class ReceiptCheckInput(BaseModel):
    payload: dict[str, object]
    signature: str


class ReceiptCheck(BaseModel):
    valid: bool


class OfferLogResult(BaseModel):
    showId: str  # noqa: N815 - wire format
    rows: list[LogRow]
    #: Recomputed server-side for convenience. Anyone can recompute it from
    #: `rows` without trusting this field, which is the point.
    intact: bool
    brokenAt: int | None = None  # noqa: N815 - wire format


class WaitlistLeft(BaseModel):
    left: bool
    passedOn: bool  # noqa: N815 - wire format


class MyWaitlistEntry(BaseModel):
    id: str
    status: WaitlistStatus
    joinedAt: str  # noqa: N815 - wire format
    showId: str  # noqa: N815 - wire format
    eventId: str  # noqa: N815 - wire format
    eventTitle: str  # noqa: N815 - wire format
    startsAt: str  # noqa: N815 - wire format
    category: str
    price: str
    #: Only ever this customer's own token — it lives on their own entry.
    offerToken: str | None  # noqa: N815 - wire format
    offerExpiresAt: str | None  # noqa: N815 - wire format
    position: int | None


class MyWaitlistResult(BaseModel):
    entries: list[MyWaitlistEntry]


class OfferView(BaseModel):
    showId: str  # noqa: N815 - wire format
    eventId: str  # noqa: N815 - wire format
    eventTitle: str  # noqa: N815 - wire format
    venue: str
    startsAt: str  # noqa: N815 - wire format
    category: str
    price: str
    expiresAt: str  # noqa: N815 - wire format


class OfferResult(BaseModel):
    offer: OfferView
