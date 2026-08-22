# Seat signals and accessible seating — design

**Date:** 2026-08-22
**Status:** approved, ready for implementation planning

Three features on top of the completed brief:

- **A — Hesitation Index.** Seat quality inferred from abandoned holds. Genuinely
  unshipped elsewhere, because no platform keeps hold telemetry.
- **B — Accessible seating.** Wheelchair spaces and companion seats that book
  atomically. Not unique, but demonstrably underserved.
- **C — Seat map hierarchy.** Row labels, named section bands, and a visual tier
  so a premium seat reads as premium. Fixes a real gap: the map is currently
  flat, and a ₹450 seat is indistinguishable from a ₹250 one until you hover.

Background and prior-art research: [docs/FEATURE_BACKLOG.md](../../FEATURE_BACKLOG.md).

---

## Step zero — split the test database

**Both features add tests, and tests currently write into the database serving
the live site.** Fix that first.

- Second free Supabase project, migrated with the same `prisma migrate deploy`
- `DATABASE_URL_TEST` / `DIRECT_URL_TEST` in `apps/api/.env`
- `lib/prisma.ts` selects by `NODE_ENV === 'test'`
- The test script fails loudly if `DATABASE_URL_TEST` is unset, rather than
  silently falling back to production. A test suite that quietly writes to
  production is worse than one that refuses to run.

---

## Feature A — Hesitation Index

### The signal

Every hold ends one of three ways, and they do not mean the same thing:

| Outcome    | Recorded in           | Interpretation                               |
| ---------- | --------------------- | -------------------------------------------- |
| `EXPIRED`  | `sweepExpiredHolds()` | Walked away. Weak — could be a closed laptop |
| `RELEASED` | `releaseHolds()`      | **Deliberately un-picked. Strong**           |
| `BOOKED`   | `createBooking()`     | Converted                                    |

A seat repeatedly picked and then un-picked is one people consider and reject.
That is different information from a cancellation, which is regret _after_
paying.

### Capture — and the regression it must not cause

**Counters on `Seat` are rejected.** A physical seat is shared by every show at
its venue, so incrementing a counter there inside the hold transaction would
take a lock spanning shows: two customers holding A12 on _different nights_
would serialise against each other. That directly degrades the concurrency
guarantee this project is graded on.

**Append-only events instead:**

```prisma
model SeatEvent {
  id     String        @id @default(uuid())
  seat   Seat          @relation(fields: [seatId], references: [id])
  seatId String
  showId String
  kind   SeatEventKind // HELD | RELEASED | EXPIRED | BOOKED
  at     DateTime      @default(now())

  @@index([seatId, kind])
  @@index([showId, at])
}
```

Written **after the transaction commits**, alongside the existing broadcast
calls — never inside the lock. Pure inserts contend with nothing, and the hold
path stays at the two round trips it was tuned to.

Writes are fire-and-forget with a caught error. A lost event costs one data
point in a statistical measure; a failed booking costs a customer their seat.
Those are not the same, and the ordering of importance is explicit.

### Aggregation

```
hesitation = (released + expired) / (released + expired + booked)
```

Computed per **physical seat**, across every show at that venue — that is what
produces enough sample to say anything.

Three guards against telling people things the data does not support:

1. **Minimum sample.** Fewer than 5 outcomes → no signal at all. One
   abandonment is not "100% rejected".
2. **Relative, never absolute, and the row is named.** Compared against the mean
   for that seat's **row**, surfaced only above ~1.5×, and the copy states which
   row: _"passed over 3× more often than other seats in row F"_. A bare
   multiplier invites the reader to invent their own baseline. A popular show
   produces more abandonments everywhere; a rate normalises for that, and a row
   comparison normalises for the section being unpopular generally.
3. **Never state a cause.** "Passed over more often than others in row F" is
   what the data supports. "Obstructed view" is a guess dressed as a fact.

Computed on read initially, with the two indexes above. If it becomes slow, a
materialised rollup is the upgrade — named, not built.

### Visibility — organiser first, publishable per event

