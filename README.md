# Ticket Booking System

A ticket booking platform for movies and concerts. Customers pick seats from a
live map, held seats auto-release when checkout is abandoned, sold-out shows run
a FIFO waitlist that offers freed seats to the next person automatically, and
every confirmed booking emails a QR code ticket.

**Live app:** _added after deployment_ · **API:** _added after deployment_

```
79 tests passing · TypeScript strict across three workspaces
```

---

## Contents

- [What it does](#what-it-does)
- [Stack](#stack)
- [Quick start](#quick-start)
- [Environment variables](#environment-variables)
- [Seat hold and TTL](#seat-hold-and-ttl)
- [Concurrency protection](#concurrency-protection)
- [Waitlist and time-limited offers](#waitlist-and-time-limited-offers)
- [Database schema](#database-schema)
- [API reference](#api-reference)
- [Tests](#tests)
- [Deployment](#deployment)
- [Documentation map](#documentation-map)

---

## What it does

| Role          | Can                                                                                                                                                                        |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Customer**  | Browse and filter events, view a live seat map, hold seats on a timer, book, receive a QR ticket by email, view history, cancel, join a waitlist and claim an offered seat |
| **Organiser** | Create events at a venue, price each section, schedule shows (which generate the seat map), and read revenue by category and by show                                       |
| **Admin**     | Create venues and build their seat layouts                                                                                                                                 |

Roles are assigned server-side only. `POST /auth/register` hard-codes
`CUSTOMER`, and the request schema has no `role` field at all — organiser and
admin accounts come from the seed script.

---

## Stack

| Layer    | Choice                                       | Why                                                                          |
| -------- | -------------------------------------------- | ---------------------------------------------------------------------------- |
| API      | Node + TypeScript + Express 5                | Express 5 forwards rejected promises to the error handler on its own         |
| Frontend | React 19 + Vite, plain CSS custom properties | Tokens give a seat that is `--seat-held` in both themes with no build config |
| Database | PostgreSQL (Supabase) + Prisma               | `SELECT … FOR UPDATE` and `FOR UPDATE SKIP LOCKED` are the whole design      |
| Queue    | Redis (Upstash) + BullMQ                     | Email only — the sweeper runs on Postgres ([ADR-018](docs/DECISIONS.md))     |
| Realtime | Socket.IO + Redis adapter                    | Rooms keyed `show:{id}`; the adapter is what makes multi-instance work       |
| Auth     | JWT (HS256, pinned) + Argon2id               | OWASP's first choice for password storage                                    |

Monorepo via npm workspaces: `apps/api`, `apps/web`, `packages/shared`.

---

## Quick start

Requires **Node 20.12+** (the API uses Node's native `.env` loading).

```bash
git clone https://github.com/Rupin-Gupta/Ticket-Booking.git
cd Ticket-Booking
npm install                              # installs all workspaces, generates the Prisma client
cp apps/api/.env.example apps/api/.env   # fill it in — see below
npm run db:migrate -- --name init        # creates the schema
npm run db:seed -w apps/api              # demo venue, event, shows and accounts
npm run dev                              # API on :4000, web on :5173
```

Open **http://localhost:5173**.

### Demo accounts

All use the password `password123`. The login screen lists them as one-click
buttons.

| Account                | Role      | Try                                  |
| ---------------------- | --------- | ------------------------------------ |
| `customer@ticket.dev`  | Customer  | Pick seats, hold, book, cancel       |
| `customer2@ticket.dev` | Customer  | Race the first one for the same seat |
| `organiser@ticket.dev` | Organiser | Manage events → **Sales & revenue**  |
| `admin@ticket.dev`     | Admin     | **Venues** → build a seat layout     |

### Worth trying

- **The race** — open one showtime in two browsers, sign in as each customer,
  and hold the same seat. One wins, the other is told it just went. The loser's
  map updates without a refresh.
- **The hold timer** — hold seats and watch the countdown; walk away and the
  seats free themselves.
- **The waitlist** — sell out a category, join the queue from another account,
  then cancel the booking. The offer email arrives with a time-limited link.
- **The QR** — book, then scan the code on the ticket page with your phone.

---

## Environment variables

Full annotated list in [`apps/api/.env.example`](apps/api/.env.example).

### Supabase gives three connection strings — take two, and not the third

| Variable       | Which string                      | Port   | Used by                                               |
| -------------- | --------------------------------- | ------ | ----------------------------------------------------- |
| `DATABASE_URL` | **Transaction** pooler            | `6543` | The running app                                       |
| `DIRECT_URL`   | **Session** pooler                | `5432` | `prisma migrate`                                      |
| —              | ~~Direct~~ `db.<ref>.supabase.co` | —      | **Never** — IPv6-only; works locally, fails on Render |

`DATABASE_URL` must end in `?pgbouncer=true`: the transaction pooler cannot
support prepared statements, which Prisma uses by default. Migrations need the
session pooler because they take advisory locks, which are session state.

**Percent-encode special characters in the password** — these strings are URLs:
`@` → `%40`, `#` → `%23`, `/` → `%2F`, `?` → `%3F`.

### The rest

| Variable                    | Notes                                                           |
| --------------------------- | --------------------------------------------------------------- |
| `JWT_SECRET`                | ≥32 chars. `openssl rand -base64 48`                            |
| `REDIS_URL`                 | Upstash **TCP** URL (`rediss://…:6379`), not the REST URL       |
| `RESEND_API_KEY`            | See the delivery note below                                     |
| `MAIL_REDIRECT_TO`          | Dev only — ignored in production ([ADR-021](docs/DECISIONS.md)) |
| `WEB_URL`                   | Comma-separated CORS allowlist                                  |
| `HOLD_TTL_SECONDS`          | Default 600. Tests set it to seconds                            |
| `OFFER_TTL_SECONDS`         | Default 600                                                     |
| `SWEEPER_INTERVAL_MS`       | Default 10000                                                   |
| `MAX_SEATS_PER_HOLD`        | Default 6                                                       |
| `MAX_ACTIVE_HOLDS_PER_USER` | Default 2, counted per show                                     |

> **Email delivery.** Resend's shared `onboarding@resend.dev` sender only
> delivers to the address that owns the Resend account. Set `MAIL_REDIRECT_TO`
> to your own address to see real ticket emails in development — the intended
> recipient is preserved in the subject line. Verify a domain for real delivery.

> ⚠️ **Supabase pauses a free project after 7 days with no database activity**,
> and restoring it is a manual click. `/health` runs a `SELECT 1`, and
> [`.github/workflows/keepalive.yml`](.github/workflows/keepalive.yml) pings it
> daily. Set the `API_URL` repository variable or that workflow does nothing.

---

## Seat hold and TTL

A physical `Seat` belongs to a venue and carries no status — a chair does not
know whether it is sold. `instantiateShowSeats()` generates one `ShowSeat` per
seat per show, inside the same transaction that creates the show. That row
carries the live status and is what everything locks.

**Expiry works at two levels, and only one is the guarantee.**

**Lazy expiry is correctness.** Every read and every mutation passes rows
through `effectiveStatus()`, which treats `HELD` past `holdExpiresAt` as
`AVAILABLE`. A seat is bookable the instant its lease lapses **even if every
background job is dead**. No abandoned checkout can lock a seat permanently.

**The sweeper is visibility.** A ten-second interval flips expired rows and
broadcasts, so other people's screens stop showing the seat as grey.

It is a plain `setInterval` running two indexed `UPDATE`s, not a queued job. An
idle BullMQ worker's blocking poll costs ~518,000 Redis commands a month against
Upstash's 500,000 free-tier allowance — a queue here would exhaust the tier in
about three days, silently. Redis stays for email and the socket adapter, where
it earns its place. ([ADR-018](docs/DECISIONS.md))

```
                  ┌──────────── TTL expires / released ───────┐
                  ▼                                           │
           ┌─────────────┐   seats selected           ┌────────────┐
           │  AVAILABLE  │ ─────────────────────────▶ │    HELD    │
           └─────────────┘                            └─────┬──────┘
                  ▲                                         │ booking confirmed
                  │ queue empty                             ▼
           ┌──────┴───────┐  offer expires           ┌────────────┐
           │   OFFERED    │ ◀──────────────┐         │   BOOKED   │
           │ (one named   │                │         └─────┬──────┘
           │  customer)   │ ───────────────┘               │ cancelled
           └──────┬───────┘  advanceWaitlist()             ▼
                  │ offer accepted            advanceWaitlist()
                  └────────────▶ BOOKED
```

`OFFERED` is a distinct status because an expired hold returns to `AVAILABLE`
while an expired offer must walk the queue. One status for both would force the
sweeper to guess which kind of expiry it found.

---

## Concurrency protection

The bug being defended against is check-then-write:

```ts
const seat = await prisma.showSeat.findUnique({ where: { id } });
// ← another request interleaves here
if (seat.status === 'AVAILABLE') {
  /* both write HELD, second wins silently */
}
```

`POST /shows/:id/holds` runs **one transaction** that opens by locking:

```sql
SELECT ss.id, ss.status, ss."holdExpiresAt", …
FROM "ShowSeat" ss JOIN "Seat" s ON s.id = ss."seatId"
WHERE ss.id = ANY($1) AND ss."showId" = $2
ORDER BY ss.id
FOR UPDATE OF ss
```

The second contender blocks until the first commits, reads `HELD`, and gets a
clean `409`.

Three details are load-bearing:

- **`ORDER BY ss.id`** — two customers requesting the same pair in opposite
  orders deadlock without it, and Postgres resolves a deadlock by killing a
  transaction, turning a clean conflict into a 500.
- **`FOR UPDATE OF ss`** — locks only `ShowSeat`. A bare `FOR UPDATE` would also
  lock the joined `Seat` rows and serialise unrelated shows in the same venue.
- **The query locks _and_ reads** — so the lock is held for two round trips, not
  four. With four, twenty contenders exceeded Prisma's 5s transaction timeout
  and seven of twenty returned 500 instead of 409.

Holds are all-or-nothing: a partial hold is worse UX than a clean rejection and
leaks seats when the cart is abandoned.

### Defence in depth

| Layer                                       | Stops                                        |
| ------------------------------------------- | -------------------------------------------- |
| `FOR UPDATE` in the hold/book transaction   | Two customers racing for one seat            |
| `BookingSeat_showSeatId_live_key` (partial) | One seat in two **live** bookings, ever      |
| `@@unique([showId, seatId])`                | A show being instantiated twice              |
| `FOR UPDATE SKIP LOCKED` on the queue pick  | Two sweepers offering one seat to two people |
| Rate limits + per-customer hold caps        | One script holding the whole venue           |

---

## Waitlist and time-limited offers

`advanceWaitlist(tx, showSeatId)` is the **only** implementation of "a seat
became free, find the next customer". Cancellation calls it; offer expiry calls
the same function. Two copies drift on exactly the clauses that matter.

1. **Join** — only when the category is genuinely sold out (an expired lease
   counts as available, so a stale row cannot push someone into a queue).
   Duplicate live entries are refused.
2. **A seat frees** — cancellation passes each seat to `advanceWaitlist()`
   inside the same transaction that freed it.
3. **The queue is read**, FIFO, one row at a time:
   `ORDER BY "joinedAt" ASC LIMIT 1 FOR UPDATE SKIP LOCKED`. `SKIP LOCKED` means
   a concurrent advance steps over a row already being offered rather than
   handing one customer two offers.
4. **Seat goes `OFFERED`**, the entry gets an `offerToken` of 32 CSPRNG bytes
   and an expiry, and an email goes out with the link.
5. **They accept — or the clock runs out.** Accepting checks five things: the
   token resolves, the entry is still `OFFERED`, it has not expired, the seat is
   still `OFFERED`, and **the caller is the customer it was offered to**. The
   token arrives by email, and email gets forwarded. Success clears it — single
   use.
6. **Expiry marks the entry `EXPIRED` and calls `advanceWaitlist()` again**,
   rather than freeing the seat. That is the loop: an ignored offer walks down
   the queue by itself, reaching general sale only when the line is empty.

Queue position is derived (`count(earlier WAITING) + 1`), never stored — a
stored column would need renumbering everyone behind on every departure
([ADR-023](docs/DECISIONS.md)).

---

## Database schema

Full schema: [`apps/api/prisma/schema.prisma`](apps/api/prisma/schema.prisma).

```
User ──< Event ──< SeatCategory ──┐
  │        │                      │
  │        └──< Show ──< ShowSeat ┘        Venue ──< Seat ──< ShowSeat
  │                        │
  └──< Booking ──< BookingSeat ───┘
  └──< WaitlistEntry
```

| Model           | Carries                                                                                     | Key constraint                                            |
| --------------- | ------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `Seat`          | Section, row, number, `posX`/`posY` — geometry, written once per venue                      | `@@unique([venueId, section, row, number])`               |
| `SeatCategory`  | Name, price, **`sections[]`** — which venue sections this band covers                       | `@@unique([eventId, name])`                               |
| `ShowSeat`      | `status`, `heldByUserId`, `holdExpiresAt`, `offerExpiresAt` — the live row everything locks | `@@unique([showId, seatId])`, `@@index([showId, status])` |
| `Booking`       | `reference` (human-facing), `qrToken` (32 random bytes)                                     | both `@unique`                                            |
| `BookingSeat`   | `priceAtBooking`, `releasedAt`                                                              | partial unique on `showSeatId WHERE releasedAt IS NULL`   |
| `WaitlistEntry` | `joinedAt`, `offerToken`, `offerExpiresAt`                                                  | `@@index([showId, categoryId, status, joinedAt])`         |

Two details worth calling out:

- **`priceAtBooking` is a snapshot.** Revenue is summed from it, never from the
  category's current price, so re-pricing never rewrites past bookings.
- **The `BookingSeat` seatbelt is a _partial_ unique index**, created by hand in
  `20260822120000_booking_seat_release`. A plain `@unique` made a cancelled seat
  unsellable forever, because the row survives cancellation for history. Prisma
  cannot express a partial index, so a future `migrate dev` may report it as
  drift — see [docs/DEBUGGING.md](docs/DEBUGGING.md).

---

## API reference

Base `/api/v1`. Bearer token auth. Full reference with request and response
shapes: **[docs/API.md](docs/API.md)**.

Every failure has one shape:

```json
{ "error": { "code": "SEAT_UNAVAILABLE", "message": "Seat A12 was just taken." } }
```

| Area      | Endpoints                                                                                                                                               |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Auth      | `POST /auth/register` · `POST /auth/login` · `GET /auth/me`                                                                                             |
| Venues    | `GET /venues` · `GET /venues/:id` · `POST /venues` · `POST /venues/:id/seats` _(admin)_                                                                 |
| Events    | `GET /events` _(filters + paging)_ · `GET /events/:id` · `GET /events/mine` · `POST /events` · `POST /events/:id/categories` · `POST /events/:id/shows` |
| Seats     | `GET /shows/:id/seats` · `POST /shows/:id/holds` · `DELETE /shows/:id/holds` · `GET /holds/me`                                                          |
| Bookings  | `POST /bookings` · `GET /bookings` · `GET /bookings/:id` · `POST /bookings/:id/cancel` · `GET /verify/:qrToken`                                         |
| Waitlist  | `POST /shows/:id/waitlist` · `GET /waitlist/me` · `DELETE /waitlist/:id` · `GET /waitlist/offers/:token` · `POST /waitlist/offers/:token/accept`        |
| Organiser | `GET /organiser/events/:id/summary`                                                                                                                     |
| Realtime  | Socket.IO — client emits `show:join`/`show:leave`, server emits `seat:sync`/`seat:update`                                                               |

`GET /shows/:id/seats` never returns `heldByUserId`. The public map shows _that_
a seat is held, never _who_ holds it.

---

## Tests

```bash
NODE_ENV=test npm test -w apps/api      # 79 tests
npm run typecheck                       # all three workspaces
```

They run against real Postgres over the real HTTP stack — SQLite would serialise
writes for free and hide the very races being tested. Each file tags its
fixtures with a random run id and cleans up after itself.

The two that matter most:

- **`tests/concurrency/holds.test.ts`** — twenty parallel holds at one seat,
  asserting exactly one `201`, nineteen `409`s, and one `HELD` row in the
  database. Also covers overlapping seat pairs requested in opposite orders,
  which is the deadlock case.
- **`tests/concurrency/waitlist.test.ts`** — drives one seat through three
  customers to general sale purely by letting each offer lapse, and asserts the
  offer always goes to the earliest `joinedAt` and to nobody else.

---

## Deployment

**Database** — Supabase, already migrated by `npm run db:migrate`.

**API → Render.** [`render.yaml`](render.yaml) is a Blueprint: Render → New →
Blueprint → point at this repo. Set the secrets it marks `sync: false`. The
build runs `prisma migrate deploy`, so a deploy applies pending migrations.

**Web → Vercel.** Keep Root Directory as the repo **root** (not `apps/web` — the
workspace would not resolve). [`vercel.json`](vercel.json) points the build at
the workspace and handles SPA rewrites. Set `VITE_API_URL` to the Render URL.

**Then, in order:**

1. Set `WEB_URL` on Render to the Vercel URL — it is the CORS allowlist, and the
   Socket.IO handshake uses it too.
2. Set the `API_URL` repository variable so the keep-alive workflow works, and
   run it once manually to prove it.
3. Seed demo data against production.

> Render's free tier spins down when idle, so the first request after a quiet
> period takes ~50s. The daily keep-alive mitigates it; warm the app before
> demoing.

---

## Documentation map

| File                                         | What lives there                                                       |
| -------------------------------------------- | ---------------------------------------------------------------------- |
| [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md)         | The 800-word write-up: hold TTL, concurrency, waitlist, offer handling |
| [docs/API.md](docs/API.md)                   | Full endpoint reference                                                |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Mechanisms in depth, and the limits accepted on purpose                |
| [docs/DECISIONS.md](docs/DECISIONS.md)       | 26 ADRs — every non-obvious choice, what it beat, and why              |
| [docs/DEBUGGING.md](docs/DEBUGGING.md)       | Traps written down before they bite, plus a symptom → cause → fix log  |
| [docs/TODO.md](docs/TODO.md)                 | Phase-by-phase checklist with a "done when" test per phase             |
| [docs/CONTEXT.md](docs/CONTEXT.md)           | Rolling session journal                                                |
| [CLAUDE.md](CLAUDE.md)                       | Condensed project memory and the non-negotiable rules                  |
