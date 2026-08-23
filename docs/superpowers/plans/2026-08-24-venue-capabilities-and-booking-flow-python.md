# Venue Capabilities and Booking Flow — Implementation Plan (Python)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give venues capabilities an admin controls (stage layout, permitted event types, turnaround), stop two organisers double-booking a venue, and replace the single-page hold with a three-page flow whose seats expire on two different clocks.

**Architecture:** Extends the existing model rather than restructuring it. Stage layout is *stored geometry* — the venue builder writes radial coordinates and the seat map renderer stays untouched. Venue scheduling gets an application-level check for good error messages plus a Postgres GiST exclusion constraint as the actual guarantee, partial on `status` so cancelling frees the slot. The two-clock TTL reuses lazy expiry: going back *shortens* a hold rather than deleting it.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2.0 async, psycopg3, Alembic, Pydantic v2, pytest + pytest-asyncio + httpx. Frontend is React 19 + Vite and is touched only in Tasks 8 and 9.

**Spec:** `docs/superpowers/specs/2026-08-23-venue-capabilities-and-booking-flow-design.md`

**Supersedes:** the TypeScript plan of 2026-08-23, deleted in the same commit as this file. The spec is unchanged and still approved; only the implementation stack moved.

## Global Constraints

- **Never change the hold transaction's locking discipline.** The `FOR UPDATE`, the status re-read and the write stay together in `apps/api/src/ticket_api/modules/seats/service.py`. Only the abuse cap may sit outside it (ADR-019).
- **`tests/concurrency/test_holds.py` must stay green after every task.** It is the regression guard for the whole plan.
- Money is a `Decimal` end to end, rendered with `models.money()`. Never `float`.
- Timestamps crossing the wire go through `models.iso()`; timestamps written to the database come from `models.utcnow()` and are **naive UTC**, because the columns are `TIMESTAMP(3)` without time zone (rule 17).
- The driver stays **psycopg3 with `prepare_threshold=None`** (rule 16). Do not add asyncpg.
- Raw SQL is `sqlalchemy.text()` with bound parameters. String-formatted SQL near request data is banned (rule 13).
- `heldByUserId` never leaves the server (rule 8).
- Wire format stays camelCase — the React app is not part of this work. Pydantic field names are the wire names.
- Every task ends with `ruff check`, `ruff format`, and the **full** suite green.
- Commit messages: imperative subject under 72 chars, body explains *why*. End with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

**Commands used throughout** (run from the repo root unless stated):

```bash
npm run test:db:up                 # throwaway Postgres on :5433, if not already running
cd apps/api
NODE_ENV=test ./.venv/bin/python -m pytest -q             # full suite
NODE_ENV=test ./.venv/bin/python -m pytest tests/x.py -q  # one file
NODE_ENV=test ./.venv/bin/python -m alembic upgrade head  # apply to the test DB
./.venv/bin/python -m ruff check --fix src tests && ./.venv/bin/python -m ruff format src tests
```

---

## File Structure

**Created**

| File | Responsibility |
| --- | --- |
| `apps/api/src/ticket_api/lib/geometry.py` | Pure seat-coordinate maths for both stage layouts. No I/O, so it is unit-testable without a database |
| `apps/api/src/ticket_api/modules/venues/scheduling.py` | Venue availability: occupied-window maths and the overlap check |
| `apps/api/tests/test_geometry.py` | Unit tests for coordinate generation |
| `apps/api/tests/test_venue_capabilities.py` | Stage layout, allowed types, turnaround, radial seats |
| `apps/api/tests/concurrency/test_scheduling.py` | Double-booking, including the parallel case |
| `apps/api/tests/test_holds_grace.py` | Two-clock TTL behaviour |
| `apps/api/alembic/versions/*_venue_capabilities.py` | `StageLayout` enum + three `Venue` columns |
| `apps/api/alembic/versions/*_show_scheduling.py` | `ShowStatus` enum, five `Show` columns, backfill |
| `apps/api/alembic/versions/*_show_no_venue_overlap.py` | `btree_gist` + the exclusion constraint |
| `apps/web/src/pages/CheckoutPage.tsx` | Page 2 of the booking flow |
| `apps/web/src/pages/checkout.css` | Its styles |

**Modified**

| File | Change |
| --- | --- |
| `apps/api/src/ticket_api/models.py` | `StageLayout`, `ShowStatus` enums; `Venue` and `Show` columns |
| `apps/api/src/ticket_api/config.py` | `RELEASE_GRACE_SECONDS`, `HOLD_TTL_SECONDS` default 600 → 300 |
| `apps/api/src/ticket_api/modules/venues/schemas.py` | Capability fields, arc input, sections-with-counts |
| `apps/api/src/ticket_api/modules/venues/service.py` | Capability validation, delegate coordinates to `geometry.py` |
| `apps/api/src/ticket_api/modules/events/schemas.py` | `durationMinutes` on show creation |
| `apps/api/src/ticket_api/modules/events/service.py` | Event-type gate; scheduling fields and the overlap check |
| `apps/api/src/ticket_api/modules/seats/service.py` | `release_holds` becomes a grace release; add `extend_hold` |
| `apps/api/src/ticket_api/modules/seats/routes.py` | Extend endpoint |
| `apps/api/src/ticket_api/seed.py` | Supply the new required `Show` columns |
| `apps/api/tests/conftest.py` | `make_show` supplies the new required `Show` columns |
| `apps/web/src/pages/ShowPage.tsx` | Continue navigates instead of holding in place |
| `apps/web/src/pages/OrganiserPage.tsx` | Section checkboxes show seat counts |
| `apps/web/src/main.tsx` | Checkout route |
| `apps/web/src/lib/types.ts` | `sections` shape change |

---

## Task 1: Seat geometry as a pure module

**Files:**
- Create: `apps/api/src/ticket_api/lib/geometry.py`
- Create: `apps/api/tests/test_geometry.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `SeatPosition` — a `NamedTuple` with fields `row: str`, `number: int`, `pos_x: float`, `pos_y: float`
  - `generate_end_stage_block(rows: int, seats_per_row: int, start_y: float) -> list[SeatPosition]`
  - `generate_centre_stage_block(rows: int, seats_per_row: int, start_radius: float, arc_start_degrees: float, arc_span_degrees: float) -> list[SeatPosition]`
  - `ROW_LABELS: str`

Extracted as a pure module because coordinate maths is the one part of this milestone testable without a database, and a round trip per assertion would make those tests slow for nothing.

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_geometry.py`:

```python
"""Pure maths — no database, no fixtures, no event loop."""

from __future__ import annotations

import math

from ticket_api.lib.geometry import (
    ROW_LABELS,
    generate_centre_stage_block,
    generate_end_stage_block,
)


def test_end_stage_produces_rows_times_seats_labelled_from_a():
    seats = generate_end_stage_block(rows=3, seats_per_row=4, start_y=0)
    assert len(seats) == 12
    assert seats[0].row == "A"
    assert seats[0].number == 1
    assert seats[-1].row == "C"
    assert seats[-1].number == 4


def test_end_stage_centres_every_row_on_zero():
    """Rows of different widths have to stay aligned in the seat map."""
    four = generate_end_stage_block(rows=1, seats_per_row=4, start_y=0)
    six = generate_end_stage_block(rows=1, seats_per_row=6, start_y=0)
    assert sum(s.pos_x for s in four) == 0
    assert sum(s.pos_x for s in six) == 0
    # pos_y is the row offset, not something centred — one row means one value.
    assert {s.pos_y for s in six} == {0}


def test_end_stage_start_y_offsets_every_row():
    seats = generate_end_stage_block(rows=2, seats_per_row=2, start_y=7)
    assert sorted({s.pos_y for s in seats}) == [7, 8]


def test_centre_stage_puts_every_seat_on_its_row_radius():
    seats = generate_centre_stage_block(
        rows=2, seats_per_row=8, start_radius=5, arc_start_degrees=0, arc_span_degrees=360
    )
    for seat in seats:
        expected = 5 if seat.row == "A" else 6
        assert math.isclose(math.hypot(seat.pos_x, seat.pos_y), expected, abs_tol=1e-9)


def test_centre_stage_quarter_arc_stays_inside_its_wedge():
    seats = generate_centre_stage_block(
        rows=1, seats_per_row=10, start_radius=4, arc_start_degrees=0, arc_span_degrees=90
    )
    # Angles inside (0, 90) put every seat in the positive quadrant.
    assert all(s.pos_x > 0 and s.pos_y > 0 for s in seats)


def test_centre_stage_full_circle_does_not_stack_first_and_last_seat():
    """
    Seats sit at the CENTRE of their angular slot, not on its edge — otherwise a
    360-degree block puts seat 1 and seat N in the same place.
    """
    seats = generate_centre_stage_block(
        rows=1, seats_per_row=6, start_radius=3, arc_start_degrees=0, arc_span_degrees=360
    )
    first, last = seats[0], seats[-1]
    assert not (
        math.isclose(first.pos_x, last.pos_x, abs_tol=1e-6)
        and math.isclose(first.pos_y, last.pos_y, abs_tol=1e-6)
    )


def test_centre_stage_matches_the_end_stage_labelling_contract():
    seats = generate_centre_stage_block(
        rows=2, seats_per_row=3, start_radius=3, arc_start_degrees=0, arc_span_degrees=180
    )
    assert len(seats) == 6
    assert seats[0].row == "A"
    assert seats[-1].row == "B"
    assert ROW_LABELS[0] == "A"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/test_geometry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ticket_api.lib.geometry'`

- [ ] **Step 3: Write the implementation**

Create `apps/api/src/ticket_api/lib/geometry.py`:

```python
"""
Seat coordinate generation for both stage layouts.

Pure functions, no I/O — coordinates are the one part of venue building that can
be tested without a database, and a round trip per assertion would make those
tests slow for nothing.

pos_x / pos_y are grid units, not pixels. The frontend decides how big a seat is,
which is why a radial layout needs no renderer change: it writes the same two
numbers, just arranged in a circle.
"""

from __future__ import annotations

import math
from typing import NamedTuple

ROW_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class SeatPosition(NamedTuple):
    row: str
    number: int
    pos_x: float
    pos_y: float


def generate_end_stage_block(
    *, rows: int, seats_per_row: int, start_y: float
) -> list[SeatPosition]:
    """A rectangular block. Rows stack downwards, each centred on x = 0."""
    return [
        SeatPosition(
            row=ROW_LABELS[r],
            number=n,
            # Centring on zero keeps rows of different widths aligned.
            pos_x=n - (seats_per_row + 1) / 2,
            pos_y=start_y + r,
        )
        for r in range(rows)
        for n in range(1, seats_per_row + 1)
    ]


def generate_centre_stage_block(
    *,
    rows: int,
    seats_per_row: int,
    start_radius: float,
    arc_start_degrees: float,
    arc_span_degrees: float,
) -> list[SeatPosition]:
    """
    A block arranged around a central stage.

    Rows become radii and seats spread along an arc. Seats sit at the *centre* of
    their angular slot rather than on its edge, so a full 360-degree block does
    not put the first and last seat on top of each other.
    """
    seats: list[SeatPosition] = []
    for r in range(rows):
        radius = start_radius + r
        for n in range(1, seats_per_row + 1):
            degrees = arc_start_degrees + (arc_span_degrees * (n - 0.5)) / seats_per_row
            radians = math.radians(degrees)
            seats.append(
                SeatPosition(
                    row=ROW_LABELS[r],
                    number=n,
                    pos_x=radius * math.cos(radians),
                    pos_y=radius * math.sin(radians),
                )
            )
    return seats
```

- [ ] **Step 4: Run the tests**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/test_geometry.py -q`
Expected: PASS, 6 tests.

- [ ] **Step 5: Lint and run the whole suite**

Run:
```bash
cd apps/api && ./.venv/bin/python -m ruff check --fix src tests && ./.venv/bin/python -m ruff format src tests
NODE_ENV=test ./.venv/bin/python -m pytest -q
```
Expected: ruff clean; 126 passed.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/ticket_api/lib/geometry.py apps/api/tests/test_geometry.py
git commit -m "$(cat <<'EOF'
Extract seat geometry as a pure, testable module

Coordinate maths is the one part of venue building testable without a database,
so it moves out of the service. Adds centre-stage generation: rows become radii
and seats spread along an arc, seated at the centre of their angular slot so a
full 360-degree block does not put the first and last seat on top of each other.

Both layouts emit plain pos_x/pos_y grid units, which is why a radial venue needs
no seat map renderer change at all.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Venue capabilities — model, migration, validation

**Files:**
- Modify: `apps/api/src/ticket_api/models.py`
- Create: `apps/api/alembic/versions/<rev>_venue_capabilities.py`
- Modify: `apps/api/src/ticket_api/modules/venues/schemas.py`
- Modify: `apps/api/src/ticket_api/modules/venues/service.py`
- Create: `apps/api/tests/test_venue_capabilities.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `StageLayout` enum with `END_STAGE`, `CENTRE_STAGE`
  - `Venue.stage_layout`, `Venue.allowed_event_types`, `Venue.turnaround_minutes`
  - `CreateVenueInput` gains `stageLayout`, `allowedEventTypes`, `turnaroundMinutes`
  - `VenueBase` / `VenueDetail` / `VenueSummary` all carry the three fields
  - Error code `CENTRE_STAGE_CANNOT_SHOW_MOVIES` (400)

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_venue_capabilities.py`:

```python
from __future__ import annotations