**Decision: the organiser sees it by default; publishing to customers is a
per-event toggle, default off.**

```prisma
model Event {
  publishSeatSignals Boolean @default(false)
}
```

- **Organiser dashboard** — always visible. A "hardest to sell" table combining
  hesitation with the cancellation rate.
- **Customer seat map** — only when `publishSeatSignals` is on for that event.

The reasoning: the honesty is the point of the feature, but an organiser has a
legitimate interest in how their inventory is described, and forcing publication
would make the feature unshippable. A toggle makes disclosure a decision someone
takes deliberately.

### API

| Endpoint                            | Change                                                                                                                                                                |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /shows/:id/seats`              | `SeatView` gains `hesitation: { ratio, rowMultiple, sample } \| null` — null unless published **and** past the sample threshold; also `tier: number` and `accessType` |
| `GET /organiser/events/:id/summary` | Gains `seatSignals[]`: the worst seats by hesitation and cancellation                                                                                                 |
| `PATCH /events/:id`                 | Accepts `publishSeatSignals`                                                                                                                                          |

### Tests

- An expired hold, an explicit release and a booking each write exactly one
  event of the right kind
- Events are written **after** commit — a rolled-back hold writes none
- Under the sample threshold, the API returns `null` rather than a misleading number
- With `publishSeatSignals` false, the field is absent from the customer map
  even when the organiser can see it
- **The hold path still passes the twenty-way concurrency test** — the
  regression guard for the whole design

---

## Feature B — Accessible seating

### Schema

```prisma
enum SeatAccessType {
  STANDARD
  WHEELCHAIR_SPACE
  COMPANION
  STEP_FREE          // reachable without stairs; not paired
}

model Seat {
  accessType    SeatAccessType @default(STANDARD)
  companionOfId String?        // COMPANION → its WHEELCHAIR_SPACE
}
```

### The invariant

**A wheelchair space and its companion seat are held, booked, and released as
one unit. Neither is separately obtainable.**

That is the part missing from real platforms. They sell accessible seats; they
do not guarantee that the person who needs assistance cannot be seated apart
from the person providing it.

Enforced by an **expansion step** at the top of `holdSeats()` and
`createBooking()`: before locking, expand the requested seat set to include
every paired partner. The lock set is still sorted by id afterwards, so the
deadlock guarantee is untouched.

Expansion happens before the lock, not inside it — it is a read of static venue
geometry, not contended state.

### Known limitation, accepted deliberately

**Paired seats bypass the waitlist.** When a booking containing a wheelchair
space is cancelled, both seats return to `AVAILABLE` together rather than being
offered to the queue.

The offer machinery is one seat per waitlist entry, so routing a pair through it
could produce a half-offered pair — which breaks the invariant the feature
exists to guarantee. Returning them to general sale is correct but not ideal;
pair-aware offers are a later change, recorded rather than hidden.

### Elsewhere

- **Venue builder** — mark a block's access type; creating a wheelchair block
  generates its companion seats and links them
- **Seat map** — distinct **shape and icon**, not colour alone, plus an
  "accessible seats only" filter. Selecting either half visibly selects both
- **`SeatView`** — gains `accessType` and `pairedWith`

### Tests

- Holding a wheelchair space also holds its companion
- The companion **cannot** be held alone
- Booking a pair produces one booking containing both
- Cancelling frees both, and both return to `AVAILABLE`
- Another customer racing for the companion alone is refused
- Expansion does not break the sorted lock set — concurrency test stays green

---

## Feature C — Seat map hierarchy

### The gap

Today every seat renders identically. Status is encoded (available, held,
booked), but **category is not** — the only way to learn a seat's price or
section is to hover it. There are no row labels, so "row F seat 12" cannot be
found by looking. A real box-office map has neither problem.

### Row labels

A gutter down **both** sides of the grid carrying the row letter, taken from
`Seat.row`, which is already stored and already returned in `SeatView`.

Both sides, not one: on a wide grid a single-sided label means tracing across
twenty seats to find your row. Rendered `aria-hidden` — every seat button
already carries `"row F seat 12"` in its accessible name, so repeating the
letter would just add noise for a screen reader.

### Section bands

Seats group into labelled bands by section, each with a header showing the
section name, its category, and the price:

```
────────────  PREMIUM · ₹450  ────────────
   [seats]
