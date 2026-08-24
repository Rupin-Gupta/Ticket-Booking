from __future__ import annotations

from pydantic import BaseModel, Field


class RaceInput(BaseModel):
    showId: str  # noqa: N815 - wire format
    #: Omit to let the server pick any free seat on the show.
    seatId: str | None = None  # noqa: N815 - wire format
    attempts: int = Field(default=50, ge=2, le=100)


class RaceOutcome(BaseModel):
    won: int
    rejected: int
    errors: int


class RaceResult(BaseModel):
    seatId: str  # noqa: N815 - wire format
    attempts: int
    elapsedMs: int  # noqa: N815 - wire format
    outcome: RaceOutcome
    errorCodes: list[str]  # noqa: N815 - wire format
    holdsGranted: int  # noqa: N815 - wire format
    #: One winner, no errors. Anything else and the guarantee did not hold.
    passed: bool


class RaceResponse(BaseModel):
    race: RaceResult
