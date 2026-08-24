# Ticket Booking System

A ticket booking platform for movies and concerts. Customers pick seats from a
live map, held seats auto-release when checkout is abandoned, sold-out shows run
a FIFO waitlist that offers freed seats to the next person automatically, and
every confirmed booking emails a QR code ticket.

**Live app:** https://ticket-booking-zeta-azure.vercel.app · **API:** https://ticket-booking-api-sisp.onrender.com/health

```
169 tests passing · Python 3.12+ · FastAPI · SQLAlchemy 2.0
20 parallel holds on one seat, over real TCP: 1 x 201, 19 x 409, 0 errors
```

> **The API was ported from TypeScript to Python** on 2026-08-23. The frontend
> stays TypeScript — it is React in a browser, which is a platform constraint
> rather than a preference. The retired Node implementation is in git history up
> to commit `6c7dfd4` for anyone who wants to diff behaviour.

> First load may take ~50s — Render's free tier spins down when idle. A daily
> keep-alive keeps it warm; give it a moment if it has been quiet.

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
| **Organiser** | Book a venue for a slot (no double-booking), price each section, schedule shows (which generate the seat map), and read revenue by category and by show                    |
| **Admin**     | Create venues, build their seat layouts, and set their capabilities — stage layout, which event types they permit, and how long the room needs between shows               |

Roles are assigned server-side only. `POST /auth/register` hard-codes
`CUSTOMER`, and the request schema has no `role` field at all — organiser and
admin accounts come from the seed script.

---

## Stack

| Layer    | Choice                                           | Why                                                                                        |
| -------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| API      | Python 3.12+ · FastAPI · Pydantic v2             | OpenAPI comes free, and it is what generates the frontend's types                          |
| Frontend | React 19 + Vite, plain CSS custom properties     | Tokens give a seat that is `--seat-held` in both themes with no build config               |
| Database | PostgreSQL (Supabase) · SQLAlchemy 2.0 + Alembic | `SELECT … FOR UPDATE` and `FOR UPDATE SKIP LOCKED` are the whole design                    |
| Driver   | **psycopg3**, never asyncpg                      | asyncpg leaks prepared statements through Supabase's pooler ([ADR-027](docs/DECISIONS.md)) |
| Queue    | Redis (Upstash) + ARQ                            | Email only — the sweeper runs on Postgres ([ADR-018](docs/DECISIONS.md))                   |
| Realtime | python-socketio + Redis manager                  | Protocol rev 5, so the existing `socket.io-client` needs no change                         |
| Auth     | JWT (HS256, pinned) + Argon2id                   | OWASP's first choice for password storage                                                  |

`apps/api` is a Python package (`pyproject.toml`); `apps/web` and
`packages/shared` are npm workspaces.

---

## Quick start

Requires **Python 3.12+** and **Node 20.12+** (Node for the frontend only).