────────────  STANDARD · ₹250  ───────────
   [seats]
```

Sections already exist in the data (`Seat.section`), and a category already
declares which sections it covers via `SeatCategory.sections[]` (ADR-016). This
is surfacing a relationship the schema already models.

### Tiering — by price rank, never by name

**Decision: a category's tier is derived from its price rank within the event,
not from matching the word "Premium".**

The seeded event uses Premium/Standard; the Spiderman event created through the
UI uses Premium/**Normal**; an organiser could equally use Gold, Front or
Stalls. Matching on a name would silently fail on real data — exactly the class
of bug that only shows up in production.

```
tier 0 = highest priced category  → premium treatment
tier 1 = next                     → standard treatment
tier 2+                           → base treatment
```

An event with one category gets no tiering at all, rather than one section
declared "premium" against nothing.

### What "premium treatment" means

The constraint: **status must stay the most legible thing on the map.** It is
functional — it decides whether a seat can be clicked. Tier is contextual.
Tier therefore uses channels status does not:

| Channel                              | Carries                               |
| ------------------------------------ | ------------------------------------- |
| Fill and border colour               | **Status** — unchanged, still primary |
| Hatching                             | Held by someone else — unchanged      |
| Seat size, corner radius             | **Tier**                              |
| Band background wash and rule weight | **Tier**                              |
| Section header treatment             | **Tier**                              |

Premium gets slightly larger seats, a warmer accent hairline, a faint band wash,
and a heavier header rule. Standard stays as it is now. The premium band reads
as the better part of the room at a glance without any seat's availability
becoming harder to read.

Colour is never the only tier signal — size and position carry it too, so the
hierarchy survives greyscale and colour-blindness, consistent with the seat map
rules already established in ADR-015.

### Where else the tier shows

- **The basket** groups selected seats by section rather than listing them flat
- **The waitlist panel** already lists per category; it gains the same tier accent
- **`SeatView`** gains `tier: number` so the client never re-derives ranking from
  prices, which would drift from the server's ordering

### Implementation note

The visual pass runs through the **`ui-ux-pro-max`** skill at implementation
time, applying the Phase 1 design system rather than generating a new one — a
second design-system pass would produce a different palette and split the
product in two.

### Tests

- Tier is derived from price order, not category name: an event with
  "Gold ₹900 / Normal ₹300" tiers Gold as 0
- Equal prices produce equal tiers rather than an arbitrary winner
- A single-category event returns tier 0 for everything and renders no tiering
- Row labels match `Seat.row` for every rendered row
- Seat buttons keep their existing accessible names and status semantics

---

## Migrations

1. `seat_events` — `SeatEvent` table, `SeatEventKind` enum, two indexes
2. `event_publish_seat_signals` — one boolean on `Event`
3. `seat_accessibility` — `SeatAccessType` enum, two columns on `Seat`

All additive. No backfill: the hesitation signal starts empty and accumulates,
which is honest — the seed script can generate history for a demo.

---

## Non-goals

- No causal claims about _why_ a seat is unpopular
- No pair-aware waitlist offers in this pass
- No companion-seat release policy near event time
- No materialised rollups until measurement says they are needed
- No tiering by category **name** — price rank only
- No new design system; the Phase 1 tokens are applied, not replaced
- No change to the hold or booking transaction's locking discipline. **The
  `FOR UPDATE`, the status re-read and the write stay together.**

---

## Order

1. Test database split
2. `SeatEvent` capture + the concurrency regression test
3. Aggregation and the organiser view
4. Publish toggle and the customer view
5. Accessibility schema and atomic pairing
6. Seat map hierarchy — row labels, section bands, price-rank tiering
7. Venue builder and the remaining seat map UI
