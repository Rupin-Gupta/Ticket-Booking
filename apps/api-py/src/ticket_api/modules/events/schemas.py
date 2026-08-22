from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...models import EventType, utcnow


def _trim(v: object) -> object:
    return v.strip() if isinstance(v, str) else v


def _naive_utc(v: object) -> object:
    """
    Accept an ISO string with or without a zone and store it naive-UTC, because
    the column is TIMESTAMP(3) WITHOUT TIME ZONE.

    Without this an aware datetime reaches a naive column and psycopg drops the
    offset silently — `2026-09-01T18:00+05:30` would be stored as 18:00 UTC,
    five and a half hours wrong, with no error anywhere.
    """
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if isinstance(v, datetime) and v.tzinfo is not None:
        from datetime import UTC

        return v.astimezone(UTC).replace(tzinfo=None)
    return v


class CreateEventInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    venueId: str = Field(min_length=1)  # noqa: N815 - wire format
    title: str = Field(min_length=1, max_length=160)
    type: EventType
    description: str | None = Field(default=None, max_length=2000)

    _normalise = field_validator("title", "description", mode="before")(_trim)


class UpdateEventInput(BaseModel):
    """
    No venueId, deliberately. Moving an event to a different venue would orphan
    every ShowSeat already generated against the old venue's seats.
    """

    model_config = ConfigDict(extra="ignore")

    title: str | None = Field(default=None, min_length=1, max_length=160)
    type: EventType | None = None
    description: str | None = Field(default=None, max_length=2000)

    _normalise = field_validator("title", "description", mode="before")(_trim)


class CreateCategoryInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=40)
    #: Money as a string, never a float. A float cannot hold 0.10.
    price: str
    sections: list[str] = Field(min_length=1)

    _normalise = field_validator("name", mode="before")(_trim)

    @field_validator("price", mode="before")
    @classmethod
    def _price_is_a_number(cls, v: object) -> str:
        try:
            parsed = Decimal(str(v))
        except (InvalidOperation, ValueError) as err:
            raise ValueError("Price must be a number >= 0") from err
        if parsed < 0 or not parsed.is_finite():
            raise ValueError("Price must be a number >= 0")
        return str(v)

    @field_validator("sections")
    @classmethod
    def _sections_are_named(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip() for s in v]
        if any(not s for s in cleaned):
            raise ValueError("Section name must not be blank.")
        return cleaned


class CreateShowInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    startsAt: datetime  # noqa: N815 - wire format

    _naive = field_validator("startsAt", mode="before")(_naive_utc)

    @field_validator("startsAt")
    @classmethod
    def _in_the_future(cls, v: datetime) -> datetime:
        if v <= utcnow():
            raise ValueError("Show must start in the future.")
        return v


class ListEventsQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: EventType | None = None
    venueId: str | None = None  # noqa: N815 - wire format
    q: str | None = Field(default=None, max_length=120)
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    #: Capped so one request cannot ask for the whole table.
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0)

    _naive = field_validator("from_", "to", mode="before")(_naive_utc)
    _normalise = field_validator("q", mode="before")(_trim)


# ------------------------------------------------------------------ outputs


class VenueRef(BaseModel):
    id: str
    name: str
    address: str | None = None


class OrganiserRef(BaseModel):
    id: str
    name: str


class CategoryOut(BaseModel):
    id: str
    name: str
    price: str
    sections: list[str] | None = None


class ShowRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    startsAt: str  # noqa: N815 - wire format
    #: `_count.showSeats`, matching what the React app already reads.
    count: dict[str, int] | None = Field(default=None, alias="_count")


class EventOut(BaseModel):
    id: str
    title: str
    type: EventType
    description: str | None = None
    venue: VenueRef
    organiser: OrganiserRef
    categories: list[CategoryOut]
    shows: list[ShowRef]


class EventListResult(BaseModel):
    events: list[EventOut]
    total: int
    limit: int
    offset: int


class EventDetailResult(BaseModel):
    event: EventOut


class EventWritten(BaseModel):
    id: str
    title: str
    type: EventType
    description: str | None = None
    venueId: str  # noqa: N815 - wire format


class EventWrittenResult(BaseModel):
    event: EventWritten


class OwnEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    type: EventType
    venue: VenueRef
    categories: list[CategoryOut]
    count: dict[str, int] = Field(alias="_count")


class OwnEventsResult(BaseModel):
    events: list[OwnEvent]


class CategoryResult(BaseModel):
    category: CategoryOut


class ShowCreated(BaseModel):
    id: str
    startsAt: str  # noqa: N815 - wire format
    seatCount: int  # noqa: N815 - wire format


class ShowCreatedResult(BaseModel):
    show: ShowCreated


class ShowEvent(BaseModel):
    id: str
    title: str
    type: EventType
    venue: VenueRef
    categories: list[CategoryOut]


class ShowDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    startsAt: str  # noqa: N815 - wire format
    event: ShowEvent
    count: dict[str, int] = Field(alias="_count")


class ShowDetailResult(BaseModel):
    show: ShowDetail
