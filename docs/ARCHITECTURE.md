# Architecture

How the system is put together and why. `CLAUDE.md` holds the condensed rules
and the authoritative Prisma schema; this file holds the reasoning and the
mechanisms. When the two disagree, `CLAUDE.md` wins and this file gets fixed.

---

## 1. Shape of the system

```
                  ┌──────────────────────────────┐
   browser ──────▶│  apps/web  React + Vite      │
                  │  seat grid, checkout, admin  │
                  └───────┬───────────────▲──────┘
                REST/JWT  │               │  Socket.IO
                          ▼               │  seat:sync / seat:update
                  ┌──────────────────────────────┐
                  │  apps/api  Express + TS      │
                  │  modules/ jobs/ realtime/    │
                  └───┬──────────┬──────────┬────┘
                      │          │          │
          Prisma tx   │          │ BullMQ   │ nodemailer
                      ▼          ▼          ▼
              ┌────────────┐ ┌────────┐ ┌──────────┐
              │ PostgreSQL │ │ Redis  │ │ Resend   │
              │  (Neon)    │ │(Upstash)│ │  email   │
              │ SOURCE OF  │ │ queues │ └──────────┘
              │   TRUTH    │ │ + s.io │
              └────────────┘ │ adapter│
                             └────────┘
```

**Postgres is the only source of truth for seat state.** Redis holds job queues
and the Socket.IO pub/sub adapter — never a seat lock. A Redis lock plus a
Postgres row is two sources of truth that drift the first time a network blip
happens; a `SELECT ... FOR UPDATE` inside a transaction is one source that
cannot.

### API module layout

`apps/api/src/modules/{auth,venues,events,shows,holds,bookings,waitlist,organiser}`

Each module is `routes.ts` + `service.ts` + `schema.ts` (Zod). Routes parse and
authorise; services own the transactions; nothing else touches Prisma directly.
Cross-module logic that both bookings and the sweeper need — `advanceWaitlist()`
— lives in the waitlist service and is imported, never duplicated.

---

## 2. Seat data model

A physical `Seat` belongs to a `Venue` once — row, number, section, and `posX`/
`posY` for rendering the grid. It carries no status; a chair does not know
whether it is sold.

At show-creation time `instantiateShowSeats(showId)` writes one `ShowSeat` row
per venue seat, in a single `createMany`. That row is where live state lives:
`status`, `categoryId` (which sets the price), `heldByUserId`, `holdExpiresAt`,
`offerExpiresAt`.

Why the split: a 500-seat venue running 40 shows means 500 `Seat` rows and
20 000 `ShowSeat` rows. Status per show is the only thing that varies, so only
that is duplicated. `@@unique([showId, seatId])` makes double-instantiation
impossible; `@@index([showId, status])` makes the seat map and the sweeper fast.

### Seat status lifecycle

```
                  ┌──────────── hold expires (TTL) ───────────┐
                  │              or DELETE /holds/:id         │
                  ▼                                           │
           ┌─────────────┐   POST /shows/:id/holds    ┌────────────┐
           │  AVAILABLE  │ ─────────────────────────▶ │    HELD    │
           └─────────────┘                            └─────┬──────┘
                  ▲                                         │ POST /bookings
                  │ no one waiting                          ▼
                  │                                   ┌────────────┐
           ┌──────┴───────┐   offer expires           │   BOOKED   │
           │   OFFERED    │ ◀──────────────┐          └─────┬──────┘
           │ (one named   │                │                │ cancel
           │  customer)   │ ───────────────┘                ▼
           └──────┬───────┘  advanceWaitlist()      advanceWaitlist()
                  │ accept offer                     ├─ queue non-empty → OFFERED
                  └────────────▶ BOOKED              └─ queue empty     → AVAILABLE
```

`OFFERED` exists as a distinct status rather than reusing `HELD` because the two
have different owners and different expiry clocks: a `HELD` seat belongs to
whoever grabbed it and dies in ~10 minutes; an `OFFERED` seat is reserved
*against a specific waitlist entry* and, on expiry, must walk the queue instead
of going straight back to `AVAILABLE`. Collapsing them means one sweeper branch
has to guess which kind of expiry it is looking at.

