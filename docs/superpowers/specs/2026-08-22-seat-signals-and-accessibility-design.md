# Seat signals and accessible seating — design

**Date:** 2026-08-22
**Status:** approved, ready for implementation planning

Two features on top of the completed brief:

- **A — Hesitation Index.** Seat quality inferred from abandoned holds. Genuinely
  unshipped elsewhere, because no platform keeps hold telemetry.
- **B — Accessible seating.** Wheelchair spaces and companion seats that book
  atomically. Not unique, but demonstrably underserved.

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
2. **Relative, never absolute.** Compared against the mean for that seat's
   **row**, and surfaced only above ~1.5×. A popular show produces more
   abandonments everywhere; a rate normalises for that, and a row comparison
   normalises for the section being unpopular generally.
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

| Endpoint                            | Change                                                                                                                          |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `GET /shows/:id/seats`              | `SeatView` gains `hesitation: { ratio, rowMultiple, sample } \| null` — null unless published **and** past the sample threshold |
| `GET /organiser/events/:id/summary` | Gains `seatSignals[]`: the worst seats by hesitation and cancellation                                                           |
| `PATCH /events/:id`                 | Accepts `publishSeatSignals`                                                                                                    |

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
- No change to the hold or booking transaction's locking discipline. **The
  `FOR UPDATE`, the status re-read and the write stay together.**

---

## Order

1. Test database split
2. `SeatEvent` capture + the concurrency regression test
3. Aggregation and the organiser view
4. Publish toggle and the customer view
5. Accessibility schema and atomic pairing
6. Venue builder and seat map UI
