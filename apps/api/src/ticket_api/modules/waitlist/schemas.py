from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ...models import WaitlistStatus


class JoinInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    categoryId: str = Field(min_length=1)  # noqa: N815 - wire format


class WaitlistJoined(BaseModel):
    id: str
    position: int


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
