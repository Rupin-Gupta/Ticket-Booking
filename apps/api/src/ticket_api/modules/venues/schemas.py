from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...models import EventType, StageLayout


def _trim(v: object) -> object:
    return v.strip() if isinstance(v, str) else v


class CreateVenueInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=120)
    address: str = Field(min_length=1, max_length=240)
    stageLayout: StageLayout = StageLayout.END_STAGE  # noqa: N815 - wire format
    # At least one, or the venue can host nothing at all.
    allowedEventTypes: list[EventType] = Field(  # noqa: N815 - wire format
        default_factory=lambda: [EventType.MOVIE, EventType.CONCERT], min_length=1
    )
    # Long enough to clear and reset the room. Capped at four hours because
    # beyond that the organiser wants a different day, not a longer gap.
    turnaroundMinutes: int = Field(default=15, ge=0, le=240)  # noqa: N815 - wire format

    _normalise = field_validator("name", "address", mode="before")(_trim)


class UpdateVenueInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    address: str | None = Field(default=None, min_length=1, max_length=240)
    stageLayout: StageLayout | None = None  # noqa: N815 - wire format
    allowedEventTypes: list[EventType] | None = Field(  # noqa: N815 - wire format
        default=None, min_length=1
    )
    turnaroundMinutes: int | None = Field(default=None, ge=0, le=240)  # noqa: N815 - wire format

    _normalise = field_validator("name", "address", mode="before")(_trim)


class AddSeatBlockInput(BaseModel):
    """
    Bulk seat creation: one named section.

    Rows are labelled A, B, C... so 26 is the ceiling — past that the labels
    would need a second letter, and nothing in this project needs a 27-row
    section. ponytail: if a venue ever does, switch to AA/AB here and nowhere
    else.
    """

    model_config = ConfigDict(extra="ignore")

    section: str = Field(min_length=1, max_length=40)
    rows: int = Field(ge=1, le=26)
    seatsPerRow: int = Field(ge=1, le=60)  # noqa: N815 - wire format

    _normalise = field_validator("section", mode="before")(_trim)


class SeatOut(BaseModel):
    """
    Reads `pos_x` off the ORM object, writes `posX` to the wire.

    The two names differ because the Python attribute is snake_case while the
    Prisma-created column — and the JSON the React app already parses — is
    camelCase. `from_attributes` looks up the *attribute*, so the validation
    alias has to say so explicitly.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    section: str
    row: str
    number: int
    posX: float = Field(validation_alias="pos_x")  # noqa: N815 - wire format
    posY: float = Field(validation_alias="pos_y")  # noqa: N815 - wire format


class SeatCount(BaseModel):
    seats: int


class VenueSummary(BaseModel):
    """
    `_count.seats`, not `seatCount`.

    That shape is Prisma's, and Prisma is gone — but three places in the React
    app read `venue._count.seats`, and the frontend is not part of this port.
    Renaming it here would be a silent UI break that no API test would catch.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    address: str
    stageLayout: StageLayout  # noqa: N815 - wire format
    allowedEventTypes: list[EventType]  # noqa: N815 - wire format
    turnaroundMinutes: int  # noqa: N815 - wire format
    count: SeatCount = Field(alias="_count")


class VenueDetail(BaseModel):
    id: str
    name: str
    address: str
    stageLayout: StageLayout  # noqa: N815 - wire format
    allowedEventTypes: list[EventType]  # noqa: N815 - wire format
    turnaroundMinutes: int  # noqa: N815 - wire format
    seats: list[SeatOut]


class VenueBase(BaseModel):
    id: str
    name: str
    address: str
    stageLayout: StageLayout  # noqa: N815 - wire format
    allowedEventTypes: list[EventType]  # noqa: N815 - wire format
    turnaroundMinutes: int  # noqa: N815 - wire format


class VenueListResult(BaseModel):
    venues: list[VenueSummary]


class VenueDetailResult(BaseModel):
    venue: VenueDetail


class VenueResult(BaseModel):
    venue: VenueBase


class SectionsResult(BaseModel):
    sections: list[str]


class SeatBlockResult(BaseModel):
    created: int
    section: str
    startY: float  # noqa: N815 - wire format
