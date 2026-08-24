from __future__ import annotations

from pydantic import BaseModel

from ...models import EventType


class EventRef(BaseModel):
    id: str
    title: str
    type: EventType
    venue: str


class Totals(BaseModel):
    revenue: str
    capacity: int
    seatsSold: int  # noqa: N815 - wire format
    percentSold: int  # noqa: N815 - wire format
    bookings: int
    cancelled: int
    waiting: int


class CategorySummary(BaseModel):
    id: str
    name: str
    #: What the organiser charges *now*. Revenue above is what was actually
    #: paid, which diverges the moment anything is re-priced.
    currentPrice: str  # noqa: N815 - wire format
    capacity: int
    seatsSold: int  # noqa: N815 - wire format
    revenue: str
    waiting: int


class ShowSummary(BaseModel):
    id: str
    startsAt: str  # noqa: N815 - wire format
    status: str
    capacity: int
    seatsSold: int  # noqa: N815 - wire format
    revenue: str
    bookings: int
    cancelled: int


class EventSummary(BaseModel):
    event: EventRef
    totals: Totals
    categories: list[CategorySummary]
    shows: list[ShowSummary]