import pytest

from ticket_api.models import Role

VENUES = "/api/v1/venues"


@pytest.fixture
async def admin(make_user):
    return await make_user(Role.ADMIN, "admin")


async def test_a_venue_defaults_to_end_stage_allowing_both_types(client, auth, admin):
    r = await client.post(
        VENUES, json={"name": "Default", "address": "x"}, headers=auth(admin[1])
    )
    assert r.status_code == 201, r.text
    venue = r.json()["venue"]
    assert venue["stageLayout"] == "END_STAGE"
    assert sorted(venue["allowedEventTypes"]) == ["CONCERT", "MOVIE"]
    assert venue["turnaroundMinutes"] == 15


async def test_an_explicit_centre_stage_concert_venue_is_accepted(client, auth, admin):
    r = await client.post(
        VENUES,
        json={
            "name": "Round",
            "address": "x",
            "stageLayout": "CENTRE_STAGE",
            "allowedEventTypes": ["CONCERT"],
            "turnaroundMinutes": 45,
        },
        headers=auth(admin[1]),
    )
    assert r.status_code == 201, r.text
    venue = r.json()["venue"]
    assert venue["stageLayout"] == "CENTRE_STAGE"
    assert venue["turnaroundMinutes"] == 45


async def test_a_centre_stage_venue_may_not_allow_movies(client, auth, admin):
    """Nobody projects a film in the round."""
    r = await client.post(
        VENUES,
        json={
            "name": "Absurd",
            "address": "x",
            "stageLayout": "CENTRE_STAGE",
            "allowedEventTypes": ["MOVIE", "CONCERT"],
        },
        headers=auth(admin[1]),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "CENTRE_STAGE_CANNOT_SHOW_MOVIES"


async def test_a_venue_must_allow_at_least_one_event_type(client, auth, admin):
    r = await client.post(
        VENUES,
        json={"name": "Nothing", "address": "x", "allowedEventTypes": []},
        headers=auth(admin[1]),
    )
    assert r.status_code == 400


async def test_patching_one_half_cannot_produce_an_incoherent_venue(client, auth, admin):
    """
    An END_STAGE venue allowing MOVIE, then flipped to CENTRE_STAGE, must be
    refused — the update has to check the merged result, not just its own body.
    """
    venue = (
        await client.post(
            VENUES, json={"name": "Flip", "address": "x"}, headers=auth(admin[1])
        )
    ).json()["venue"]["id"]

    r = await client.patch(
        f"{VENUES}/{venue}", json={"stageLayout": "CENTRE_STAGE"}, headers=auth(admin[1])
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "CENTRE_STAGE_CANNOT_SHOW_MOVIES"


async def test_turnaround_is_bounded(client, auth, admin):
    for minutes in (-1, 241):
        r = await client.post(
            VENUES,
            json={"name": f"T{minutes}", "address": "x", "turnaroundMinutes": minutes},
            headers=auth(admin[1]),
        )
        assert r.status_code == 400, minutes


async def test_capabilities_appear_on_read(client, auth, admin):
    venue = (
        await client.post(
            VENUES,
            json={
                "name": "Readable",
                "address": "x",
                "stageLayout": "CENTRE_STAGE",
                "allowedEventTypes": ["CONCERT"],
            },
            headers=auth(admin[1]),
        )
    ).json()["venue"]["id"]

    detail = (await client.get(f"{VENUES}/{venue}")).json()["venue"]
    assert detail["stageLayout"] == "CENTRE_STAGE"
    assert detail["allowedEventTypes"] == ["CONCERT"]

    listed = next(v for v in (await client.get(VENUES)).json()["venues"] if v["id"] == venue)
    assert listed["stageLayout"] == "CENTRE_STAGE"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/test_venue_capabilities.py -q`
Expected: FAIL — `KeyError: 'stageLayout'`

- [ ] **Step 3: Add the enum and columns to the model**

In `apps/api/src/ticket_api/models.py`, add after the `EventType` enum:

```python
class StageLayout(enum.StrEnum):
    END_STAGE = "END_STAGE"  # audience faces one way, like a cinema
    CENTRE_STAGE = "CENTRE_STAGE"  # in the round, audience surrounds the stage
```

Replace the whole `class Venue(Base):` block with:

```python
class Venue(Base):
    __tablename__ = "Venue"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text)
    address: Mapped[str] = mapped_column(Text)

    #: Admin-owned capabilities. An organiser books a venue; it does not book them.
    stage_layout: Mapped[StageLayout] = mapped_column(
        "stageLayout", pg_enum(StageLayout, "StageLayout"), default=StageLayout.END_STAGE
    )
    #: Which event types may be scheduled here. A CENTRE_STAGE venue may not
    #: allow MOVIE — nobody projects a film in the round.
    allowed_event_types: Mapped[list[EventType]] = mapped_column(
        "allowedEventTypes",
        ARRAY(pg_enum(EventType, "EventType")),
        default=lambda: [EventType.MOVIE, EventType.CONCERT],
    )
    #: Minutes the room stays unavailable after a show ends, for clearing and
    #: resetting. A stadium needs longer than a screening room.
    turnaround_minutes: Mapped[int] = mapped_column("turnaroundMinutes", Integer, default=15)

    seats: Mapped[list[Seat]] = relationship(back_populates="venue")
    events: Mapped[list[Event]] = relationship(back_populates="venue")
```

Add `StageLayout` to the `__all__` list at the bottom of the file.

- [ ] **Step 4: Write the migration**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m alembic revision -m "venue capabilities"`

Then replace the generated file's `upgrade`/`downgrade` with:

```python
def upgrade() -> None:
    """
    Venues become admin-owned infrastructure with capabilities, rather than a
    name and an address.

    Existing venues keep working: END_STAGE allowing both event types is exactly
    what they implicitly were, so the server defaults backfill them for free.
    """
    stage_layout = postgresql.ENUM("END_STAGE", "CENTRE_STAGE", name="StageLayout")
    # checkfirst so re-running against a partially migrated database is safe.
    stage_layout.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "Venue",
        sa.Column(
            "stageLayout",
            postgresql.ENUM("END_STAGE", "CENTRE_STAGE", name="StageLayout", create_type=False),
            nullable=False,
            server_default="END_STAGE",
        ),
    )
    op.add_column(
        "Venue",
        sa.Column(
            "allowedEventTypes",
            postgresql.ARRAY(
                postgresql.ENUM("MOVIE", "CONCERT", name="EventType", create_type=False)
            ),
            nullable=False,
            server_default=sa.text("ARRAY['MOVIE','CONCERT']::\"EventType\"[]"),
        ),
    )
    op.add_column(
        "Venue",
        sa.Column("turnaroundMinutes", sa.Integer(), nullable=False, server_default="15"),
    )


def downgrade() -> None:
    op.drop_column("Venue", "turnaroundMinutes")
    op.drop_column("Venue", "allowedEventTypes")
    op.drop_column("Venue", "stageLayout")
    postgresql.ENUM(name="StageLayout").drop(op.get_bind(), checkfirst=True)
```

Make sure the file imports what it uses — the top of the generated file needs:

```python
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
```

- [ ] **Step 5: Apply it**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m alembic upgrade head`
Expected: `Running upgrade 9bfb11a52e4a -> <rev>, venue capabilities`

- [ ] **Step 6: Extend the request and response schemas**

In `apps/api/src/ticket_api/modules/venues/schemas.py`, add the import:

```python
from ...models import EventType, StageLayout
```

Replace `CreateVenueInput` and `UpdateVenueInput`:

```python
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
```

Add the three fields to `VenueBase`, `VenueDetail` and `VenueSummary` — each gains:

```python
    stageLayout: StageLayout  # noqa: N815 - wire format
    allowedEventTypes: list[EventType]  # noqa: N815 - wire format
    turnaroundMinutes: int  # noqa: N815 - wire format
```

- [ ] **Step 7: Validate and persist in the service**

In `apps/api/src/ticket_api/modules/venues/service.py`, add after the `ROW_LABELS` constant:

```python
def _assert_capabilities_coherent(
    stage_layout: StageLayout, allowed: list[EventType]
) -> None:
    """
    A centre-stage venue may not allow MOVIE.

    Nobody projects a film in the round, and refusing it here beats discovering
    it when a cinema's seat map renders as a circle.
    """
    if stage_layout is StageLayout.CENTRE_STAGE and EventType.MOVIE in allowed:
        raise ApiError.bad_request(
            "CENTRE_STAGE_CANNOT_SHOW_MOVIES",
            "A centre-stage venue surrounds the stage, so it cannot host a film. "
            "Allow CONCERT only, or use END_STAGE.",
        )


def _venue_base(venue: Venue) -> VenueBase:
    return VenueBase(
        id=venue.id,
        name=venue.name,
        address=venue.address,
        stageLayout=venue.stage_layout,
        allowedEventTypes=list(venue.allowed_event_types),
        turnaroundMinutes=venue.turnaround_minutes,
    )
```

Replace `create_venue`:

```python
async def create_venue(data: CreateVenueInput) -> VenueBase:
    _assert_capabilities_coherent(data.stageLayout, data.allowedEventTypes)
    venue = Venue(
        name=data.name,
        address=data.address,
        stage_layout=data.stageLayout,
        allowed_event_types=data.allowedEventTypes,
        turnaround_minutes=data.turnaroundMinutes,
    )
    async with Session() as session:
        session.add(venue)
        await session.commit()
        await session.refresh(venue)
    return _venue_base(venue)
```

Replace `update_venue`:

```python
async def update_venue(venue_id: str, data: UpdateVenueInput) -> VenueBase:
    async with Session() as session:
        venue = (
            (await session.execute(select(Venue).where(Venue.id == venue_id)))
            .scalars()
            .first()
        )
        if venue is None:  # 404 before anything else
            raise ApiError.not_found("VENUE_NOT_FOUND", "No venue with that id.")

        # Merge BEFORE checking, so changing only one half of the pair cannot
        # produce an incoherent venue.
        _assert_capabilities_coherent(
            data.stageLayout or venue.stage_layout,
            data.allowedEventTypes or list(venue.allowed_event_types),
        )

        if data.name is not None:
            venue.name = data.name
        if data.address is not None:
            venue.address = data.address
        if data.stageLayout is not None:
            venue.stage_layout = data.stageLayout
        if data.allowedEventTypes is not None:
            venue.allowed_event_types = data.allowedEventTypes
        if data.turnaroundMinutes is not None:
            venue.turnaround_minutes = data.turnaroundMinutes

        await session.commit()
        await session.refresh(venue)

    return _venue_base(venue)
```

In `list_venues`, replace the returned `VenueSummary(...)` with:

```python
        VenueSummary(
            id=venue.id,
            name=venue.name,
            address=venue.address,
            stageLayout=venue.stage_layout,
            allowedEventTypes=list(venue.allowed_event_types),
            turnaroundMinutes=venue.turnaround_minutes,
            count=SeatCount(seats=seats),
        )
```

In `get_venue`, replace the returned `VenueDetail(...)` with:

```python
    return VenueDetail(
        id=venue.id,
        name=venue.name,
        address=venue.address,
        stageLayout=venue.stage_layout,
        allowedEventTypes=list(venue.allowed_event_types),
        turnaroundMinutes=venue.turnaround_minutes,
        seats=[SeatOut.model_validate(s) for s in seats],
    )
```

Update the imports at the top of the file:

```python
from ...models import EventType, Seat, StageLayout, Venue
```

- [ ] **Step 8: Run the tests**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/test_venue_capabilities.py -q`
Expected: PASS, 7 tests.

- [ ] **Step 9: Lint and run the whole suite**

Run:
```bash
cd apps/api && ./.venv/bin/python -m ruff check --fix src tests && ./.venv/bin/python -m ruff format src tests
NODE_ENV=test ./.venv/bin/python -m pytest -q
```
Expected: 133 passed.

- [ ] **Step 10: Commit**

```bash
git add apps/api
git commit -m "$(cat <<'EOF'
Give venues admin-owned capabilities

A venue is now infrastructure with a stage layout, the event types it permits,
and a turnaround window, rather than a name and an address. Existing venues need
no backfill: END_STAGE allowing both types is exactly what they implicitly were,
so the server defaults cover them.

One validation earns its place: a CENTRE_STAGE venue may not allow MOVIE. Nobody
projects a film in the round, and refusing it at creation beats discovering it
when a cinema's seat map renders as a circle. update_venue merges before
checking, so flipping one half of the pair cannot produce an incoherent venue —
which is its own test.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Radial seat generation

**Files:**
- Modify: `apps/api/src/ticket_api/modules/venues/schemas.py`
- Modify: `apps/api/src/ticket_api/modules/venues/service.py`
- Modify: `apps/api/tests/test_venue_capabilities.py`

**Interfaces:**
- Consumes: `generate_end_stage_block` / `generate_centre_stage_block` (Task 1); `Venue.stage_layout` (Task 2)
- Produces: `AddSeatBlockInput` gains `arcStartDegrees` and `arcSpanDegrees`

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_venue_capabilities.py`:

```python
import math  # noqa: E402 - grouped with the radial tests below


async def _make_venue(client, auth, admin, **caps) -> str:
    body = {"name": f"V{len(caps)}", "address": "x", **caps}
    r = await client.post(VENUES, json=body, headers=auth(admin[1]))
    assert r.status_code == 201, r.text
    return r.json()["venue"]["id"]


async def test_an_end_stage_venue_produces_a_grid(client, auth, admin):
    venue = await _make_venue(client, auth, admin)
    r = await client.post(
        f"{VENUES}/{venue}/seats",
        json={"section": "Stalls", "rows": 2, "seatsPerRow": 4},
        headers=auth(admin[1]),
    )
    assert r.status_code == 201, r.text

    seats = (await client.get(f"{VENUES}/{venue}")).json()["venue"]["seats"]
    assert len(seats) == 8
    # A grid has exactly as many distinct posY values as it has rows.
    assert len({s["posY"] for s in seats}) == 2


async def test_a_centre_stage_venue_places_every_seat_on_its_row_radius(
    client, auth, admin
):
    venue = await _make_venue(
        client, auth, admin, stageLayout="CENTRE_STAGE", allowedEventTypes=["CONCERT"]
    )
    r = await client.post(
        f"{VENUES}/{venue}/seats",
        json={
            "section": "Ring A",
            "rows": 2,
            "seatsPerRow": 8,
            "arcStartDegrees": 0,
            "arcSpanDegrees": 360,
        },
        headers=auth(admin[1]),
    )
    assert r.status_code == 201, r.text

    seats = (await client.get(f"{VENUES}/{venue}")).json()["venue"]["seats"]
    assert len(seats) == 16

    radii = {round(math.hypot(s["posX"], s["posY"]), 6) for s in seats}
    # Two rows means two distinct radii...
    assert len(radii) == 2
    # ...and a ring is not a grid: many distinct posY values, not two.
    assert len({round(s["posY"], 6) for s in seats}) > 2


async def test_a_second_centre_stage_block_sits_outside_the_first(client, auth, admin):
    venue = await _make_venue(
        client, auth, admin, stageLayout="CENTRE_STAGE", allowedEventTypes=["CONCERT"]
    )
    for section in ("Inner", "Outer"):
        r = await client.post(
            f"{VENUES}/{venue}/seats",
            json={"section": section, "rows": 1, "seatsPerRow": 6},
            headers=auth(admin[1]),
        )
        assert r.status_code == 201, r.text

    seats = (await client.get(f"{VENUES}/{venue}")).json()["venue"]["seats"]

    def radius(section: str) -> float:
        s = next(x for x in seats if x["section"] == section)
        return math.hypot(s["posX"], s["posY"])

    assert radius("Outer") > radius("Inner")


async def test_the_arc_fields_are_ignored_for_an_end_stage_venue(client, auth, admin):
    """They apply only to CENTRE_STAGE; sending them must not corrupt a grid."""
    venue = await _make_venue(client, auth, admin)
    await client.post(
        f"{VENUES}/{venue}/seats",
        json={
            "section": "Stalls",
            "rows": 1,
            "seatsPerRow": 4,
            "arcStartDegrees": 90,
            "arcSpanDegrees": 45,
        },
        headers=auth(admin[1]),
    )
    seats = (await client.get(f"{VENUES}/{venue}")).json()["venue"]["seats"]
    assert sum(s["posX"] for s in seats) == 0
    assert len({s["posY"] for s in seats}) == 1
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/test_venue_capabilities.py -q`
Expected: FAIL — the centre-stage venue still produces a grid, so the radii set has more than 2 entries.

- [ ] **Step 3: Accept the arc in the request schema**

In `apps/api/src/ticket_api/modules/venues/schemas.py`, replace `AddSeatBlockInput`:

```python
class AddSeatBlockInput(BaseModel):
    """
    Bulk seat creation: one named section.

    Rows are labelled A, B, C... so 26 is the ceiling — past that the labels
    would need a second letter, and nothing in this project needs a 27-row
    section. ponytail: if a venue ever does, switch to AA/AB here and nowhere
    else.

    The arc fields apply only to a CENTRE_STAGE venue and are ignored otherwise.
    Defaulting to a full circle means a single-section ring needs no extra input;
    four 90-degree blocks build a venue with four wedges.
    """

    model_config = ConfigDict(extra="ignore")

    section: str = Field(min_length=1, max_length=40)
    rows: int = Field(ge=1, le=26)
    seatsPerRow: int = Field(ge=1, le=60)  # noqa: N815 - wire format
    arcStartDegrees: float = Field(default=0, ge=0, le=360)  # noqa: N815 - wire format
    arcSpanDegrees: float = Field(default=360, gt=0, le=360)  # noqa: N815 - wire format

    _normalise = field_validator("section", mode="before")(_trim)
```

- [ ] **Step 4: Branch on layout in the service**

In `apps/api/src/ticket_api/modules/venues/service.py`, add the import:

```python
from ...lib.geometry import generate_centre_stage_block, generate_end_stage_block
```

Replace the whole `add_seat_block` function, and delete the now-unused module-level `ROW_LABELS`:

```python
async def add_seat_block(venue_id: str, data: AddSeatBlockInput) -> SeatBlockResult:
    """
    Generates a block of seats using whichever layout the venue was built for.

    A new block is always placed outside or below everything already there, so
    sections never overlap and the caller never computes an offset.
    """
    venue = await get_venue(venue_id)  # 404 before anything else

    async with Session() as session:
        if venue.stageLayout is StageLayout.CENTRE_STAGE:
            start = await _outermost_radius(session, venue_id)
            positions = generate_centre_stage_block(
                rows=data.rows,
                seats_per_row=data.seatsPerRow,
                start_radius=start + 2,
                arc_start_degrees=data.arcStartDegrees,
                arc_span_degrees=data.arcSpanDegrees,
            )
        else:
            start = await _lowest_row(session, venue_id)
            positions = generate_end_stage_block(
                rows=data.rows, seats_per_row=data.seatsPerRow, start_y=start + 2
            )

        seats = [
            Seat(
                venue_id=venue_id,
                section=data.section,
                row=p.row,
                number=p.number,
                pos_x=p.pos_x,
                pos_y=p.pos_y,
            )
            for p in positions
        ]
        session.add_all(seats)

        try:
            await session.commit()
        except IntegrityError as err:
            await session.rollback()
            # unique(venueId, section, row, number) — re-adding the same block.
            if isinstance(err.orig, UniqueViolation):
                raise ApiError.conflict(
                    "SEATS_ALREADY_EXIST",
                    f'Section "{data.section}" already has seats with those row '
                    "and number labels.",
                ) from err
            raise

    return SeatBlockResult(created=len(seats), section=data.section, startY=start + 2)


async def _lowest_row(session: AsyncSession, venue_id: str) -> float:
    """Lowest occupied grid row, or -2 so the first block starts at y = 0."""
    lowest = await session.scalar(
        select(func.max(Seat.pos_y)).where(Seat.venue_id == venue_id)
    )
    return -2.0 if lowest is None else float(lowest)


async def _outermost_radius(session: AsyncSession, venue_id: str) -> float:
    """
    Radius of the outermost existing seat, or 1 so the first ring starts at 3 —
    far enough out to leave room for the stage in the middle.
    """
    seats = (
        (
            await session.execute(
                select(Seat.pos_x, Seat.pos_y).where(Seat.venue_id == venue_id)
            )
        )
        .all()
    )
    if not seats:
        return 1.0
    return max(math.hypot(float(x), float(y)) for x, y in seats)
```

Add these imports to the top of the file:

```python
import math

from sqlalchemy.ext.asyncio import AsyncSession
```

- [ ] **Step 5: Run the tests**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/test_venue_capabilities.py -q`
Expected: PASS, 11 tests.

- [ ] **Step 6: Lint and run the whole suite**

Run:
```bash
cd apps/api && ./.venv/bin/python -m ruff check --fix src tests && ./.venv/bin/python -m ruff format src tests
NODE_ENV=test ./.venv/bin/python -m pytest -q
```
Expected: 137 passed.

- [ ] **Step 7: Commit**

```bash
git add apps/api
git commit -m "$(cat <<'EOF'
Generate radial seating for centre-stage venues

The venue builder now branches on the venue's stage layout, delegating both
cases to the pure geometry module. A centre-stage block places rows as radii and
spreads seats along an arc, defaulting to a full circle so a single-section ring
needs no extra input while four 90-degree blocks build four wedges.

Each new block is placed outside or below everything already there, so sections
never overlap and the caller never computes an offset — the same guarantee the
grid layout already made, expressed in polar terms.

Because both layouts emit plain posX/posY grid units, the seat map renderer
needs no change whatsoever.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Event-type gate

**Files:**
- Modify: `apps/api/src/ticket_api/modules/events/service.py`
- Modify: `apps/api/tests/test_venues_events.py`

**Interfaces:**
- Consumes: `Venue.allowed_event_types` (Task 2)
- Produces: error code `EVENT_TYPE_NOT_ALLOWED` (400)

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_venues_events.py`:

```python
async def test_an_event_type_the_venue_forbids_is_refused(client, auth, admin, organiser):
    """An organiser books a venue rather than owning it."""
    venue = (
        await client.post(
            VENUES,
            json={"name": "ConcertOnly", "address": "x", "allowedEventTypes": ["CONCERT"]},
            headers=auth(admin[1]),
        )
    ).json()["venue"]["id"]

    r = await client.post(
        EVENTS,
        json={"venueId": venue, "title": "Film", "type": "MOVIE"},
        headers=auth(organiser[1]),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "EVENT_TYPE_NOT_ALLOWED"
    # The message must name what the venue DOES allow, or the organiser guesses.
    assert "CONCERT" in r.json()["error"]["message"]


async def test_a_permitted_event_type_still_works(client, auth, admin, organiser):
    venue = (
        await client.post(
            VENUES,
            json={"name": "ConcertOnly2", "address": "x", "allowedEventTypes": ["CONCERT"]},
            headers=auth(admin[1]),
        )
    ).json()["venue"]["id"]

    r = await client.post(
        EVENTS,
        json={"venueId": venue, "title": "Gig", "type": "CONCERT"},
        headers=auth(organiser[1]),
    )
    assert r.status_code == 201, r.text
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/test_venues_events.py -q`
Expected: FAIL — got 201, expected 400.

- [ ] **Step 3: Add the gate**

In `apps/api/src/ticket_api/modules/events/service.py`, replace the venue lookup at the top of `create_event`:

```python
async def create_event(data: CreateEventInput, caller: TokenPayload) -> EventWritten:
    async with Session() as session:
        venue = (
            (await session.execute(select(Venue).where(Venue.id == data.venueId)))
            .scalars()
            .first()
        )
        if venue is None:
            raise ApiError.bad_request("VENUE_NOT_FOUND", "No venue with that id.")

        # A venue is admin-owned infrastructure; an organiser books it, and
        # cannot put a film in a room built for concerts.
        if data.type not in venue.allowed_event_types:
            allowed = " and ".join(t.value for t in venue.allowed_event_types)
            raise ApiError.bad_request(
                "EVENT_TYPE_NOT_ALLOWED", f"This venue hosts {allowed} only."
            )

        event = Event(
            venue_id=data.venueId,
            title=data.title,
            type=data.type,
            description=data.description,
            organiser_id=caller["sub"],
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)

    return EventWritten(
        id=event.id,
        title=event.title,
        type=event.type,
        description=event.description,
        venueId=event.venue_id,
    )
```

- [ ] **Step 4: Run the tests**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/test_venues_events.py -q`
Expected: PASS.

- [ ] **Step 5: Lint and run the whole suite**

Run:
```bash
cd apps/api && ./.venv/bin/python -m ruff check --fix src tests && ./.venv/bin/python -m ruff format src tests
NODE_ENV=test ./.venv/bin/python -m pytest -q
```
Expected: 139 passed.

- [ ] **Step 6: Commit**

```bash
git add apps/api
git commit -m "$(cat <<'EOF'
Refuse events a venue does not permit

An organiser books a venue rather than owning it, so a room an admin marked
concert-only cannot host a film. The error names what the venue does allow, so
the organiser is not left guessing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Show scheduling fields

**Files:**
- Modify: `apps/api/src/ticket_api/models.py`
- Create: `apps/api/src/ticket_api/modules/venues/scheduling.py`
- Create: `apps/api/alembic/versions/<rev>_show_scheduling.py`
- Modify: `apps/api/src/ticket_api/modules/events/schemas.py`
- Modify: `apps/api/src/ticket_api/modules/events/service.py`
- Modify: `apps/api/tests/conftest.py`
- Modify: `apps/api/src/ticket_api/seed.py`
- Create: `apps/api/tests/concurrency/test_scheduling.py`

**Interfaces:**
- Consumes: `Venue.turnaround_minutes` (Task 2)
- Produces:
  - `ShowStatus` enum with `SCHEDULED`, `CANCELLED`
  - `Show.venue_id`, `Show.duration_minutes`, `Show.ends_at`, `Show.occupies_until`, `Show.status`
  - `occupied_window(starts_at: datetime, duration_minutes: int, turnaround_minutes: int) -> tuple[datetime, datetime]` returning `(ends_at, occupies_until)`
  - `CreateShowInput` gains required `durationMinutes`

> **Cross-task hazard.** `Show` gains four NOT NULL columns. The `make_show`
> fixture in `conftest.py` and the `seed.py` script both construct `Show(...)`
> directly and **will break every test** until Step 7 updates them. Do not stop
> half way through this task.

- [ ] **Step 1: Write the failing test for the window maths**

Create `apps/api/tests/concurrency/test_scheduling.py`:

```python
"""Venue availability, including the parallel case."""

from __future__ import annotations

from datetime import datetime

from ticket_api.modules.venues.scheduling import occupied_window


def test_the_window_runs_to_the_end_of_the_show_plus_turnaround():
    ends_at, occupies_until = occupied_window(
        starts_at=datetime(2026, 9, 1, 18, 0), duration_minutes=120, turnaround_minutes=15
    )
    assert ends_at == datetime(2026, 9, 1, 20, 0)
    assert occupies_until == datetime(2026, 9, 1, 20, 15)


def test_a_zero_turnaround_frees_the_room_the_moment_the_show_ends():
    ends_at, occupies_until = occupied_window(
        starts_at=datetime(2026, 9, 1, 18, 0), duration_minutes=90, turnaround_minutes=0
    )
    assert ends_at == occupies_until == datetime(2026, 9, 1, 19, 30)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/concurrency/test_scheduling.py -q`
Expected: FAIL — `ModuleNotFoundError: ticket_api.modules.venues.scheduling`

- [ ] **Step 3: Write the window maths**

Create `apps/api/src/ticket_api/modules/venues/scheduling.py`:

```python
"""
Venue availability.

The window a show occupies is longer than the show: the room has to empty, be
cleaned, and be reset before anybody else can use it. Turnaround is a venue
property because a stadium needs longer than a screening room.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def occupied_window(
    *, starts_at: datetime, duration_minutes: int, turnaround_minutes: int
) -> tuple[datetime, datetime]:
    """Returns (ends_at, occupies_until)."""
    ends_at = starts_at + timedelta(minutes=duration_minutes)
    return ends_at, ends_at + timedelta(minutes=turnaround_minutes)
```

- [ ] **Step 4: Run it**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/concurrency/test_scheduling.py -q`
Expected: PASS, 2 tests.

- [ ] **Step 5: Add the model columns**

In `apps/api/src/ticket_api/models.py`, add after the `StageLayout` enum:

```python
class ShowStatus(enum.StrEnum):
    SCHEDULED = "SCHEDULED"
    CANCELLED = "CANCELLED"
```

Replace the whole `class Show(Base):` block:

```python
class Show(Base):
    __tablename__ = "Show"
    __table_args__ = (Index("Show_venueId_startsAt_idx", "venueId", "startsAt"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column("eventId", Text, ForeignKey("Event.id"))

    #: Denormalised from event.venue so the venue-overlap exclusion constraint —
    #: which can only span one table — has something to key on. Safe because
    #: Event.venueId is immutable: moving an event would orphan every ShowSeat
    #: generated against the old venue's seats.
    venue_id: Mapped[str] = mapped_column("venueId", Text)

    starts_at: Mapped[datetime] = mapped_column("startsAt", ts())
    #: Supplied by the organiser; there is no sensible default for "how long is
    #: this show".
    duration_minutes: Mapped[int] = mapped_column("durationMinutes", Integer)
    ends_at: Mapped[datetime] = mapped_column("endsAt", ts())
    #: endsAt plus the venue's turnaround. This, not endsAt, is what blocks the
    #: room for another organiser.
    occupies_until: Mapped[datetime] = mapped_column("occupiesUntil", ts())
    status: Mapped[ShowStatus] = mapped_column(
        pg_enum(ShowStatus, "ShowStatus"), default=ShowStatus.SCHEDULED
    )

    event: Mapped[Event] = relationship(back_populates="shows")
    show_seats: Mapped[list[ShowSeat]] = relationship(back_populates="show")
    waitlist_entries: Mapped[list[WaitlistEntry]] = relationship(back_populates="show")
    bookings: Mapped[list[Booking]] = relationship(back_populates="show")
```

Add `ShowStatus` to `__all__`.

- [ ] **Step 6: Write the migration**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m alembic revision -m "show scheduling"`

Replace the generated `upgrade`/`downgrade`:

```python
def upgrade() -> None:
    """
    A show becomes a booking of a venue for a window of time, so two organisers
    can no longer schedule overlapping shows in one room.

    Columns are added nullable, backfilled, then made NOT NULL, so existing rows
    survive. Existing shows get a 120-minute duration: there is no way to recover
    the real value, and two hours is defensible for both a film and a gig.
    """
    show_status = postgresql.ENUM("SCHEDULED", "CANCELLED", name="ShowStatus")
    show_status.create(op.get_bind(), checkfirst=True)

    op.add_column("Show", sa.Column("venueId", sa.Text(), nullable=True))
    op.add_column("Show", sa.Column("durationMinutes", sa.Integer(), nullable=True))
    op.add_column("Show", sa.Column("endsAt", postgresql.TIMESTAMP(precision=3), nullable=True))
    op.add_column("Show", sa.Column("occupiesUntil", postgresql.TIMESTAMP(precision=3), nullable=True))
    op.add_column(
        "Show",
        sa.Column(
            "status",
            postgresql.ENUM("SCHEDULED", "CANCELLED", name="ShowStatus", create_type=False),
            nullable=False,
            server_default="SCHEDULED",
        ),
    )

    op.execute(
        """
        UPDATE "Show" s
        SET "venueId"         = e."venueId",
            "durationMinutes" = 120,
            "endsAt"          = s."startsAt" + INTERVAL '120 minutes',
            "occupiesUntil"   = s."startsAt" + INTERVAL '120 minutes'
                                + (v."turnaroundMinutes" * INTERVAL '1 minute')
        FROM "Event" e
        JOIN "Venue" v ON v.id = e."venueId"
        WHERE e.id = s."eventId"
        """
    )

    for column in ("venueId", "durationMinutes", "endsAt", "occupiesUntil"):
        op.alter_column("Show", column, nullable=False)

    op.create_index("Show_venueId_startsAt_idx", "Show", ["venueId", "startsAt"])


def downgrade() -> None:
    op.drop_index("Show_venueId_startsAt_idx", table_name="Show")
    for column in ("status", "occupiesUntil", "endsAt", "durationMinutes", "venueId"):
        op.drop_column("Show", column)
    postgresql.ENUM(name="ShowStatus").drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 7: Fix the two direct `Show(...)` constructors before applying**

In `apps/api/tests/conftest.py`, inside `make_show`, replace the `Show(...)` construction:

```python
            # Pinned to a fixed hour, NOT the current wall-clock time.
            # Corrected during execution: an unpinned time made the
            # venue-scheduling tests fail for a few hours every afternoon,
            # because this fixture's own background show collided with the fixed
            # hours those tests book. 09:00 + 120min + 15min turnaround = clear
            # by 11:15, well before the 18:00-21:00 band they use.
            starts_at = (utcnow() + timedelta(days=30)).replace(
                hour=9, minute=0, second=0, microsecond=0
            )
            ends_at, occupies_until = occupied_window(
                starts_at=starts_at,
                duration_minutes=120,
                turnaround_minutes=venue.turnaround_minutes,
            )
            show = Show(
                event_id=event.id,
                venue_id=venue.id,
                starts_at=starts_at,
                duration_minutes=120,
                ends_at=ends_at,
                occupies_until=occupies_until,
            )
```

and add to its imports:

```python
from ticket_api.modules.venues.scheduling import occupied_window  # noqa: E402
```

In `apps/api/src/ticket_api/seed.py`, replace the `Show(...)` construction inside the shows loop:

```python
        async with transaction() as session:
            ends_at, occupies_until = occupied_window(
                starts_at=starts_at,
                duration_minutes=169,  # Interstellar's actual runtime
                turnaround_minutes=15,
            )
            show = Show(
                event_id=event_id,
                venue_id=venue_id,
                starts_at=starts_at,
                duration_minutes=169,
                ends_at=ends_at,
                occupies_until=occupies_until,
            )
```

and add the import:

```python
from .modules.venues.scheduling import occupied_window
```

- [ ] **Step 8: Apply the migration**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m alembic upgrade head`
Expected: `Running upgrade <prev> -> <rev>, show scheduling`

- [ ] **Step 9: Require duration on show creation**

In `apps/api/src/ticket_api/modules/events/schemas.py`, replace `CreateShowInput`:

```python
class CreateShowInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    startsAt: datetime  # noqa: N815 - wire format
    # No default: only the organiser knows how long their show runs, and guessing
    # would silently block the wrong amount of venue time.
    durationMinutes: int = Field(ge=5, le=24 * 60)  # noqa: N815 - wire format

    _naive = field_validator("startsAt", mode="before")(_naive_utc)

    @field_validator("startsAt")
    @classmethod
    def _in_the_future(cls, v: datetime) -> datetime:
        if v <= utcnow():
            raise ValueError("Show must start in the future.")
        return v
```

- [ ] **Step 10: Populate the new columns in `create_show`**

In `apps/api/src/ticket_api/modules/events/service.py`, replace `create_show`:

```python
async def create_show(event_id: str, data: CreateShowInput, caller: TokenPayload) -> ShowCreated:
    async with Session() as session:
        event = await _assert_owns(session, event_id, caller)
        venue = (
            (await session.execute(select(Venue).where(Venue.id == event.venue_id)))
            .scalars()
            .one()
        )
        venue_id = venue.id
        turnaround = venue.turnaround_minutes

    ends_at, occupies_until = occupied_window(
        starts_at=data.startsAt,
        duration_minutes=data.durationMinutes,
        turnaround_minutes=turnaround,
    )

    # One transaction: a show whose seats failed to generate is worse than no
    # show at all — it renders as a bookable date with an empty seat map.
    async with transaction() as session:
        show = Show(
            event_id=event_id,
            venue_id=venue_id,
            starts_at=data.startsAt,
            duration_minutes=data.durationMinutes,
            ends_at=ends_at,
            occupies_until=occupies_until,
        )
        session.add(show)
        await session.flush()

        seat_count = await instantiate_show_seats(
            session, show_id=show.id, event_id=event_id, venue_id=venue_id
        )
        show_id, starts_at = show.id, show.starts_at

    return ShowCreated(id=show_id, startsAt=iso(starts_at) or "", seatCount=seat_count)
```

Add the import:

```python
from ..venues.scheduling import occupied_window
```

- [ ] **Step 11: Fix every test that creates a show through the API**

Every existing `POST /events/{id}/shows` call now needs `durationMinutes`. Run the
suite and add `"durationMinutes": 120` to each failing request body:

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest -q`
Expected: failures in `tests/test_venues_events.py` only, all 400 `VALIDATION_FAILED`.
Fix each, then re-run.

- [ ] **Step 12: Lint and run the whole suite**

Run:
```bash
cd apps/api && ./.venv/bin/python -m ruff check --fix src tests && ./.venv/bin/python -m ruff format src tests
NODE_ENV=test ./.venv/bin/python -m pytest -q
```
Expected: 141 passed.

- [ ] **Step 13: Verify the seed still works**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m ticket_api.seed`
Expected: two shows created, 100 seats each. Then re-run it and confirm both say "already seeded".

- [ ] **Step 14: Commit**

```bash
git add apps/api
git commit -m "$(cat <<'EOF'
Model a show as a booking of a venue for a window of time

A show now carries its duration, its end, and the point at which the room
becomes free again — which is later than the end, because the room has to empty,
be cleaned and be reset. Turnaround is a venue property since a stadium needs
longer than a screening room.

venueId is denormalised onto Show so the exclusion constraint in the next commit,
which can only span one table, has something to key on. That is safe because
Event.venueId is already immutable: moving an event would orphan every ShowSeat
generated against the old venue's seats. Same trade priceAtBooking already makes.

Existing rows are backfilled with a 120-minute duration. The real value is
unrecoverable and two hours is defensible for both a film and a gig; columns are
added nullable, backfilled, then made NOT NULL so nothing is lost.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Prevent double-booking a venue

**Files:**
- Modify: `apps/api/src/ticket_api/modules/venues/scheduling.py`
- Modify: `apps/api/src/ticket_api/modules/events/service.py`
- Create: `apps/api/alembic/versions/<rev>_show_no_venue_overlap.py`
- Modify: `apps/api/tests/concurrency/test_scheduling.py`
- Modify: `docs/DEBUGGING.md`

**Interfaces:**
- Consumes: `occupied_window` and the `Show` columns (Task 5)
- Produces: `assert_venue_free(session, venue_id, starts_at, occupies_until) -> None`; error code `VENUE_DOUBLE_BOOKED` (409)

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/concurrency/test_scheduling.py`:

```python
import asyncio  # noqa: E402
from datetime import timedelta  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from ticket_api.db import Session  # noqa: E402
from ticket_api.models import Role, Show, ShowStatus, utcnow  # noqa: E402


@pytest.fixture
async def shared_venue(client, auth, make_user, make_show):
    """
    Two organisers, two events, one venue. Each event is fully priced, so show
    creation is never blocked by pricing.
    """
    first = await make_show(seats=2)
    _, other_token = await make_user(Role.ORGANISER, "other")

    second = await client.post(
        "/api/v1/events",
        json={"venueId": first["venue_id"], "title": "Rival", "type": "CONCERT"},
        headers=auth(other_token),
    )
    assert second.status_code == 201, second.text
    second_event = second.json()["event"]["id"]

    priced = await client.post(
        f"/api/v1/events/{second_event}/categories",
        json={"name": "Main", "price": "100", "sections": ["Main"]},
        headers=auth(other_token),
    )
    assert priced.status_code == 201, priced.text

    return {
        "venue_id": first["venue_id"],
        "a_event": first["event_id"],
        "a_token": first["organiser_token"],
        "b_event": second_event,
        "b_token": other_token,
    }


def at(day_offset: int, hour: int) -> str:
    d = utcnow() + timedelta(days=30 + day_offset)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


async def _schedule(client, auth, event, token, starts_at, minutes):
    return await client.post(
        f"/api/v1/events/{event}/shows",
        json={"startsAt": starts_at, "durationMinutes": minutes},
        headers=auth(token),
    )


async def test_a_second_overlapping_show_is_refused(client, auth, shared_venue):
    v = shared_venue
    first = await _schedule(client, auth, v["a_event"], v["a_token"], at(0, 18), 120)
    assert first.status_code == 201, first.text

    # Starts an hour in, while the first show is still running.
    clash = await _schedule(client, auth, v["b_event"], v["b_token"], at(0, 19), 60)
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "VENUE_DOUBLE_BOOKED"


async def test_a_show_starting_inside_the_turnaround_is_refused(client, auth, shared_venue):
    v = shared_venue
    await _schedule(client, auth, v["a_event"], v["a_token"], at(1, 18), 60)
    # Ends 19:00; the default 15-minute turnaround runs to 19:15.
    too_soon = await _schedule(client, auth, v["b_event"], v["b_token"], at(1, 19), 60)
    assert too_soon.status_code == 409


async def test_a_show_starting_after_the_turnaround_is_accepted(client, auth, shared_venue):
    v = shared_venue
    await _schedule(client, auth, v["a_event"], v["a_token"], at(2, 18), 60)
    # Ends 19:00, free from 19:15. 20:00 is clear.
    later = await _schedule(client, auth, v["b_event"], v["b_token"], at(2, 20), 60)
    assert later.status_code == 201, later.text


async def test_the_same_organiser_cannot_double_book_either(client, auth, shared_venue):
    """The constraint is about the room, not about who is asking."""
    v = shared_venue
    await _schedule(client, auth, v["a_event"], v["a_token"], at(3, 18), 120)
    clash = await _schedule(client, auth, v["a_event"], v["a_token"], at(3, 19), 60)
    assert clash.status_code == 409


async def test_cancelling_a_show_frees_its_slot(client, auth, shared_venue):
    """
    The constraint is partial on status, so a cancelled show stops blocking with
    no cleanup code anywhere.
    """
    v = shared_venue
    created = await _schedule(client, auth, v["a_event"], v["a_token"], at(4, 18), 60)
    show_id = created.json()["show"]["id"]

    blocked = await _schedule(client, auth, v["b_event"], v["b_token"], at(4, 18), 60)
    assert blocked.status_code == 409

    async with Session() as session:
        show = (
            (await session.execute(select(Show).where(Show.id == show_id))).scalars().one()
        )
        show.status = ShowStatus.CANCELLED
        await session.commit()

    freed = await _schedule(client, auth, v["b_event"], v["b_token"], at(4, 18), 60)
    assert freed.status_code == 201, freed.text


async def test_the_database_refuses_an_overlap_even_bypassing_the_application(
    client, auth, shared_venue
):
    v = shared_venue
    starts = at(5, 18)
    created = await _schedule(client, auth, v["a_event"], v["a_token"], starts, 60)
    assert created.status_code == 201, created.text

    from datetime import datetime

    slot = datetime.fromisoformat(starts)
    async with Session() as session:
        session.add(
            Show(
                event_id=v["b_event"],
                venue_id=v["venue_id"],
                starts_at=slot,
                duration_minutes=60,
                ends_at=slot + timedelta(minutes=60),
                occupies_until=slot + timedelta(minutes=75),
            )
        )
        with pytest.raises(Exception):  # noqa: B017 - any DB refusal is the point
            await session.commit()


async def test_two_organisers_racing_for_one_slot_exactly_one_wins(
    live_server, client, auth, shared_venue
):
    v = shared_venue
    starts = at(6, 18)

    async with httpx.AsyncClient(base_url=live_server, timeout=60.0) as http:
        a, b = await asyncio.gather(
            http.post(
                f"/api/v1/events/{v['a_event']}/shows",
                json={"startsAt": starts, "durationMinutes": 90},
                headers=auth(v["a_token"]),
            ),
            http.post(
                f"/api/v1/events/{v['b_event']}/shows",
                json={"startsAt": starts, "durationMinutes": 90},
                headers=auth(v["b_token"]),
            ),
        )

    assert sorted([a.status_code, b.status_code]) == [201, 409], (a.text, b.text)

    async with Session() as session:
        count = len(
            (
                await session.execute(
                    select(Show).where(
                        Show.venue_id == v["venue_id"],
                        Show.status == ShowStatus.SCHEDULED,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert count == 1, "two shows were scheduled in one venue at one time"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/concurrency/test_scheduling.py -q`
Expected: FAIL — overlapping shows are currently both created.

- [ ] **Step 3: Write the exclusion-constraint migration**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m alembic revision -m "show no venue overlap"`

Replace the generated `upgrade`/`downgrade`:

```python
def upgrade() -> None:
    """
    The guarantee that survives an application bug: no two SCHEDULED shows may
    occupy one venue at overlapping times.

    tsrange, NOT tstzrange — the columns are TIMESTAMP(3) WITHOUT TIME ZONE, and
    the range type has to match the column type or the constraint will not build.

    WHERE status = 'SCHEDULED' is the elegant part: a cancelled show stops
    blocking its slot automatically, with no cleanup code anywhere. Same house
    style as BookingSeat_showSeatId_live_key — guard the live rows, let the dead
    ones stay for history.
    """
    # Equality on a text column inside a GiST exclusion constraint needs this.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        """
        ALTER TABLE "Show" ADD CONSTRAINT "show_no_venue_overlap"
          EXCLUDE USING gist (
            "venueId"                            WITH =,
            tsrange("startsAt", "occupiesUntil") WITH &&
          ) WHERE (status = 'SCHEDULED')
        """
    )


def downgrade() -> None:
    op.execute('ALTER TABLE "Show" DROP CONSTRAINT IF EXISTS "show_no_venue_overlap"')
    # btree_gist is left installed: dropping an extension another migration or
    # another application might rely on is not this migration's business.
```

- [ ] **Step 4: Apply it**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m alembic upgrade head`
Expected: applied. If it fails with `conflicting key value violates exclusion constraint`, seeded shows already overlap — inspect and cancel or move one. **Do not weaken the constraint to make it apply.**

- [ ] **Step 5: Add the application-level check**

Append to `apps/api/src/ticket_api/modules/venues/scheduling.py`:

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...errors import ApiError

# Locks the venue's scheduled shows before checking, so two simultaneous
# organisers serialise here rather than both passing the check and racing to
# insert.
_CLASHING_SHOWS = text(
    """
    SELECT id, "startsAt", "occupiesUntil"
    FROM "Show"
    WHERE "venueId" = :venue_id
      AND status = 'SCHEDULED'
      AND "startsAt" < :occupies_until
      AND "occupiesUntil" > :starts_at
    ORDER BY "startsAt"
    FOR UPDATE
    """
)


async def assert_venue_free(
    session: AsyncSession,
    *,
    venue_id: str,
    starts_at: datetime,
    occupies_until: datetime,
) -> None:
    """
    Refuses to schedule a show that overlaps another in the same venue.

    Runs inside the caller's transaction. The exclusion constraint underneath is
    the real guarantee; this exists to turn a database error into a message that
    names the clashing show and says when the room actually frees.
    """
    clash = (
        (
            await session.execute(
                _CLASHING_SHOWS,
                {
                    "venue_id": venue_id,
                    "starts_at": starts_at,
                    "occupies_until": occupies_until,
                },
            )
        )
        .mappings()
        .first()
    )
    if clash is not None:
        raise ApiError.conflict(
            "VENUE_DOUBLE_BOOKED",
            f"This venue is already booked from {clash['startsAt'].isoformat()} "
            f"until {clash['occupiesUntil'].isoformat()}, including turnaround.",
        )
```

- [ ] **Step 6: Call it from `create_show`**

In `apps/api/src/ticket_api/modules/events/service.py`, inside `create_show`, add as the **first statement** inside the `async with transaction() as session:` block:

```python
        await assert_venue_free(
            session,
            venue_id=venue_id,
            starts_at=data.startsAt,
            occupies_until=occupies_until,
        )
```

and extend the import:

```python
from ..venues.scheduling import assert_venue_free, occupied_window
```

- [ ] **Step 7: Run the scheduling tests**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/concurrency/test_scheduling.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 8: Lint and run the whole suite**

Run:
```bash
cd apps/api && ./.venv/bin/python -m ruff check --fix src tests && ./.venv/bin/python -m ruff format src tests
NODE_ENV=test ./.venv/bin/python -m pytest -q
```
Expected: 148 passed. **`tests/concurrency/test_holds.py` must still be green.**

- [ ] **Step 9: Record the constraint in the debugging log**

In `docs/DEBUGGING.md`, immediately after the "A future autogenerate will try to drop the partial index" entry, add:

```markdown
### A future autogenerate will also try to drop the venue overlap constraint

**Symptom:** after an `alembic revision --autogenerate`, two shows can be
scheduled in one venue at overlapping times.
**Cause:** `show_no_venue_overlap` is a GiST **exclusion constraint**. SQLAlchemy
cannot represent one, so it does not appear in the models and autogenerate
proposes removing it as drift.
**Fix:** it belongs in the `show_no_venue_overlap` migration and nowhere else.
Delete the proposal, not the constraint. Note `tsrange`, not `tstzrange` — the
columns are `TIMESTAMP(3)` **without** time zone, and the range type has to match
the column type or the constraint will not build at all.
```

- [ ] **Step 10: Commit**

```bash
git add apps/api docs/DEBUGGING.md
git commit -m "$(cat <<'EOF'
Stop two organisers double-booking a venue

Show stored only startsAt, so overlapping shows in one hall both succeeded.
Invisible while an organiser implicitly owned a venue; a real defect now they
are tenants sharing one.

Two layers, as everywhere else in this codebase. assert_venue_free runs inside
the show-creation transaction and locks the venue's scheduled shows first, so
two simultaneous organisers serialise there rather than both passing the check
and racing to insert; it exists to turn a database error into a message naming
the clash and when the room actually frees. Underneath, a Postgres GiST
exclusion constraint provides the guarantee that survives an application bug —
tested by writing an overlap straight to the table, bypassing the service.

The constraint is partial on status, which is the elegant part: cancelling a
show stops it blocking its slot with no cleanup code anywhere. Same house style
as BookingSeat_showSeatId_live_key — guard the live rows, let the dead ones stay
for history. Both are invisible to the models, so both are now recorded in
DEBUGGING.md as drift a future autogenerate will try to remove.

tsrange rather than tstzrange, because the columns are TIMESTAMP(3) without time
zone and the range type must match or the constraint will not build.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Two-clock hold expiry

**Files:**
- Modify: `apps/api/src/ticket_api/config.py`
- Modify: `apps/api/.env.example`
- Modify: `apps/api/src/ticket_api/modules/seats/service.py`
- Modify: `apps/api/src/ticket_api/modules/seats/schemas.py`
- Modify: `apps/api/src/ticket_api/modules/seats/routes.py`
- Create: `apps/api/tests/test_holds_grace.py`

**Interfaces:**
- Consumes: nothing new
- Produces:
  - `settings.RELEASE_GRACE_SECONDS` (default 15); `HOLD_TTL_SECONDS` default 600 → 300
  - `release_holds(show_id, user_id) -> ReleaseResult` — now shortens rather than deletes, and returns `freeAt`
  - `extend_hold(show_id, user_id) -> ExtendResult`
  - Route `POST /shows/{show_id}/holds/extend`; error code `NO_ACTIVE_HOLD` (409)

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_holds_grace.py`:

```python
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update

from ticket_api.config import settings
from ticket_api.db import Session
from ticket_api.models import SeatStatus, ShowSeat, utcnow


async def _hold(client, auth, show, token):
    r = await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    return r


async def test_an_abandoned_hold_runs_for_the_full_ttl(client, auth, make_show, make_user):
    show = await make_show(seats=1)
    _, token = await make_user()
    r = await _hold(client, auth, show, token)

    from datetime import datetime

    expires = datetime.fromisoformat(r.json()["holdExpiresAt"].replace("Z", ""))
    seconds = (expires - utcnow()).total_seconds()
    assert abs(seconds - settings.HOLD_TTL_SECONDS) < 10, seconds


async def test_going_back_shortens_the_hold_instead_of_deleting_it(
    client, auth, make_show, make_user
):
    show = await make_show(seats=1)
    _, token = await make_user()
    await _hold(client, auth, show, token)

    r = await client.delete(f"/api/v1/shows/{show['show_id']}/holds", headers=auth(token))
    assert r.status_code == 200
    assert r.json()["released"] == 1
    assert r.json()["freeAt"].endswith("Z")

    async with Session() as session:
        row = (
            (await session.execute(select(ShowSeat).where(ShowSeat.id == show["seat_ids"][0])))
            .scalars()
            .one()
        )
    # Still HELD, still owned — just on a much shorter clock.
    assert row.status is SeatStatus.HELD
    assert row.held_by_user_id is not None, "the owner is kept so returning can reclaim it"

    seconds = (row.hold_expires_at - utcnow()).total_seconds()
    assert 0 < seconds <= settings.RELEASE_GRACE_SECONDS + 2, seconds


async def test_after_the_grace_elapses_another_customer_can_take_the_seat(
    client, auth, make_show, make_user
):
    """No sweeper runs here — lazy expiry alone must make it bookable."""
    show = await make_show(seats=1)
    _, first = await make_user()
    _, second = await make_user()
    await _hold(client, auth, show, first)
    await client.delete(f"/api/v1/shows/{show['show_id']}/holds", headers=auth(first))

    # Wind the clock past the grace rather than waiting it out.
    async with Session() as session:
        await session.execute(
            update(ShowSeat)
            .where(ShowSeat.id == show["seat_ids"][0])
            .values(hold_expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()

    r = await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(second),
    )
    assert r.status_code == 201, r.text


async def test_returning_within_the_grace_window_restores_the_full_ttl(
    client, auth, make_show, make_user
):
    show = await make_show(seats=1)
    _, token = await make_user()
    await _hold(client, auth, show, token)
    await client.delete(f"/api/v1/shows/{show['show_id']}/holds", headers=auth(token))

    r = await client.post(
        f"/api/v1/shows/{show['show_id']}/holds/extend", headers=auth(token)
    )
    assert r.status_code == 200, r.text
    assert r.json()["seats"] == 1

    from datetime import datetime

    expires = datetime.fromisoformat(r.json()["holdExpiresAt"].replace("Z", ""))
    seconds = (expires - utcnow()).total_seconds()
    assert seconds > settings.RELEASE_GRACE_SECONDS + 10, seconds


async def test_extending_is_refused_when_nothing_is_held(
    client, auth, make_show, make_user
):
    show = await make_show(seats=1)
    _, token = await make_user()
    r = await client.post(
        f"/api/v1/shows/{show['show_id']}/holds/extend", headers=auth(token)
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "NO_ACTIVE_HOLD"


async def test_extending_cannot_resurrect_a_seat_somebody_else_took(
    client, auth, make_show, make_user
):
    show = await make_show(seats=1)
    _, first = await make_user()
    _, second = await make_user()
    await _hold(client, auth, show, first)
    await client.delete(f"/api/v1/shows/{show['show_id']}/holds", headers=auth(first))

    async with Session() as session:
        await session.execute(
            update(ShowSeat)
            .where(ShowSeat.id == show["seat_ids"][0])
            .values(hold_expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()

    # Second customer takes it during the gap.
    taken = await client.post(
        f"/api/v1/shows/{show['show_id']}/holds",
        json={"seatIds": show["seat_ids"]},
        headers=auth(second),
    )
    assert taken.status_code == 201

    r = await client.post(
        f"/api/v1/shows/{show['show_id']}/holds/extend", headers=auth(first)
    )
    assert r.status_code == 409


async def test_extend_requires_authentication(client, make_show):
    show = await make_show(seats=1)
    r = await client.post(f"/api/v1/shows/{show['show_id']}/holds/extend")
    assert r.status_code == 401
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/test_holds_grace.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'RELEASE_GRACE_SECONDS'`

- [ ] **Step 3: Add the config**

In `apps/api/src/ticket_api/config.py`, replace the `HOLD_TTL_SECONDS` line and add the grace below it:

```python
    # Five minutes. Long enough to fill in details without rushing; short enough
    # that an abandoned checkout does not hold a seat all afternoon.
    HOLD_TTL_SECONDS: int = 300
    # How long seats linger after an explicit "back". Not zero, so bouncing back
    # and forward does not cost a customer their seats to somebody faster.
    RELEASE_GRACE_SECONDS: int = 15
```

In `apps/api/.env.example`, replace `HOLD_TTL_SECONDS=600` with:

```
HOLD_TTL_SECONDS=300
RELEASE_GRACE_SECONDS=15
```

- [ ] **Step 4: Add the response schemas**

In `apps/api/src/ticket_api/modules/seats/schemas.py`, replace `ReleaseResult` and add `ExtendResult`:

```python
class ReleaseResult(BaseModel):
    released: int
    #: When the seats actually become bookable by anyone else.
    freeAt: str  # noqa: N815 - wire format


class ExtendResult(BaseModel):
    holdExpiresAt: str  # noqa: N815 - wire format
    seats: int
```

- [ ] **Step 5: Make release a grace release and add extend**

In `apps/api/src/ticket_api/modules/seats/service.py`, replace `release_holds` entirely and add `extend_hold` after it:

```python
async def release_holds(show_id: str, user_id: str) -> ReleaseResult:
    """
    Explicit "back" or "cancel" from checkout.

    Shortens the hold rather than deleting it. The seat becomes bookable by
    anybody else after RELEASE_GRACE_SECONDS — effective_status enforces that
    exactly, with no sweeper involved — but the owner is kept, so a customer who
    bounces back and forward can reclaim it with extend_hold instead of losing
    their seats to somebody faster.

    A deleted hold would make that impossible, and would make a mis-clicked Back
    button irreversible.
    """
    free_at = utcnow() + timedelta(seconds=settings.RELEASE_GRACE_SECONDS)

    async with Session() as session:
        ids = (
            (
                await session.execute(
                    # Scoped to this user's own holds. Without held_by_user_id in
                    # the filter this endpoint would free anyone's seats.
                    select(ShowSeat.id).where(
                        ShowSeat.show_id == show_id,
                        ShowSeat.held_by_user_id == user_id,
                        ShowSeat.status == SeatStatus.HELD,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not ids:
            return ReleaseResult(released=0, freeAt=iso(free_at) or "")

        await session.execute(
            update(ShowSeat).where(ShowSeat.id.in_(ids)).values(hold_expires_at=free_at)
        )
        await session.commit()

    # Others should see them free once the grace elapses. The callback must
    # RE-READ the seats at fire time and broadcast their real effective status —
    # it must NOT capture a fixed AVAILABLE payload.
    #
    # Corrected during execution: the fixed-payload version was deterministically
    # wrong. Release schedules T+15s; the customer calls extend_hold at T+3s and
    # the row is correctly restored to a 300-second hold; the stale callback then
    # fires anyway and tells every viewer the seat is free. Re-reading is what the
    # sweeper already does, and is why the sweeper never had this bug.
    _schedule_status_rebroadcast(show_id, list(ids))

    return ReleaseResult(released=len(ids), freeAt=iso(free_at) or "")


async def extend_hold(show_id: str, user_id: str) -> ExtendResult:
    """
    Restores a shortened hold to the full TTL.

    Only touches seats this caller still holds and whose clock has not run out,
    so it can never resurrect a seat somebody else has taken in the meantime.
    """
    expires_at = utcnow() + timedelta(seconds=settings.HOLD_TTL_SECONDS)

    async with Session() as session:
        result = await session.execute(
            update(ShowSeat)
            .where(
                ShowSeat.show_id == show_id,
                ShowSeat.held_by_user_id == user_id,
                ShowSeat.status == SeatStatus.HELD,
                ShowSeat.hold_expires_at > utcnow(),
            )
            .values(hold_expires_at=expires_at)
        )
        await session.commit()

    count = result.rowcount or 0
    if count == 0:
        raise ApiError.conflict(
            "NO_ACTIVE_HOLD", "Your hold has already expired. Pick your seats again."
        )

    return ExtendResult(holdExpiresAt=iso(expires_at) or "", seats=count)
```

Add to the imports at the top of the file:

```python
import asyncio
```

and extend the schema import:

```python
from .schemas import ExtendResult, HoldResult, HoldSeatsInput, MyHold, ReleaseResult, SeatView
```

- [ ] **Step 6: Update the routes**

In `apps/api/src/ticket_api/modules/seats/routes.py`, replace the `release` handler and add the extend endpoint after it:

```python
@show_router.delete("/{show_id}/holds", response_model=ReleaseResult)
async def release(show_id: str, user: CurrentUser) -> ReleaseResult:
    return await service.release_holds(show_id, user["sub"])


@show_router.post("/{show_id}/holds/extend", response_model=ExtendResult)
async def extend(show_id: str, user: CurrentUser) -> ExtendResult:
    return await service.extend_hold(show_id, user["sub"])
```

and extend the schema import to include `ExtendResult`.

- [ ] **Step 7: Fix the two existing release assertions**

`tests/test_seats.py::test_release_frees_only_your_own_seats` asserts the seat is
`AVAILABLE` immediately after release. That is now only true after the grace
window. Replace the assertion block at the end of that test with:

```python
    seats = {
        s["id"]: s
        for s in (await client.get(f"/api/v1/shows/{show['show_id']}/seats")).json()["seats"]
    }
    # Both still read HELD — mine on a 15-second clock, theirs on the full TTL.
    assert seats[show["seat_ids"][0]]["status"] == "HELD"
    assert seats[show["seat_ids"][1]]["status"] == "HELD"

    async with Session() as session:
        rows = {
            r.id: r
            for r in (
                await session.execute(
                    select(ShowSeat).where(ShowSeat.id.in_(show["seat_ids"]))
                )
            )
            .scalars()
            .all()
        }
    mine_left = (rows[show["seat_ids"][0]].hold_expires_at - utcnow()).total_seconds()
    theirs_left = (rows[show["seat_ids"][1]].hold_expires_at - utcnow()).total_seconds()
    assert mine_left <= settings.RELEASE_GRACE_SECONDS + 2
    assert theirs_left > settings.RELEASE_GRACE_SECONDS + 10
```

`tests/test_seats.py::test_releasing_nothing_is_not_an_error` still passes — `released` is still 0.

- [ ] **Step 8: Run the tests**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/test_holds_grace.py tests/test_seats.py -q`
Expected: PASS.

- [ ] **Step 9: Lint and run the whole suite**

Run:
```bash
cd apps/api && ./.venv/bin/python -m ruff check --fix src tests && ./.venv/bin/python -m ruff format src tests
NODE_ENV=test ./.venv/bin/python -m pytest -q
```
Expected: 155 passed.

- [ ] **Step 10: Commit**

```bash
git add apps/api
git commit -m "$(cat <<'EOF'
Give holds two clocks: five minutes abandoned, fifteen seconds on back

An abandoned checkout and a deliberate "back" are different events and deserve
different treatment. Abandonment keeps the full TTL because the customer may
return; an explicit back shortens the hold to fifteen seconds because they have
decided — but not to zero, so bouncing back and forward does not cost them their
seats to somebody faster.

The implementation adds no mechanism. Release shortens holdExpiresAt rather than
deleting the hold, so effective_status makes the seat bookable at exactly fifteen
seconds with no sweeper involved, while the owner is kept so extend_hold can
restore the full TTL if the customer comes back. A deleted hold would make that
impossible and would make a mis-clicked Back irreversible.

extend_hold only touches seats the caller still holds on an unexpired clock, so
it can never resurrect a seat somebody else has taken in the meantime — which is
its own test.

HOLD_TTL_SECONDS drops from 600 to 300.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Section pricing with seat counts

**Files:**
- Modify: `apps/api/src/ticket_api/modules/venues/schemas.py`
- Modify: `apps/api/src/ticket_api/modules/venues/service.py`
- Modify: `apps/api/tests/test_venue_capabilities.py`
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/pages/OrganiserPage.tsx`
- Modify: `apps/web/src/pages/manage.css`

**Interfaces:**
- Consumes: nothing new
- Produces: `GET /venues/{id}/sections` returns `{ sections: { name: string; seatCount: number }[] }`

> **Breaking wire change.** `sections` goes from `string[]` to an array of
> objects. The React app reads it in `OrganiserPage.tsx`; both halves change in
> this task or the organiser screen breaks.

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_venue_capabilities.py`:

```python
async def test_sections_are_reported_with_their_seat_counts(client, auth, admin):
    """
    Pricing a section blind is how an organiser discovers at show-creation time
    that "Balcony" was four hundred seats.
    """
    venue = await _make_venue(client, auth, admin)
    await client.post(
        f"{VENUES}/{venue}/seats",
        json={"section": "Front", "rows": 2, "seatsPerRow": 5},
        headers=auth(admin[1]),
    )
    await client.post(
        f"{VENUES}/{venue}/seats",
        json={"section": "Back", "rows": 3, "seatsPerRow": 4},
        headers=auth(admin[1]),
    )

    sections = (await client.get(f"{VENUES}/{venue}/sections")).json()["sections"]
    by_name = {s["name"]: s["seatCount"] for s in sections}
    assert by_name == {"Front": 10, "Back": 12}
    # Alphabetical, so the organiser screen has a stable order.
    assert [s["name"] for s in sections] == ["Back", "Front"]


async def test_a_venue_with_no_seats_reports_no_sections(client, auth, admin):
    venue = await _make_venue(client, auth, admin)
    assert (await client.get(f"{VENUES}/{venue}/sections")).json()["sections"] == []
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/test_venue_capabilities.py -q`
Expected: FAIL — `TypeError: string indices must be integers` (sections are still plain strings).

- [ ] **Step 3: Add the response model**

In `apps/api/src/ticket_api/modules/venues/schemas.py`, add above `SectionsResult` and replace it:

```python
class SectionOut(BaseModel):
    name: str
    seatCount: int  # noqa: N815 - wire format


class SectionsResult(BaseModel):
    sections: list[SectionOut]
```

- [ ] **Step 4: Return counts from the service**

In `apps/api/src/ticket_api/modules/venues/service.py`, replace `list_sections`:

```python
async def list_sections(venue_id: str) -> list[SectionOut]:
    """
    Sections in a venue with their seat counts — what a category may claim, and
    how many seats a price will cover.

    The count matters: pricing a section blind is how an organiser discovers at
    show-creation time that "Balcony" was four hundred seats.
    """
    async with Session() as session:
        rows = (
            await session.execute(
                select(Seat.section, func.count(Seat.id))
                .where(Seat.venue_id == venue_id)
                .group_by(Seat.section)
                .order_by(Seat.section.asc())
            )
        ).all()
    return [SectionOut(name=name, seatCount=int(count)) for name, count in rows]
```

Add `SectionOut` to the `from .schemas import (...)` list.

`distinct` was only used by the old `list_sections`, so it is now an unused
import. Remove it from the `sqlalchemy` import line, or `ruff check` fails with
F401 at Step 11:

```python
from sqlalchemy import func, select
```

- [ ] **Step 5: Update the route's return type**

In `apps/api/src/ticket_api/modules/venues/routes.py`, the handler body is unchanged; only its annotation needs to still typecheck. Confirm it reads:

```python
@router.get("/{venue_id}/sections", response_model=SectionsResult)
async def list_sections(venue_id: str) -> SectionsResult:
    return SectionsResult(sections=await service.list_sections(venue_id))
```

- [ ] **Step 6: Run the API tests**

Run: `cd apps/api && NODE_ENV=test ./.venv/bin/python -m pytest tests/test_venue_capabilities.py -q`
Expected: PASS.

- [ ] **Step 7: Add the frontend type**

There is currently no named type for this response — `OrganiserPage.tsx` types it
inline as `api.get<{ sections: string[] }>`. Add one to
`apps/web/src/lib/types.ts`, next to the other shared types:

```ts
/** A venue section and how many seats it holds. NOT the same as
 *  `Category.sections`, which is the list of section *names* a price band
 *  claims and stays a plain string[]. */
export type VenueSection = { name: string; seatCount: number };
```

**Do not touch `Category.sections`.** It is `string[]` on both sides and is
unaffected by this change — only the `/venues/:id/sections` response shape moves.

- [ ] **Step 8: Show the counts in the organiser UI**

In `apps/web/src/pages/OrganiserPage.tsx` (lines 166-172), change the sections
fetch and the derived `unpriced`:

```tsx
  const sections = useAsync(
    () => api.get<{ sections: VenueSection[] }>(`/api/v1/venues/${event.venue.id}/sections`),
    [event.venue.id],
  );

  const priced = new Set(event.categories.flatMap((c) => c.sections));
  const unpriced = (sections.data?.sections ?? []).filter((s) => !priced.has(s.name));
```

Update the warning line to use names:

```tsx
          <p className="manage__warn">
            Still unpriced: <strong>{unpriced.map((s) => s.name).join(', ')}</strong>. A show
            cannot be created until every section has a price.
          </p>
```

Change `AddCategory`'s prop type:

```tsx
function AddCategory({
  eventId,
  available,
  onAdded,
}: {
  eventId: string;
  available: VenueSection[];
  onAdded: () => void;
}) {
```

and its checkbox fieldset:

```tsx
      <fieldset className="checks">
        <legend className="field__label">Sections this covers</legend>
        {available.map((section) => (
          <label key={section.name} className="check">
            <input
              type="checkbox"
              checked={chosen.includes(section.name)}
              onChange={() => toggle(section.name)}
            />
            {section.name}
            {/* The seat count is the point: pricing a section blind is how an
                organiser finds out at show-creation time that it was 400 seats. */}
            <small>{section.seatCount} seats</small>
          </label>
        ))}
      </fieldset>
```

Add `VenueSection` to the imports from `../lib/types.js`.

- [ ] **Step 9: Add the count styling**

Append to `apps/web/src/pages/manage.css`:

```css
.check small {
  color: var(--text-muted);
  font-size: var(--text-xs);
}
```

- [ ] **Step 10: Typecheck and build the frontend**

Run: `npm run typecheck && npm run build`
Expected: 0 errors; the build succeeds.

- [ ] **Step 11: Lint and run the whole suite**

Run:
```bash
cd apps/api && ./.venv/bin/python -m ruff check --fix src tests && ./.venv/bin/python -m ruff format src tests
NODE_ENV=test ./.venv/bin/python -m pytest -q
```
Expected: 157 passed.

- [ ] **Step 12: Commit**

```bash
git add apps/api apps/web
git commit -m "$(cat <<'EOF'
Show seat counts when pricing a section

An organiser priced sections blind, learning only at show-creation time that
"Balcony" was four hundred seats. GET /venues/:id/sections now returns each
section with its seat count, ordered alphabetically so the screen is stable, and
the pricing checkboxes show it.

This changes the wire shape from string[] to objects, so the organiser page
changes in the same commit — a half-applied rename here would break the only
screen that reads it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Three-page booking flow

**Files:**
- Create: `apps/web/src/pages/CheckoutPage.tsx`
- Create: `apps/web/src/pages/checkout.css`
- Modify: `apps/web/src/pages/ShowPage.tsx`
- Modify: `apps/web/src/main.tsx`

**Interfaces:**
- Consumes: `POST /shows/:id/holds`, `DELETE /shows/:id/holds` (grace release, Task 7), `POST /bookings`
- Produces: route `/shows/:id/checkout`

**Design note for the implementer:** page 1 must not lock. Clicking a seat is browsing; locking on browse means one undecided person freezes a row for everybody else. The lock is acquired by **Continue**, and only then.

- [ ] **Step 1: Create the checkout page**

Create `apps/web/src/pages/CheckoutPage.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import type { SeatView } from '@ticket/shared';
import { api } from '../lib/api.js';
import { messageFor, useAuth } from '../auth/AuthContext.js';
import { useAsync } from '../lib/useAsync.js';
import { formatPrice, formatShowDate, formatShowTime } from '../lib/format.js';
import { Alert, Button, Card, Skeleton } from '../components/ui.js';
import { HoldCountdown } from '../components/HoldCountdown.js';
import './checkout.css';

type ShowDetail = {
  id: string;
  startsAt: string;
  event: { id: string; title: string; venue: { name: string } };
};

/**
 * Page 2 of 3. The seats are already held by the time this renders — Continue
 * on page 1 acquired the lock.
 *
 * Leaving does not delete the hold, it shortens it to a grace window, so a
 * customer who bounces back and forward can reclaim their seats rather than
 * losing them to somebody faster.
 */
export function CheckoutPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const navigate = useNavigate();

  const show = useAsync(() => api.get<{ show: ShowDetail }>(`/api/v1/shows/${id}`), [id]);
  const seats = useAsync(() => api.get<{ seats: SeatView[] }>(`/api/v1/shows/${id}/seats`), [id]);

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const mine = (seats.data?.seats ?? []).filter((s) => s.heldByMe);
  const total = mine.reduce((sum, s) => sum + Number(s.price), 0);
  const expiresAt = mine.find((s) => s.holdExpiresAt)?.holdExpiresAt ?? null;

  // Arriving here with nothing held means the hold lapsed, or the URL was opened
  // directly. Send them back rather than showing an empty checkout.
  useEffect(() => {
    if (!seats.loading && seats.data && mine.length === 0) {
      navigate(`/shows/${id}`, { replace: true });
    }
  }, [seats.loading, seats.data, mine.length, navigate, id]);

  // ponytail: no unload handler. sendBeacon cannot send an Authorization header,
  // so a beacon release would need an unauthenticated endpoint that frees seats —
  // a worse problem than the one it solves. A closed tab is handled by the
  // five-minute TTL, which is exactly why lazy expiry exists: the client is an
  // optimisation, the server's clock is the truth.

  const goBack = useCallback(async () => {
    setBusy(true);
    try {
      await api.del(`/api/v1/shows/${id}/holds`);
    } catch {
      // Even if this fails the hold expires on its own; never block the exit.
    } finally {
      navigate(`/shows/${id}`);
    }
  }, [id, navigate]);

  async function confirm() {
    if (!user) {
      navigate('/login', { state: { from: { pathname: `/shows/${id}/checkout` } } });
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const { booking } = await api.post<{ booking: { id: string } }>('/api/v1/bookings', {
        showId: id,
        seatIds: mine.map((s) => s.id),
      });
      navigate(`/bookings/${booking.id}`);
    } catch (err) {
      setError(messageFor(err));
      seats.reload();
    } finally {
      setBusy(false);
    }
  }

  if (show.loading || seats.loading) return <Skeleton count={1} height={320} />;
  if (show.error) return <Alert>{show.error}</Alert>;
  if (!show.data) return null;

  const detail = show.data.show;

  return (
    <div className="checkout">
      <nav aria-label="Breadcrumb" className="detail__crumbs">
        <Link to={`/events/${detail.event.id}`}>{detail.event.title}</Link>
        <span aria-hidden="true">/</span>
        <Link to={`/shows/${id}`}>Seats</Link>
        <span aria-hidden="true">/</span>
        <span aria-current="page">Checkout</span>
      </nav>

      <ol className="steps" aria-label="Booking progress">
        <li>Choose seats</li>
        <li aria-current="step">Checkout</li>
        <li>Ticket</li>
      </ol>

      <Card className="checkout__card">
        <h1 className="checkout__title">{detail.event.title}</h1>
        <p className="checkout__meta">
          {formatShowDate(detail.startsAt)} at {formatShowTime(detail.startsAt)} ·{' '}
          {detail.event.venue.name}
        </p>

        {expiresAt && (
          <p className="checkout__timer">
            Your seats are held for{' '}
            <HoldCountdown expiresAt={expiresAt} onExpire={() => navigate(`/shows/${id}`)} />
          </p>
        )}

        {error && <Alert>{error}</Alert>}

        <ul className="checkout__seats">
          {mine.map((s) => (
            <li key={s.id}>
              <span>
                {s.row}
                {s.number} · {s.categoryName}
              </span>
              <span>{formatPrice(s.price)}</span>
            </li>
          ))}
        </ul>

        <p className="checkout__total">
          <span>Total</span>
          <strong>{formatPrice(total)}</strong>
        </p>

        <Button variant="cta" full loading={busy} onClick={confirm}>
          {user ? 'Confirm booking' : 'Log in to confirm'}
        </Button>
        <Button variant="quiet" full disabled={busy} onClick={goBack}>
          Back to seats
        </Button>
        <p className="checkout__note">
          Going back keeps your seats for a few more seconds in case you change your mind.
        </p>
      </Card>
    </div>
  );
}
```

- [ ] **Step 2: Add its styles**

Create `apps/web/src/pages/checkout.css`:

```css
.checkout {
  display: grid;
  gap: var(--space-4);
  max-width: 30rem;
  margin: 0 auto;
}

.checkout__card {
  display: grid;
  gap: var(--space-3);
  padding: var(--space-5);
}

.checkout__title {
  font-family: var(--font-brand);
  font-weight: 400;
  font-size: var(--text-xl);
}

.checkout__meta,
.checkout__note,
.checkout__timer {
  font-size: var(--text-sm);
  color: var(--text-muted);
}

.checkout__seats {
  display: grid;
  gap: var(--space-2);
  list-style: none;
  margin: 0;
  padding: var(--space-3) 0;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
}
.checkout__seats li {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
}

.checkout__total {
  display: flex;
  justify-content: space-between;
  font-variant-numeric: tabular-nums;
}

/* Progress. Numbers come from a counter so the markup stays a plain list and the
   current step is announced by aria-current rather than by colour. */
.steps {
  display: flex;
  gap: var(--space-4);
  list-style: none;
  margin: 0;
  padding: 0;
  counter-reset: step;
  font-size: var(--text-xs);
  color: var(--text-muted);
}
.steps li {
  counter-increment: step;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.steps li::before {
  content: counter(step);
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 1px solid var(--border-strong);
  font-variant-numeric: tabular-nums;
}
.steps li[aria-current='step'] {
  color: var(--text);
  font-weight: 600;
}
.steps li[aria-current='step']::before {
  background: var(--brand);
  border-color: var(--brand);
  color: var(--brand-ink);
}
```

- [ ] **Step 3: Make Continue navigate instead of holding in place**

In `apps/web/src/pages/ShowPage.tsx`, replace the whole body of `placeHold` (it starts at line 80) with:

```tsx
  async function placeHold() {
    if (!user) {
      navigate('/login', { state: { from: { pathname: `/shows/${id}` } } });
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/shows/${id}/holds`, { seatIds: [...selected] });
      // Page 2. The lock is acquired here and nowhere earlier — clicking a seat
      // is browsing, and locking on browse freezes a row for everybody else.
      navigate(`/shows/${id}/checkout`);
    } catch (err) {
      setError(messageFor(err));
      reloadSeats();
    } finally {
      setBusy(false);
    }
  }
```

Then change the CTA label on line 252 from:

```tsx
                {user ? 'Hold these seats' : 'Log in to hold seats'}
```

to:

```tsx
                {user ? 'Continue' : 'Log in to continue'}
```

- [ ] **Step 4: Register the route**

In `apps/web/src/main.tsx`, add the import beside the other page imports:

```tsx
import { CheckoutPage } from './pages/CheckoutPage.js';
```

and the route immediately after the `/shows/:id` route:

```tsx
            <Route
              path="/shows/:id/checkout"
              element={
                <RequireAuth>
                  <CheckoutPage />
                </RequireAuth>
              }
            />
```

- [ ] **Step 5: Typecheck and build**

Run: `npm run typecheck && npm run build`
Expected: 0 type errors; the build succeeds.

- [ ] **Step 6: Walk the flow by hand**

Run: `npm run test:db:up` (if not running), then in one terminal `cd apps/api && ./.venv/bin/uvicorn ticket_api.main:asgi --reload --port 4000`, and in another `npm run dev:web`.

Then, in the browser at http://localhost:5173:
1. Open a show, select two seats. **Confirm in the network tab that no request fires** — selection must not lock.
2. Press Continue → exactly one `POST /holds` → you land on `/shows/:id/checkout` with a countdown.
3. Press Back to seats → one `DELETE /holds` → the seats stay yours for ~15s, then free.
4. Select again, Continue, Confirm booking → the ticket page with a QR.

- [ ] **Step 7: Commit**

```bash
git add apps/web
git commit -m "$(cat <<'EOF'
Split booking into three pages, locking only on Continue

Choosing seats, paying, and holding a ticket are three different activities and
now three different pages. The lock moves to Continue: clicking a seat is
browsing, and locking on browse means one undecided person freezes a row for
everybody else.

Leaving checkout does not abandon the seats outright — it shortens the hold to
the grace window, and the copy says so, because a customer who presses Back by
mistake should not lose their seats to somebody faster.

No unload handler, deliberately. sendBeacon cannot send an Authorization header,
so a beacon release would need an unauthenticated endpoint that frees seats — a
worse problem than the one it solves. A closed tab is handled by the five-minute
TTL, which is precisely why lazy expiry exists: the client is an optimisation,
the server's clock is the truth.

Arriving at checkout with nothing held — a lapsed hold, or the URL opened
directly — redirects back to the seat map rather than rendering an empty basket.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Documentation

**Files:**
- Modify: `docs/DECISIONS.md`
- Modify: `docs/API.md`
- Modify: `README.md`
- Modify: `docs/TODO.md`
- Modify: `docs/CONTEXT.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Record the ADRs**

Append to `docs/DECISIONS.md` (the last existing ADR is 030):

```markdown
---

## ADR-031 — Stage layout is stored venue geometry, not a render-time projection

**Accepted** · 2026-08-24

`Venue.stageLayout` decides how the venue builder generates coordinates. A
centre-stage venue's seats are written with radial `posX`/`posY` at build time.

_Alternative, and an earlier draft of this design:_ layout as a per-event
projection, computing radial positions at render time so one hall could be
staged both ways.

_Why not:_ it solved a problem nobody has. A hall built in the round **is** in
the round. Storing the geometry means the seat map renderer needs no special
case at all — it already draws whatever coordinates it is given — and the two
layouts differ only in the stage marker.

_Consequence:_ a venue cannot be re-staged after its seats exist. Build a second
venue instead. That is the honest model: re-staging a real room means moving real
chairs.

---

## ADR-032 — Venue double-booking is prevented by a partial exclusion constraint

**Accepted** · 2026-08-24

Two layers. `assert_venue_free()` inside the show-creation transaction locks the
venue's scheduled shows and produces a message naming the clash. Underneath, a
Postgres GiST exclusion constraint on `("venueId", tsrange("startsAt",
"occupiesUntil"))`, partial on `status = 'SCHEDULED'`.

_Why the occupied window is not the show:_ the room has to empty, be cleaned and
be reset. `occupiesUntil = startsAt + duration + venue.turnaroundMinutes`, with
turnaround on the venue because a stadium needs longer than a screening room.

_Why `venueId` is denormalised onto `Show`:_ an exclusion constraint spans one
table. Safe because `Event.venueId` is already immutable — moving an event would
orphan every `ShowSeat` generated against the old venue's seats. The same trade
`priceAtBooking` makes.

_Why partial on status:_ cancelling a show frees its slot automatically, with no
cleanup code. House style, shared with `BookingSeat_showSeatId_live_key` — guard
the live rows, let the dead ones stay for history.

_`tsrange`, not `tstzrange`:_ the columns are `TIMESTAMP(3)` **without** time
zone, and the range type has to match the column type or the constraint will not
build.

_Cost:_ SQLAlchemy cannot express it, so it is hand-written and invisible to the
models. Recorded in `docs/DEBUGGING.md` as drift a future autogenerate will try
to drop.

---

## ADR-033 — Holds expire on two clocks

**Accepted** · 2026-08-24

Abandonment gives the full `HOLD_TTL_SECONDS` (300). An explicit back or cancel
shortens the hold to `RELEASE_GRACE_SECONDS` (15) rather than deleting it.

_Why not delete:_ keeping the owner lets `extend_hold()` restore the full TTL if
the customer returns, so a mis-clicked Back is recoverable rather than a lost
seat. Deleting makes that impossible.

_Why not zero:_ bouncing back and forward should not cost somebody their seats to
a faster customer.

_Why this needed no new mechanism:_ `effective_status()` already treats a lapsed
lease as free, so the seat becomes bookable at exactly fifteen seconds without
the sweeper being involved at all. One number changed.
```

- [ ] **Step 2: Update the API reference**

In `docs/API.md`:

- `POST /venues` — body gains `stageLayout?`, `allowedEventTypes?`, `turnaroundMinutes?`; add `400 CENTRE_STAGE_CANNOT_SHOW_MOVIES`.
- `POST /venues/:id/seats` — body gains `arcStartDegrees?`, `arcSpanDegrees?` (centre-stage only).
- `GET /venues/:id/sections` — returns `{ name, seatCount }[]`, not `string[]`.
- `POST /events` — add `400 EVENT_TYPE_NOT_ALLOWED`.
- `POST /events/:id/shows` — body gains required `durationMinutes`; add `409 VENUE_DOUBLE_BOOKED`.
- `DELETE /shows/:id/holds` — now **shortens** holds to `RELEASE_GRACE_SECONDS`; returns `{ released, freeAt }`.
- Add `POST /shows/:id/holds/extend` — restores the full TTL; `409 NO_ACTIVE_HOLD`.

- [ ] **Step 3: Update the README**

In `README.md`, in the "Seat hold and TTL" section, after the paragraph beginning "**The sweeper is visibility.**", insert:

```markdown
### Two clocks

Abandoning checkout and pressing Back are different events:

| Situation | Seats free after |
| --- | --- |
| Tab closed, walked away | **5 minutes** |
| Explicit back or cancel | **15 seconds** |

Back does not delete the hold — it _shortens_ it. The owner is kept, so a
customer who bounces back and forward can reclaim their seats instead of losing
them to somebody faster. No new mechanism: `effective_status()` makes the seat
bookable at exactly fifteen seconds, with the sweeper uninvolved.
```

In the "What it does" table, change the Admin row to:

```markdown
| **Admin**     | Create venues, build their seat layouts, and set their capabilities — stage layout, which event types they permit, and how long the room needs between shows |
```

and the Organiser row to:

```markdown
| **Organiser** | Book a venue for a slot (no double-booking), price each section, schedule shows, and read revenue by category and by show |
```

- [ ] **Step 4: Tick the milestone**

In `docs/TODO.md`, replace the Milestone 1 status banner and tick all seven boxes:

```markdown
### Milestone 1 — venue capabilities, scheduling, booking flow ✅ DONE (2026-08-24)

- [x] `Venue.stageLayout` (END_STAGE / CENTRE_STAGE) + radial seat generation
- [x] `Venue.allowedEventTypes` + `turnaroundMinutes`; centre-stage cannot allow MOVIE
- [x] `Show.durationMinutes` / `endsAt` / `occupiesUntil`, organiser supplies duration
- [x] **No double-booking a venue** — app-level check plus a Postgres GiST
      exclusion constraint, partial on `status` so a cancelled show frees its slot
- [x] Section-wise pricing UI showing each section's seat count
- [x] **Three-page flow** — select (no lock) → Continue (locks) → checkout → ticket
- [x] **Two clocks** — 5 min abandonment, 15 s explicit back. Back _shortens_
      the hold rather than deleting it
```

- [ ] **Step 5: Update the running status**

In `docs/CONTEXT.md`, update the **Current state** table so **Phase** reads
`Milestone 1 complete — venue capabilities, scheduling, three-page flow` and
**Next action** reads `Milestone 2: show cancellation`. Add a session entry
describing what was built and any surprises.

In `CLAUDE.md`, update the **Current phase** block to name Milestone 2 as next,
and add the two new schema facts to the data model section: `Venue.stageLayout` /
`allowedEventTypes` / `turnaroundMinutes`, and `Show.venueId` / `durationMinutes` /
`endsAt` / `occupiesUntil` / `status`.

- [ ] **Step 6: Format and commit**

```bash
npx prettier --write README.md docs/*.md CLAUDE.md
git add README.md docs CLAUDE.md
git commit -m "$(cat <<'EOF'
Document venue capabilities, scheduling and the two-clock TTL

Three ADRs: stage layout as stored geometry rather than a render-time projection,
including why that reverses an earlier draft; venue double-booking prevented by a
partial GiST exclusion constraint, with the reasoning for denormalising venueId,
for making the constraint partial, and for tsrange over tstzrange; and the
two-clock hold expiry, including why an explicit back shortens rather than
deletes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review

**Spec coverage**

| Spec section | Task |
| --- | --- |
| 1. Venue capabilities — schema, layout as stored geometry, the MOVIE validation, event-type gate | 1, 2, 3, 4 |
| 2. Venue scheduling — occupied window, denormalised `venueId`, both layers, cancelled shows free the slot | 5, 6 |
| 3. Section-wise pricing with seat counts | 8 |
| 4. Three-page flow, two clocks, the honest sendBeacon limitation | 7, 9 |
| Migrations (venue_capabilities, show_scheduling, then the constraint after backfill) | 2, 5, 6 |
| Tests — including the parallel double-booking case, the shape of the seat race | every task; 6 has the parallel one |
| Non-goals — no per-event projection, no per-event turnaround, no recurring shows, no availability calendar UI, no change to the hold locking discipline | respected |
| Step zero — test database split | **already done** (ADR-030), which is why this plan starts at Milestone 1 |

**Placeholders:** none. Every code step carries real code; every test step carries real assertions. Step 11 of Task 5 and Step 2 of Task 10 are mechanical sweeps ("add `durationMinutes` to each failing body", "update these seven rows") rather than literal code — both name the exact edits and their verification commands.

**Names checked against the codebase**, not written from memory: `_trim`,
`SeatCount`, `SeatOut`, `VenueBase`, `VenueDetail`, `VenueSummary`,
`SectionsResult`, `SeatBlockResult` and the `UniqueViolation` / `IntegrityError`
imports in `venues/`; `settings`, `iso`, `utcnow`, `timedelta`, `broadcast_status`
and the existing `ReleaseResult` import in `seats/`; `ROW_LABELS` at module level
in `venues/service.py` (Task 3 deletes it); `placeHold` at ShowPage.tsx:80 and the
CTA at :252; the sections fetch at OrganiserPage.tsx:166-172.

Three corrections came out of that pass:

1. `apps/web/src/lib/types.ts` has **no** type for the sections response — it is
   inline in `OrganiserPage.tsx`. Task 8 now *adds* `VenueSection` rather than
   changing something that does not exist.
2. `Category.sections` is a **different** field that also happens to be called
   `sections`, and stays `string[]`. Task 8 says so explicitly, because renaming
   it would break category display for no reason.
3. `distinct` is imported in `venues/service.py` solely for the old
   `list_sections`. Task 8 rewrites that function, so the import must go or
   `ruff check` fails F401 — now called out in the step that causes it.

**Verified before planning, so the plan does not rest on an assumption:**
`btree_gist` is available on both the local test container (1.7) and Supabase
(1.7, not yet installed — Task 6's migration installs it). A `tsrange` exclusion
constraint was built on the test container and confirmed to raise
`ExclusionViolation` on an overlapping insert.

**Type consistency:**
- `SeatPosition` fields are `row`, `number`, `pos_x`, `pos_y` in Task 1 and consumed with those names in Task 3.
- `generate_end_stage_block` / `generate_centre_stage_block` are keyword-only in Task 1 and called keyword-only in Task 3.
- `occupied_window` returns the tuple `(ends_at, occupies_until)` in Task 5 and is unpacked that way in Task 5's `create_show`, the conftest fixture and the seed.
- `assert_venue_free` is keyword-only (`venue_id`, `starts_at`, `occupies_until`) in Task 6 and called that way in the same task.
- `SectionOut` is `{name, seatCount}` in Task 8 and consumed as `VenueSection` with the same two fields in the frontend half of Task 8.
- `ReleaseResult` gains `freeAt` in Task 7 and is asserted in Task 7's tests; the frontend in Task 9 ignores it, which is fine.
- `ExtendResult` is `{holdExpiresAt, seats}` in Task 7, asserted in Task 7.

**Known cross-task hazards, flagged for the executor:**
1. **Task 5 breaks every test mid-task.** `Show` gains four NOT NULL columns, and both `conftest.make_show` and `seed.py` construct `Show(...)` directly. Step 7 fixes them; do not stop between Steps 5 and 7.
2. **Task 8 is a breaking wire change.** `sections` goes `string[]` → objects, and the organiser page reads it. Both halves are in the same task and the same commit.
3. **Task 7 changes existing behaviour**, so `test_release_frees_only_your_own_seats` must be updated — Step 7 gives the replacement assertions rather than leaving the executor to guess.