```bash
git clone https://github.com/Rupin-Gupta/Ticket-Booking.git
cd Ticket-Booking

# --- API
cd apps/api
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
cp .env.example .env                     # fill it in — see below
./.venv/bin/alembic upgrade head         # creates the schema
./.venv/bin/python -m ticket_api.seed    # demo venue, event, shows and accounts
cd ../..

# --- frontend + both processes
npm install
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
| `DIRECT_URL`   | **Session** pooler                | `5432` | `alembic upgrade`                                     |
| —              | ~~Direct~~ `db.<ref>.supabase.co` | —      | **Never** — IPv6-only; works locally, fails on Render |

The transaction pooler is pgbouncer, and pgbouncer cannot carry a prepared
statement across pooled connections — it is prepared on one backend and executed
on another that has never heard of it. That is why the driver is **psycopg3 with
`prepare_threshold=None`** and never asyncpg, which leaks prepared statements
through that pooler even with its own cache disabled ([ADR-027](docs/DECISIONS.md)).

A trailing `?pgbouncer=true` was a Prisma-only flag and is now stripped
automatically, so an old `.env` keeps working. Migrations need the **session**
pooler because Alembic takes an advisory lock, which is session state.

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
know whether it is sold. `instantiate_show_seats()` generates one `ShowSeat` per
seat per show, inside the same transaction that creates the show. That row
carries the live status and is what everything locks.

**Expiry works at two levels, and only one is the guarantee.**

**Lazy expiry is correctness.** Every read and every mutation passes rows
through `effective_status()`, which treats `HELD` past `holdExpiresAt` as
`AVAILABLE`. A seat is bookable the instant its lease lapses **even if every
background job is dead**. No abandoned checkout can lock a seat permanently.

**The sweeper is visibility.** A ten-second interval flips expired rows and
broadcasts, so other people's screens stop showing the seat as grey.

It is one `asyncio` task running two indexed `UPDATE`s on a ten-second sleep,
not a queued job. An idle ARQ worker's blocking poll costs ~518,000 Redis
commands a month against
Upstash's 500,000 free-tier allowance — a queue here would exhaust the tier in
about three days, silently. Redis stays for email and the socket adapter, where
it earns its place. ([ADR-018](docs/DECISIONS.md))

### Two clocks

Abandoning checkout and pressing Back are different events:

| Situation               | Seats free after |
| ----------------------- | ---------------- |
| Tab closed, walked away | **5 minutes**    |
| Explicit back or cancel | **15 seconds**   |

Back does not delete the hold — it _shortens_ it. The owner is kept, so a
customer who bounces back and forward can reclaim their seats instead of losing
them to somebody faster. No new mechanism: `effective_status()` makes the seat
bookable at exactly fifteen seconds, with the sweeper uninvolved.
([ADR-033](docs/DECISIONS.md))

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
           └──────┬───────┘  advance_waitlist()             ▼
                  │ offer accepted            advance_waitlist()
                  └────────────▶ BOOKED
```

`OFFERED` is a distinct status because an expired hold returns to `AVAILABLE`
while an expired offer must walk the queue. One status for both would force the
sweeper to guess which kind of expiry it found.

---

## Concurrency protection

The bug being defended against is check-then-write:

```python
seat = await session.get(ShowSeat, seat_id)
#  ← another request interleaves here
if seat.status is SeatStatus.AVAILABLE:
    ...  # both write HELD, the second wins silently, two customers own one seat
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
  four. With four, twenty contenders exceeded the transaction timeout and seven
  of twenty returned 500 instead of 409. Prisma's client-side `maxWait`/`timeout`
  are now server-side `lock_timeout` and `statement_timeout` via `set_config`,
  which is strictly stronger: a client-side deadline can be missed by a wedged
  client, a server-side one cannot.

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

`advance_waitlist(tx, showSeatId)` is the **only** implementation of "a seat
became free, find the next customer". Cancellation calls it; offer expiry calls
the same function. Two copies drift on exactly the clauses that matter.

1. **Join** — only when the category is genuinely sold out (an expired lease
   counts as available, so a stale row cannot push someone into a queue).
   Duplicate live entries are refused.
2. **A seat frees** — cancellation passes each seat to `advance_waitlist()`
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
6. **Expiry marks the entry `EXPIRED` and calls `advance_waitlist()` again**,
   rather than freeing the seat. That is the loop: an ignored offer walks down
   the queue by itself, reaching general sale only when the line is empty.

Queue position is derived (`count(earlier WAITING) + 1`), never stored — a
stored column would need renumbering everyone behind on every departure
([ADR-023](docs/DECISIONS.md)).

---

## Database schema

Full schema: [`apps/api/src/ticket_api/models.py`](apps/api/src/ticket_api/models.py).

The table and column names are Prisma's, deliberately kept: quoted PascalCase
tables, camelCase columns, native Postgres enums. Renaming them during the port
would have been a second variable in a rewrite whose whole value was provable
equivalence, and the hand-written partial index below is already written against
these names. The cost is one explicit `mapped_column("holdExpiresAt")` per
attribute — mechanical, and paid once.

```
User ──< Event ──< SeatCategory ──┐
  │        │                      │
  │        └──< Show ──< ShowSeat ┘        Venue ──< Seat ──< ShowSeat
  │                        │
  └──< Booking ──< BookingSeat ───┘
  └──< WaitlistEntry
