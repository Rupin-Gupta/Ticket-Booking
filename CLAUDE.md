# Ticket Booking System — Project Memory

This file is project context for Claude Code. Keep it at the repo root. Update
the "Current phase" line at the bottom as you progress — every new session
should be able to read this file and know exactly where the project stands.

Full rationale for every decision below lives in the companion architecture
doc ("Ticket Booking Blueprint") — this file is the condensed, load-bearing
version meant to travel with the code.

## What this is

A ticket booking platform for movies and concerts: customers book seats from
a visual map, held seats auto-release on checkout abandonment, sold-out
events run a waitlist with automatic seat reassignment on cancellation, and
every confirmed booking sends an email with a QR code ticket.

## Stack (decided — do not change without asking)

**The API was ported from TypeScript to Python on 2026-08-23**, at the owner's
request. The frontend stays TypeScript: it is React in a browser, which is a
platform constraint, not a preference. Everything below reflects the current
state; the retired TypeScript API is in git history up to commit `6c7dfd4`.

- **Backend:** Python 3.12+ + FastAPI + SQLAlchemy 2.0 (async), deployed on Render
- **Database driver:** **psycopg3**, never asyncpg — see rule 16
- **Frontend:** React + TypeScript + Vite, deployed on Vercel
- **Database:** PostgreSQL via SQLAlchemy + Alembic, hosted on Supabase (free tier)
- **Queue / cache:** Redis via Upstash + ARQ — email jobs
- **Realtime:** `python-socketio` 5.x (protocol rev 5, compatible with the
  frontend's `socket.io-client` 4.x), rooms keyed `show:{showId}`
- **Auth:** JWT (PyJWT, HS256 pinned) + Argon2id (`argon2-cffi`), roles
  `CUSTOMER` / `ORGANISER` / `ADMIN`
- **Validation:** Pydantic v2 (replaced Zod)
- **QR:** `qrcode` PyPI package, encodes a `/verify/{token}` URL, not raw JSON
- **Email:** Resend Python SDK
- **Tests:** pytest + pytest-asyncio + httpx
- **Repo layout:** `apps/api` (Python), `apps/web` (TypeScript), with the web
  app's API types generated from FastAPI's OpenAPI schema

The database schema was deliberately **not** renamed during the port. Tables stay
quoted PascalCase, columns camelCase, enums native Postgres types. Renaming would
have been a second variable in a port whose whole value is provable equivalence,
and the hand-written partial unique index is already written against these names.

Render's free Postgres expires after 30 days — never use it for this project.
Supabase is the pick (ADR-013); its free tier pauses after 7 days of no
database activity, which the daily `/health` keep-alive is there to prevent.

## Non-negotiable architectural rules

1. **Postgres is the single source of truth for seat state.** Never introduce
   a Redis-based lock for seat holds. Every state transition (hold, release,
   book, offer) happens inside a Prisma `$transaction` that opens with
   `SELECT ... FOR UPDATE` on the `ShowSeat` row(s) involved, checks status,
   then writes. No check-then-write without a lock — that's a
   time-of-check-to-time-of-use race and it is exactly the bug this project
   is graded on avoiding.
2. **Booking cancellation and waitlist offer-expiry both call the same
   `advanceWaitlist(showSeatId)` function.** Do not write two versions of
   "find the next person in line and offer them the seat."
3. **Waitlist order is FIFO**, scoped to `(showId, categoryId)`, ordered by
   `joinedAt`. Use `FOR UPDATE SKIP LOCKED` when selecting the next entry.
4. **Two sweeper checks, one interval job:** every ~10s, release `ShowSeat`
   rows where `status = HELD AND holdExpiresAt < now()`, and expire rows
   where `status = OFFERED AND offerExpiresAt < now()` (which then calls
   `advanceWaitlist()`). Lazy expiry — treating an expired hold/offer as free
   the instant a transaction reads it — is the correctness backstop; the
   sweeper is what makes it feel real-time for other viewers.
5. **Email sending is queued (BullMQ), never inline in the request handler.**
   A booking confirms and returns immediately; the email job can retry
   independently without ever blocking or failing a confirmed booking.
6. **A physical `Seat` belongs to a `Venue` once. A `ShowSeat` row is
   generated per seat, per show, at show-creation time** (`instantiateShowSeats()`).
   That row — not the physical seat — carries live status, price, and
   hold/offer state.
7. **`POST /auth/register` hard-codes `role: CUSTOMER` server-side.** No
   request shape accepts a client-supplied `role` — this is a mass-assignment
   privilege-escalation hole otherwise. Organiser/admin accounts come from
   the seed script or an admin-only promote endpoint, never self-registration.
8. **Any `ShowSeat` data returned to a client is an explicit `select`.**
   `heldByUserId` never leaves the server — the public seat map must not
   reveal who is holding a seat.
9. **Cap seats-per-hold-request and concurrent active holds per customer,
   and rate-limit `POST /shows/:id/holds` and `POST /auth/login`.** The
   concurrency lock stops two people racing for one seat; it does nothing
   to stop one customer (or a script) legitimately holding every seat in a
   category on purpose.
10. **`offerToken` and `qrToken` are `crypto.randomBytes(32).toString('hex')`
    — never `Math.random()`, never counter-derived.** Both are bearer
    credentials for a real seat.
11. **JWT: pin `algorithm: 'HS256'` explicitly on both `sign()` and
    `verify()`.** Never let the library infer the algorithm from the token
    header. Keep access tokens short-lived (15 min) since a JWT can't be
    revoked before it expires.
12. **Hash passwords with Argon2id** (`argon2` package) — OWASP's current
    Password Storage Cheat Sheet lists it first choice, bcrypt as legacy.
    If bcrypt is used instead, cap password length at 72 bytes explicitly
    rather than relying on silent truncation.
13. **Never let `$queryRawUnsafe` or `Prisma.raw()` touch a value that came
    from `req`.** Tagged-template `$queryRaw` (used everywhere in this
    project) auto-parameterizes and is injection-safe; those two escape
    hatches are not.
14. **Supabase needs two connection strings, and there is a third that must
    never be used.** `DATABASE_URL` = transaction pooler, `:6543`, and it
    must carry `?pgbouncer=true`. `DIRECT_URL` = session pooler, `:5432`,
    for `prisma migrate` only. The direct `db.<ref>.supabase.co` string is
    IPv6-only and unreachable from Render — never point either variable at
    it. Also: the free project **pauses after 7 days of no queries** and
    needs a manual restore, so the daily `/health` ping is load-bearing, not
    a nicety.
15. **Wire the Socket.IO Redis manager before assuming realtime works beyond
    one instance.** Without it, broadcasts don't cross process boundaries —
    silently drops updates the moment Render runs more than one instance.
    In Python this is `socketio.AsyncRedisManager`.
16. **The database driver is `psycopg3` with `prepare_threshold=None`. Never
    asyncpg.** Supabase's transaction pooler is pgbouncer, which cannot carry
    a prepared statement across pooled connections — it is prepared on one
    backend and executed on another that has never heard of it. asyncpg leaks
    prepared statements through that pooler *even with its own cache disabled*
    (supabase/supabase#39227, still open at time of writing), and starts
    failing above roughly 100 concurrent requests — which is exactly the shape
    of the concurrency test this project is graded on. psycopg3 was spiked
    before any application code was written: 20, 100 and 250 concurrent
    contenders for a single seat row, each run one winner, zero errors.
    The connection string also needs `?pgbouncer=true` stripped — that was a
    Prisma-only flag, and psycopg forwards unknown parameters to the server,
    which rejects them.
17. **Timestamps crossing the wire go through `iso()`, money through
    `money()`.** `datetime.isoformat()` omits the trailing `Z`, and the browser
    reads a zone-less timestamp as *local* time — every hold countdown would be
    wrong by the viewer's UTC offset, silently, for non-UTC users only. And the
    price column is `Numeric(65, 30)`, so a plain `str()` ships thirty zeros;
    `money()` renders `450`, never `450.000…` and never a float.

## Data model

Full Prisma schema — treat this as authoritative; extend it, don't restructure
it without discussion:

```prisma
model User {
  id           String   @id @default(uuid())
  email        String   @unique
  passwordHash String
  role         Role     @default(CUSTOMER)
  name         String
  createdAt    DateTime @default(now())

  eventsOrganised Event[]         @relation("Organiser")
  bookings        Booking[]
  waitlistEntries WaitlistEntry[]
}

enum Role {
  CUSTOMER
  ORGANISER
  ADMIN
}

model Venue {
  id      String  @id @default(uuid())
  name    String
  address String
  seats   Seat[]
  events  Event[]
}

model Seat {
  id      String @id @default(uuid())
  venue   Venue  @relation(fields: [venueId], references: [id])
  venueId String
  section String   // e.g. "Balcony", "Floor"
  row     String   // e.g. "A"
  number  Int      // e.g. 12
  posX    Float    // for the visual seat grid
  posY    Float

  showSeats ShowSeat[]

  @@unique([venueId, section, row, number])
}

model Event {
  id          String    @id @default(uuid())
  organiser   User      @relation("Organiser", fields: [organiserId], references: [id])
  organiserId String
  venue       Venue     @relation(fields: [venueId], references: [id])
  venueId     String
  title       String
  type        EventType
  description String?

  categories SeatCategory[]
  shows      Show[]
}

enum EventType {
  MOVIE
  CONCERT
}

model SeatCategory {
  id      String  @id @default(uuid())
  event   Event   @relation(fields: [eventId], references: [id])
  eventId String
  name    String  // "Premium", "Standard"
  price   Decimal
  sections String[] // venue sections this band covers — see ADR-016

  showSeats       ShowSeat[]
  waitlistEntries WaitlistEntry[]

  @@unique([eventId, name])
}

model Show {
  id       String   @id @default(uuid())
  event    Event    @relation(fields: [eventId], references: [id])
  eventId  String
  startsAt DateTime

  showSeats       ShowSeat[]
  waitlistEntries WaitlistEntry[]
  bookings        Booking[] // required opposite side of Booking.show — see ADR-012
}

model ShowSeat {
  id             String       @id @default(uuid())
  show           Show         @relation(fields: [showId], references: [id])
  showId         String
  seat           Seat         @relation(fields: [seatId], references: [id])
  seatId         String
  category       SeatCategory @relation(fields: [categoryId], references: [id])
  categoryId     String
  status         SeatStatus   @default(AVAILABLE)
  heldByUserId   String?
  holdExpiresAt  DateTime?
  offerExpiresAt DateTime?

  bookingSeats BookingSeat[] // one per booking across time; at most one live

  @@unique([showId, seatId])
  @@index([showId, status])
}

enum SeatStatus {
  AVAILABLE
  HELD
  OFFERED // held open for one specific waitlisted customer
  BOOKED
}

model Booking {
  id          String        @id @default(uuid())
  reference   String        @unique // human-facing, e.g. "BK-7F3K2"
  customer    User          @relation(fields: [customerId], references: [id])
  customerId  String
  show        Show          @relation(fields: [showId], references: [id])
  showId      String
  status      BookingStatus @default(CONFIRMED)
  qrToken     String        @unique // opaque token encoded in the QR
  createdAt   DateTime      @default(now())
  cancelledAt DateTime?

  seats BookingSeat[]

  @@index([customerId, createdAt]) // booking history: by customer, newest first
}

enum BookingStatus {
  CONFIRMED
  CANCELLED
}

model BookingSeat {
  id             String    @id @default(uuid())
  booking        Booking   @relation(fields: [bookingId], references: [id])
  bookingId      String
  showSeat       ShowSeat  @relation(fields: [showSeatId], references: [id])
  showSeatId     String    // NOT @unique — see ADR-020
  priceAtBooking Decimal
  releasedAt     DateTime? // set on cancellation; the row survives for history

  @@unique([bookingId, showSeatId])
  @@index([showSeatId])
}

// The seatbelt is a PARTIAL unique index Prisma cannot express, created by hand
// in migration 20260822120000_booking_seat_release:
//   CREATE UNIQUE INDEX "BookingSeat_showSeatId_live_key"
//     ON "BookingSeat"("showSeatId") WHERE "releasedAt" IS NULL;
// A plain @unique made a cancelled seat unsellable forever.

model WaitlistEntry {
  id             String         @id @default(uuid())
  show           Show           @relation(fields: [showId], references: [id])
  showId         String
  category       SeatCategory   @relation(fields: [categoryId], references: [id])
  categoryId     String
  customer       User           @relation(fields: [customerId], references: [id])
  customerId     String
  status         WaitlistStatus @default(WAITING)
  joinedAt       DateTime       @default(now())
  offeredSeatId  String?
  offerToken     String?        @unique
  offerExpiresAt DateTime?

  @@index([showId, categoryId, status, joinedAt])
}

enum WaitlistStatus {
  WAITING
  OFFERED
  EXPIRED
  CONVERTED
  CANCELLED
}
```

## API conventions

- REST, resource-oriented, versioned under `/api/v1`.
- Auth: JWT bearer token, role claim checked by a `requireRole([...])`
  middleware layered on top of resource-ownership checks.
- Key routes: `POST /shows/:id/holds`, `DELETE /holds/:id`,
  `POST /bookings`, `POST /bookings/:id/cancel`,
  `POST /shows/:id/waitlist`, `POST /waitlist/offers/:token/accept`,
  `GET /organiser/events/:id/summary`.
- Socket.IO: clients emit `show:join` / `show:leave`; server emits
  `seat:sync` (full snapshot on join) and `seat:update` (single seat, on
  every mutation).

## Repo layout

```
ticket-booking-system/
├── apps/
│   ├── api/            # Node + Express + TypeScript
│   │   ├── src/
│   │   │   ├── modules/        # auth, venues, events, shows, holds, bookings, waitlist, organiser
│   │   │   ├── jobs/            # sweepers + email worker (BullMQ)
│   │   │   ├── realtime/        # Socket.IO setup + room helpers
│   │   │   ├── lib/             # prisma client, qrcode, mailer, jwt
│   │   │   └── middleware/      # auth, roles, error handler
│   │   ├── prisma/schema.prisma
│   │   ├── tests/concurrency/   # the parallel-hold race test — keep green
│   │   └── .env.example
│   └── web/             # React + TypeScript + Vite
├── packages/shared/      # TS types/enums used by both apps
├── README.md
└── SYSTEM_DESIGN.md       # 800-word write-up, deliverable #4
```

## Testing expectations

- `apps/api/tests/concurrency/` must contain a test that fires ~20 parallel
  hold requests at the same seat and asserts exactly one succeeds (HTTP 201)
  and the rest are rejected (HTTP 409). Every change touching holds or
  bookings must keep this test green.
- A companion test for the waitlist: cancel a booking with several people
  waitlisted for that category and assert the offer goes to the earliest
  `joinedAt` entry, and only to that one.

## Build phases

Work through these in order — don't jump ahead. Full detail (task-by-task
checklists, per-phase Claude Code tips) is in the architecture doc; this is
the index.

0. **Foundations** — monorepo, TS/lint config, Supabase + Prisma init, Express +
   Vite skeletons, `.env.example`, first commit.
1. **Auth & roles** — User model, register/login, JWT + role middleware.
2. **Venues, events & shows** — admin/organiser CRUD, `instantiateShowSeats()`.
3. **Seat map, holds & concurrency** _(evaluation-critical)_ — seat map
   endpoint, locked hold transaction, hold sweeper, the concurrency test,
   basic seat grid UI.
4. **Booking, QR & email** — booking confirmation, QR generation, queued
   email worker, booking history/cancel.
5. **Waitlist & time-limited offers** _(evaluation-critical)_ —
   `advanceWaitlist()`, offer sweeper, accept-offer endpoint, the waitlist test.
6. **Real-time seat map** — Socket.IO rooms, broadcast on every mutation,
   retire polling.
7. **Organiser dashboard & polish** — revenue summary, seat map visuals,
   countdown timer, loading/error states.
8. **Deploy, document, verify** — Vercel + Render + Supabase, smoke-test live,
   README, `SYSTEM_DESIGN.md`, re-run the concurrency test against prod.
9. **Hardening & standing out** _(optional, do it if time allows)_ — seed
   script, Dockerfile + docker-compose, graceful shutdown, structured
   logging, CI running both test suites, a load-test script, OpenAPI docs,
   error tracking. Not required by the brief; it's what separates a
   submission that passes from one that stands out.

## Current phase

`Python port, in progress (started 2026-08-23). The deployed site is
intentionally offline for the duration.`

**Ported and verified against the live database:**

| Piece | State |
| --- | --- |
| Config, engine, ORM (all 10 tables) | done — enums, Decimal and arrays round-trip |
| Security: Argon2id, HS256, random tokens | done — 20 checks |
| Auth module + app factory + error shapes | done |
| Seat map, holds, sweeper | done — **20-way race green over real TCP** |

**Not yet ported:** venues, events/shows, bookings + QR, waitlist + offers,
organiser dashboard, the ARQ email queue, the Socket.IO server, Alembic, the
79-test suite, and the `apps/api-py` → `apps/api` swap.

The port is a strict 1:1 rewrite: the existing tests are the specification, and
no behaviour changes until they all pass. Milestone 0/1 feature work
(`docs/superpowers/plans/2026-08-23-venue-capabilities-and-booking-flow.md`) is
**superseded** — it was written against the TypeScript codebase and must be
re-planned once the port lands.
