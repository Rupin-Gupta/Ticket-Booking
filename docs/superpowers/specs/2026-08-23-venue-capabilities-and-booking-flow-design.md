# Venue capabilities, scheduling, and the booking flow — design

**Date:** 2026-08-23
**Status:** awaiting review
**Milestone:** 1 (after milestone 0, the test database split)

Reshapes the domain around a clearer ownership model, and replaces the
single-page hold with a three-page flow.

> **Ownership, decided by the owner:** an **admin** owns venues — creating them,
> choosing their stage layout, and deciding which event types they permit. An
> **organiser** is a _tenant_: they book a venue for a time slot, price its
> sections, and edit or cancel their own events. They cannot create or modify a
> venue.

---

## 1. Venue capabilities

```prisma
enum StageLayout {
  END_STAGE      // audience faces one way, like a cinema
  CENTRE_STAGE   // in the round, audience surrounds the stage
}

model Venue {
  stageLayout        StageLayout @default(END_STAGE)
  allowedEventTypes  EventType[] @default([MOVIE, CONCERT])
  turnaroundMinutes  Int         @default(15)
}
```

### Layout is stored geometry, not a rendering mode

An earlier draft made layout a per-event _projection_, computing radial
positions at render time so one hall could be staged both ways. That was solving
a problem nobody has. A hall built in the round **is** in the round.

So the venue builder generates the coordinates when the seats are created:

- `END_STAGE` — the current grid. Rows stack, `posX` centred on zero, stage below
- `CENTRE_STAGE` — each section takes an angular wedge, rows become radii, seats
  spread along their arc, stage at the origin

Both write plain `posX` / `posY`. **The seat map renderer needs no special
case** — it already draws whatever coordinates it is given. Only the stage
marker moves, from an edge caption to a centre disc.

### One validation worth having

**A `CENTRE_STAGE` venue may not allow `MOVIE`.** Nobody projects a film in the
round. Refusing it at venue creation is far better than discovering it when a
cinema's seat map renders as a circle.

### Event type gate

`POST /events` verifies the chosen venue permits that type, returning
`400 EVENT_TYPE_NOT_ALLOWED` naming what the venue does allow.

---

## 2. Venue scheduling — no double booking

### The bug this fixes

`Show` currently stores only `startsAt`. Two organisers can create overlapping
shows in the same hall and both succeed. Harmless while an organiser implicitly
"owned" a venue; a real defect once organisers are tenants sharing one.

### The occupied window is not the show

A venue is unavailable for longer than the performance — the room has to empty,
be cleaned, and be reset:

```
occupiesUntil = startsAt + durationMinutes + venue.turnaroundMinutes
```

Turnaround lives on the **venue** because a stadium needs longer than a
screening room. It defaults to the 15 minutes specified.

```prisma
model Show {
  venueId         String    // denormalised — see below
  startsAt        DateTime
  durationMinutes Int       // organiser must supply it
  endsAt          DateTime  // startsAt + durationMinutes
  occupiesUntil   DateTime  // endsAt + venue.turnaroundMinutes
  status          ShowStatus @default(SCHEDULED)
}
```

### Why `venueId` is denormalised onto `Show`

A Postgres exclusion constraint operates on a single table, and the venue
currently reaches `Show` only via `Event`. Copying it is safe because
**`Event.venueId` is already immutable** — `updateEventSchema` omits it
deliberately, since moving an event would orphan every `ShowSeat` generated
against the old venue's seats.

Denormalising an immutable derived value to buy a database-level guarantee is
the same trade `priceAtBooking` already makes.

### Two layers, as everywhere else in this codebase

**Application layer** — inside the show-creation transaction, lock the venue's
scheduled shows and check for overlap, so the organiser gets a useful error
naming the show that clashes.

**Database layer** — the guarantee that survives an application bug:

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE "Show" ADD CONSTRAINT show_no_venue_overlap
  EXCLUDE USING gist (
    "venueId"                              WITH =,
    tstzrange("startsAt", "occupiesUntil")  WITH &&
  ) WHERE (status = 'SCHEDULED');