```

| Model           | Carries                                                                                     | Key constraint                                          |
| --------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| `Seat`          | Section, row, number, `posX`/`posY` — geometry, written once per venue                      | unique `(venueId, section, row, number)`                |
| `SeatCategory`  | Name, price, **`sections[]`** — which venue sections this band covers                       | unique `(eventId, name)`                                |
| `ShowSeat`      | `status`, `heldByUserId`, `holdExpiresAt`, `offerExpiresAt` — the live row everything locks | unique `(showId, seatId)`, index `(showId, status)`     |
| `Booking`       | `reference` (human-facing), `qrToken` (32 random bytes)                                     | both unique                                             |
| `BookingSeat`   | `priceAtBooking`, `releasedAt`                                                              | partial unique on `showSeatId WHERE releasedAt IS NULL` |
| `WaitlistEntry` | `joinedAt`, `offerToken`, `offerExpiresAt`                                                  | index `(showId, categoryId, status, joinedAt)`          |

Two details worth calling out:

- **`priceAtBooking` is a snapshot.** Revenue is summed from it, never from the
  category's current price, so re-pricing never rewrites past bookings.
- **The `BookingSeat` seatbelt is a _partial_ unique index**, written by hand in
  the baseline migration. A plain unique constraint made a cancelled seat
  unsellable forever, because the row survives cancellation for history. Neither
  Prisma nor SQLAlchemy's declarative layer can express a partial index, so a
  future `alembic revision --autogenerate` may propose dropping it — that is
  drift, not an improvement. See [docs/DEBUGGING.md](docs/DEBUGGING.md).

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
npm run test:db:up                      # throwaway Postgres on :5433
npm run db:deploy:test                  # apply migrations to it
npm test                                # 120 tests, ~9s
npm run lint:api                        # ruff check + format --check
npm run typecheck                       # the two TypeScript workspaces
```

**Tests refuse to run against the production database.** `active_database_url()`
requires `DATABASE_URL_TEST` under `NODE_ENV=test` and will not fall back — a
suite that quietly writes to production is worse than one that will not start.
The email queue is guarded the same way, so tests never enqueue into the live
Upstash instance either.

They run against real Postgres over the real HTTP stack. SQLite would serialise
writes for free and hide the very races being tested.

The two that matter most:

- **`tests/concurrency/test_holds.py`** — twenty parallel holds at one seat,
  asserting exactly one `201`, nineteen `409`s, and one `HELD` row in the
  database. Runs against a **real uvicorn listener over TCP**, not httpx's
  in-process transport, which can serialise every request through one task and
  would pass even if the lock did nothing. Also covers overlapping seat pairs
  requested in opposite orders, which is the deadlock case.
- **`tests/concurrency/test_waitlist.py`** — several seats freed at once must go
  to distinct customers, and two people racing to accept one offer must produce
  exactly one booking.

---

## Deployment

**Database** — Supabase, migrated by `alembic upgrade head`.

**API → Render.** [`render.yaml`](render.yaml) is a Blueprint: Render → New →
Blueprint → point at this repo. Set the secrets it marks `sync: false`. The
build runs `alembic upgrade head`, so a deploy applies pending migrations.
Runtime is `python`, `rootDir` is `apps/api`, and it starts one uvicorn worker —
one deliberately, because Socket.IO connections are stateful and a second worker
without the Redis manager wired would silently drop half of every broadcast.

**Web → Vercel.** Keep Root Directory as the repo **root** (not `apps/web` — the
workspace would not resolve). [`vercel.json`](vercel.json) points the build at
the workspace and handles SPA rewrites. Set `VITE_API_URL` to the Render URL.

### Verifying the deployment

```bash
cd apps/api && ./.venv/bin/python scripts/verify_production.py https://your-api.onrender.com
```

Checks health, that `NODE_ENV` is really production, that every service is
configured and the database reachable, that CORS rejects a foreign origin, that
the seat map still hides `heldByUserId` — and then **re-runs the twenty-way
concurrency race against the live deployment**. That last one matters: on
localhost the app, the test and Postgres share a machine, while in production the
lock is held across a network, behind a connection pooler, on an instance that
may have just cold-started.

It creates its contenders directly in the database and mints their tokens
locally rather than driving `/auth/register` — twenty signups from one IP are
blocked by our own rate limiter long before they reach the seat, which is the
limiter working. Clean up afterwards with `-- --cleanup`.

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
| [docs/CONVENTIONS.md](docs/CONVENTIONS.md)   | Condensed stack, schema and the non-negotiable rules                   |