---

## 3. Seat hold + TTL

### Placing a hold

```
POST /api/v1/shows/:showId/holds   { seatIds: string[] }
```

Inside one `prisma.$transaction`:

1. `SELECT id FROM "ShowSeat" WHERE id = ANY($1) ORDER BY id FOR UPDATE`
   — row locks, taken in a deterministic order.
2. Re-read those rows and reject unless every one is genuinely free:
   `status = 'AVAILABLE'`, **or** an expired lease
   (`status IN ('HELD','OFFERED') AND expiry < now()`).
3. `UPDATE` them to `HELD`, `heldByUserId = me`,
   `holdExpiresAt = now() + HOLD_TTL_SECONDS`.
4. Commit, then emit `seat:update` per seat into room `show:{showId}`.

`ORDER BY id` matters. Two customers each grabbing seats {A,B} in opposite
orders deadlock without it; Postgres resolves that by killing one transaction,
which turns a clean 409 into a 500. Sorting the lock set makes it impossible.

All-or-nothing: partial holds are worse UX than a clean rejection ("seat B just
went") and they leak seats when the customer walks away from a half-filled cart.

### Expiry: lazy check + sweeper

Two mechanisms, different jobs:

- **Lazy expiry is the correctness guarantee.** Every transaction that reads a
  seat treats `expiry < now()` as free. Even if every background job is dead,
  no seat is ever permanently locked by an abandoned checkout.
- **The sweeper is the UX guarantee.** A BullMQ repeatable job every ~10s flips
  expired `HELD` rows back to `AVAILABLE` and expired `OFFERED` rows through
  `advanceWaitlist()`, then broadcasts. Without it a seat *is* free but still
  renders grey on everyone else's screen until someone happens to touch it.

`ponytail:` one repeatable job, two queries, no per-seat timers. Per-seat
`setTimeout` does not survive a restart and does not work across two Render
instances. If 10s granularity ever proves too coarse, drop the interval before
reaching for anything cleverer.

**Guarantee to hold onto:** correctness comes from the transaction, not the
scheduler. The scheduler only makes the truth visible sooner.

---

## 4. Concurrency protection

The bug this project is graded on avoiding:

```ts
// WRONG — time-of-check-to-time-of-use race
const seat = await prisma.showSeat.findUnique({ where: { id } });
if (seat.status === 'AVAILABLE') {              // ← another request interleaves here
  await prisma.showSeat.update({ where: { id }, data: { status: 'HELD' } });
}
```

Both requests read `AVAILABLE`, both write `HELD`, second write silently wins.
Two customers, one seat, no error anywhere.

The fix is a row-level write lock held for the whole check-then-write:

```ts
await prisma.$transaction(async (tx) => {
  const locked = await tx.$queryRaw<{ id: string }[]>`
    SELECT id FROM "ShowSeat"
    WHERE id = ANY(${seatIds}) AND "showId" = ${showId}
    ORDER BY id
    FOR UPDATE`;                                    // ← serialises contenders here

  if (locked.length !== seatIds.length) throw new ApiError(404, 'SEAT_NOT_FOUND');

  const free = await tx.showSeat.findMany({
    where: {
      id: { in: seatIds },
      OR: [
        { status: 'AVAILABLE' },
        { status: 'HELD',    holdExpiresAt:  { lt: new Date() } },
        { status: 'OFFERED', offerExpiresAt: { lt: new Date() } },
      ],
    },
  });
  if (free.length !== seatIds.length) throw new ApiError(409, 'SEAT_UNAVAILABLE');

  await tx.showSeat.updateMany({ where: { id: { in: seatIds } }, data: { ... } });
});
```

The second contender blocks at `FOR UPDATE` until the first commits, then reads
`HELD` and gets a clean `409`. One winner, deterministically.

Tagged-template `$queryRaw` parameterises `${seatIds}` — it is not string
interpolation. `$queryRawUnsafe` and `Prisma.raw()` are, and are banned anywhere
near request data.

### Defence in depth

| Layer | Protects against |
| --- | --- |
| `FOR UPDATE` in the hold/book transaction | two customers racing for one seat |
| `@@unique([showId, seatId])` on `ShowSeat` | duplicate seat instantiation |
| `@unique` on `BookingSeat.showSeatId` | one seat sold into two bookings, ever |
| `FOR UPDATE SKIP LOCKED` on waitlist pick | two sweepers offering one seat to two people |
| Rate limit + per-customer hold cap | one script legitimately holding the venue |

`BookingSeat.showSeatId @unique` is the seatbelt: even if every application check
were wrong, Postgres refuses to record the same show-seat in two bookings.

### The test that must stay green

`apps/api/tests/concurrency/` — fire ~20 parallel `POST /holds` at one seat,
assert exactly one `201` and nineteen `409`, and assert the DB holds exactly one
`HELD` row. Any change to holds or bookings re-runs it.

---

## 5. Waitlist and time-limited offers

### Joining

Only when the category is genuinely sold out. `POST /shows/:id/waitlist`
`{ categoryId }` → one `WaitlistEntry` (`WAITING`, `joinedAt = now()`).
Unique-ish per `(showId, categoryId, customerId)` while active so refreshing the
page does not buy someone three places in line.

### `advanceWaitlist(showSeatId)` — one function, two callers

Called by booking cancellation **and** by offer expiry. Never forked into two
near-identical copies; that is how the two paths drift.

Inside one transaction:

1. `SELECT ... FOR UPDATE` the freed `ShowSeat`.
2. Pick the next entry:
   ```sql
   SELECT * FROM "WaitlistEntry"
   WHERE "showId" = $1 AND "categoryId" = $2 AND status = 'WAITING'
   ORDER BY "joinedAt" ASC
   LIMIT 1
   FOR UPDATE SKIP LOCKED
   ```
3. **No entry** → seat goes `AVAILABLE`, broadcast, done.
4. **Entry found** → seat goes `OFFERED` with
   `offerExpiresAt = now() + OFFER_TTL_SECONDS`; entry goes `OFFERED` with a
   matching expiry and `offerToken = crypto.randomBytes(32).toString('hex')`.
5. After commit, enqueue the offer email carrying
   `{WEB_URL}/waitlist/offers/{offerToken}`.

`SKIP LOCKED` is what keeps two concurrent cancellations from both handing the
seat to the same person: the second picker skips the locked row and takes the
next one down the queue.

`ORDER BY joinedAt ASC` is the FIFO promise. Test it: cancel with three people
waiting and assert the offer went to the earliest `joinedAt` and to nobody else.

### Accepting

`POST /api/v1/waitlist/offers/:token/accept` — transaction locks the entry and
the seat, and rejects unless *all* of: token matches, entry is `OFFERED`,
`offerExpiresAt > now()`, seat is still `OFFERED` for that entry, and the caller
is the customer the offer belongs to. Then it books the seat and marks the entry
`CONVERTED`.

The token is a bearer credential for a real seat, so: 32 random bytes, single
use, time-limited, and checked against the logged-in user. Never `Math.random()`,
never a counter, never accepted after expiry even by a second.

### Expiring

Same ~10s sweeper. Entries where `status = 'OFFERED' AND offerExpiresAt < now()`
become `EXPIRED`, and the seat is fed straight back into `advanceWaitlist()` —
which either offers it to the next person or releases it. The queue walks itself
down until someone accepts or the line runs out.

---

## 6. Booking, QR, email

`POST /api/v1/bookings { showId, seatIds }` inside one transaction: lock the
seats, require every one to be `HELD` **by this user** and unexpired, write
`Booking` + `BookingSeat` rows (price snapshotted into `priceAtBooking`), flip
seats to `BOOKED`.

`priceAtBooking` is stored on the row because an organiser editing category
pricing next week must not retroactively rewrite last week's revenue.

`reference` is human-facing (`BK-7F3K2`, on the ticket). `qrToken` is 32 random
bytes and is what the QR actually encodes, as a `{WEB_URL}/verify/{qrToken}`
URL — a QR holding raw booking JSON is forgeable by anyone with a QR generator,
and a QR holding the sequential reference is guessable.

Email is **queued, never inline**. The booking commits and responds; a BullMQ
worker renders the QR (`qrcode` → data URL) and sends via Nodemailer/Resend with
retry and backoff. An SMTP hiccup must never fail a booking the customer already
paid for and the database already recorded.

Cancellation (`POST /bookings/:id/cancel`) is owner-checked, marks the booking
`CANCELLED`, and calls `advanceWaitlist()` for each freed seat.

---

## 7. Realtime

Socket.IO, one room per show: `show:{showId}`.

- Client emits `show:join` / `show:leave` on mount/unmount.
- Server emits `seat:sync` — full seat snapshot on join, so a late joiner is
  never rendering a stale map.
- Server emits `seat:update` — one seat, after *every* committed mutation:
  hold, release, book, cancel, offer, offer-expiry, sweeper release.

Broadcast **after commit**, never inside the transaction. Emitting from inside
means a rolled-back transaction has already told every browser the seat is gone.

Payloads are explicit `select`s. `heldByUserId` never leaves the server — the
public map shows *that* a seat is held, never *who* holds it.

`@socket.io/redis-adapter` is wired from the start. Without it, two Render
instances each broadcast to their own connected clients and half the viewers
silently miss updates — a bug that never reproduces locally on one process.

The seat grid also falls back to a polled `GET /shows/:id/seats` if the socket
drops, so a flaky connection degrades instead of freezing.

---

## 8. Auth and roles

JWT bearer, `HS256` pinned explicitly on both `sign()` and `verify()` —
never inferred from the token header. Access tokens are short-lived (15 min)
because a JWT cannot be revoked before it expires.

Passwords: Argon2id (`argon2`). If bcrypt is ever substituted, password length
must be explicitly capped at 72 bytes rather than relying on silent truncation.

`POST /auth/register` hard-codes `role: CUSTOMER` server-side. No request shape
anywhere accepts a client-supplied role — accepting one is a one-line
privilege-escalation hole. Organiser and admin accounts come from the seed
script or an admin-only promote endpoint.

Authorisation is two layers: `requireRole(['ORGANISER'])` for the coarse gate,
then a resource-ownership check inside the service (this organiser owns *this*
event). Role alone lets any organiser read any other organiser's revenue.

---

## 9. Environment split

Neon needs two connection strings and mixing them up is the classic
"works locally, breaks on deploy" failure:

- `DATABASE_URL` — pooled (`-pooler` host), used by the running app.
- `DIRECT_URL` — unpooled, used by `prisma migrate` only. Migrations take
  advisory locks that a connection pooler mangles.

Everything else configurable rather than hard-coded: `HOLD_TTL_SECONDS`,
`OFFER_TTL_SECONDS`, `SWEEPER_INTERVAL_MS`, `MAX_SEATS_PER_HOLD`,
`MAX_ACTIVE_HOLDS_PER_USER`. The brief calls the hold TTL "configurable", and
tests need to set it to 2 seconds without waiting ten minutes.

---

## 10. Known limits

Deliberate, with the upgrade path named:

| Simplification | Ceiling | Upgrade when |
| --- | --- | --- |
| No payment gateway | Booking confirms without money changing hands | Out of scope in the brief |
| Sweeper interval 10s | Released seats appear free up to 10s late to *other* viewers | Drop the interval; correctness is already exact via lazy expiry |
| One repeatable sweeper job | All expiries scan two indexed queries | Partition by show if a single scan gets slow |
| Seat map rendered from `posX`/`posY` | No curved or tiered seating geometry | Store a layout JSON per venue if a real floor plan is needed |
| Render free tier cold starts | First request after idle is slow | Paid tier, or a keep-warm ping |