```

`btree_gist` is required for equality on a text column inside a GiST exclusion
constraint. Supabase provides it.

**The `WHERE` clause is the elegant part:** a cancelled show stops blocking its
slot automatically, with no cleanup code. Exactly the pattern already used by
`BookingSeat_showSeatId_live_key`, and worth naming as a house style — _guard
the live rows, let the dead ones stay for history._

Prisma cannot express exclusion constraints, so this is hand-written in the
migration and recorded in `docs/DEBUGGING.md` alongside the other partial index
that a future `migrate dev` might try to drop.

### Organiser flow

Pick a venue → see its booked slots → choose a start time and duration → the
occupied window is shown before confirming, including the turnaround.

---

## 3. Section-wise pricing

Already modelled by `SeatCategory.sections[]` with a price (ADR-016). What is
missing is the _authoring_: today an admin builds seats and an organiser prices
them in a separate screen, with no view of how many seats a price covers.

Change is UI and validation only, no schema:

- When pricing an event, each venue section is listed **with its seat count**:
  _"Section 1 — 20 seats — ₹\_\_\_"_
- Every section must be priced before a show can be scheduled, which the server
  already enforces via `SECTION_NOT_PRICED`; the UI now says so up front rather
  than failing at the end

---

## 4. The three-page booking flow

| Page            | Route                 | Locking                         |
| --------------- | --------------------- | ------------------------------- |
| 1. Choose seats | `/shows/:id`          | **None.** Selection is local    |
| 2. Checkout     | `/shows/:id/checkout` | **Lock acquired on "Continue"** |
| 3. Ticket       | `/bookings/:id`       | Booked                          |

**Locking on Continue, not on click, is deliberate.** Clicking a seat is
browsing. Locking on browse means one undecided person freezes a row for
everyone else.

### Two clocks

| Situation                           | Seats free after | Why                                                                                                   |
| ----------------------------------- | ---------------- | ----------------------------------------------------------------------------------------------------- |
| Abandoned — tab closed, walked away | **5 minutes**    | They may come back                                                                                    |
| Explicit back or cancel             | **15 seconds**   | They have decided — but a grace window means bouncing back and forward does not cost them their seats |

Implementation: going back does **not** delete the hold. It _shortens_ it —
`holdExpiresAt = now + RELEASE_GRACE_SECONDS`.

That reuses everything already built. `effectiveStatus()` makes the seat
bookable by others at exactly fifteen seconds; the sweeper makes it _visible_
shortly after. One number changed, no new mechanism.

Returning within the grace window restores the full five minutes, which composes
with the contention-aware hold extension planned separately.

**Config:** `HOLD_TTL_SECONDS` 600 → **300**. New `RELEASE_GRACE_SECONDS = 15`.

### Honest limitation

A closed tab or a hard browser-back cannot be reliably intercepted.
`beforeunload` plus `navigator.sendBeacon` is best-effort; when it fails, the
five-minute TTL is the backstop.

**This is exactly why lazy expiry exists.** The client is an optimisation; the
server's clock is the truth.

---

## Migrations

1. `venue_capabilities` — `StageLayout` enum, three columns on `Venue`. Existing
   venues default to `END_STAGE` and both event types
2. `show_scheduling` — `venueId`, `durationMinutes`, `endsAt`, `occupiesUntil`,
   `ShowStatus` on `Show`; backfill from existing rows using a 120-minute default
   duration; then `btree_gist` and the exclusion constraint

The constraint is added **after** the backfill, so existing data is checked
rather than assumed. If the seeded shows already overlap, the migration fails
loudly — which is the correct outcome.

---

## Tests

**Venue capabilities**

- A centre-stage venue refuses `MOVIE` in `allowedEventTypes`
- Creating an event of a type the venue disallows returns 400
- Centre-stage seat generation produces radial coordinates; end-stage a grid

**Scheduling**

- Overlapping shows in one venue: second refused
- Overlap **inside the turnaround window** refused — a show starting 5 minutes
  after another ends, with a 15-minute turnaround, must fail
- Adjacent shows outside the window both succeed
- **Two organisers creating overlapping shows simultaneously: exactly one wins**
  — the same parallel-request shape as the seat concurrency test
- Cancelling a show frees its slot for another organiser
- The exclusion constraint refuses an overlap written directly to the database,
  bypassing the application entirely

**Booking flow**

- Selecting seats issues no lock; the database is untouched until Continue
- Continue locks exactly the selected seats
- An explicit back sets expiry ~15s out; the seat is immediately bookable by
  another customer after it, without waiting for the sweeper
- An abandoned hold survives ~5 minutes
- Returning within the grace window restores the full TTL
- **The twenty-way concurrency test stays green** — the regression guard

---

## Non-goals

- No layout as a per-event projection; layout is venue geometry
- No per-event turnaround override; it is a venue property
- No recurring or repeating shows
- No venue availability calendar UI beyond a list of booked slots
- No change to the hold transaction's locking discipline. **The `FOR UPDATE`,
  the status re-read and the write stay together.**
