# TODO

Single source of truth for progress. Tick items as they land; a phase closes
only when its checks pass and its docs are updated in the same commit.

Legend: `[ ]` not started · `[~]` in progress · `[x]` done

---

## Phase 0 — Foundations

- [x] `CLAUDE.md` project memory
- [x] Docs skeleton (`README`, `ARCHITECTURE`, `CONTEXT`, `DECISIONS`, `RULES`, `DEBUGGING`, `TODO`, `API`)
- [x] Blueprint artifact published
- [x] `git init` + `.gitignore` + first commit
- [x] npm workspaces root (`apps/api`, `apps/web`, `packages/shared`)
- [x] TypeScript strict + Prettier, shared base tsconfig — ESLint deliberately
      skipped, see ADR-011
- [x] Express skeleton: health route, error handler, request logging, helmet, CORS
- [x] Vite + React skeleton, routing, API client, dev proxy
- [x] `packages/shared` enums + `SeatView`, with a compile-time parity check
      against the Prisma enums
- [x] Prisma schema written from `CLAUDE.md` (+ the `Show.bookings`
      back-relation it was missing), client generates
- [x] `apps/api/.env.example` with every key, validated by `src/env.ts`
- [x] Supabase project created, `DATABASE_URL` (`:6543`, `?pgbouncer=true`) +
      `DIRECT_URL` (`:5432`) set
- [x] `JWT_SECRET` generated
- [x] First migration applied — `20260822094817_init`, all 10 tables live
- [x] Upstash Redis created, `REDIS_URL` set — TCP `rediss://`, not the REST URL
- [ ] Resend account, `RESEND_API_KEY` set ← user action, **needed by Phase 4**

**Done when:** `npm run dev` starts both apps, `/health` returns 200, and
`prisma migrate dev` applies cleanly against Supabase. ✅ **All three true.**

**Verified:** `npm run typecheck` clean across all three workspaces ·
`/health` 200 with a config checklist · vite proxy reaches the API ·
CORS returns no allow-origin for a foreign origin · helmet headers present ·
404s return the standard error shape · 10 tables, 5 enums and the two
`ShowSeat` indexes confirmed present **through the pooled `:6543` connection**,
which is what proves `?pgbouncer=true` is working.

> Upstash and Resend are listed here because the accounts belong with the other
> setup, but neither blocks Phase 1 or Phase 2.

## Phase 1 — Auth and roles

- [x] Argon2id hash/verify helpers, with a decoy hash so an unknown email costs
      the same time as a real one
- [x] `POST /auth/register` — role hard-coded `CUSTOMER`, not even parsed
- [x] `POST /auth/login` — JWT, `HS256` pinned on sign **and** verify, 15 min
- [x] `GET /auth/me`
- [x] `requireAuth` + `requireRole([...])` middleware
- [x] Zod validation on every body, central error handler → consistent error shape
- [x] Rate limit on `/auth/login` (10 / 15 min) and `/auth/register` (5 / hour)
- [x] Seed script: one admin, one organiser, two customers
- [x] Design system: tokens (light + dark), type scale, Button / Field / Alert /
      Card primitives, SVG icon set, app shell, theme toggle
- [x] Web: register / login / logout, token storage, `RequireAuth` guard
- [x] 10 auth tests on `node:test`, all green

**Done when:** a customer cannot reach an organiser route, and a register
request carrying `"role":"ADMIN"` still produces a `CUSTOMER`. ✅ **Both
asserted, the second against the database row rather than the response.**

Also covered by test: duplicate email → 409, short password → 400, wrong
password and unknown email return byte-identical errors, a wrong-secret token
is rejected, and an `alg:none` token is rejected.

## Phase 2 — Venues, events, shows

- [x] Admin: venue CRUD + bulk seat creation (rows × columns → `posX`/`posY`),
      blocks stack automatically so sections cannot overlap
- [x] Organiser: event CRUD, ownership-checked in the service, not just the route
- [x] Seat categories per event with pricing + `sections[]` (ADR-016)
- [x] Show creation → `instantiateShowSeats()` in one transaction
- [x] Public: `GET /events` with filters (type, venue, date, search) + paging,
      `GET /events/:id`, `GET /shows/:id`
- [x] Web: event list + filters, event detail, show picker
- [x] Web: admin venue builder with a live layout preview, organiser event form
- [x] Seed builds a 100-seat venue, a priced event and two shows with full maps
- [x] 12 more tests (23 total), all green

**Done when:** creating a show materialises exactly one `ShowSeat` per venue
seat, priced by category, and re-running it is refused by the unique constraint.
✅ **Asserted directly** — seat count matches the venue, every row is
`AVAILABLE`, and each carries the category claiming its section.

Also covered: a show is refused while any section is unpriced (and rolls back
completely), two categories cannot claim one section, a category cannot name a
section the venue lacks, re-adding a seat block 409s, and a second organiser
gets 403 on someone else's event with the row left untouched.

## Phase 3 — Seat map, holds, concurrency ⭐ evaluation-critical

- [x] `GET /shows/:id/seats` — explicit select, `heldByUserId` never serialised
- [x] `POST /shows/:id/holds` — locked transaction, `ORDER BY ss.id FOR UPDATE OF ss`
- [x] Lazy expiry (`effectiveStatus`) applied on every read and every mutation
- [x] `DELETE /shows/:id/holds` — releases only the caller's own seats
- [x] `GET /holds/me`
- [x] `MAX_SEATS_PER_HOLD` + `MAX_ACTIVE_HOLDS_PER_USER` caps
- [x] Rate limit on hold creation (20/min)
- [x] Sweeper — `setInterval` on Postgres, not BullMQ (ADR-018)
- [x] **Concurrency test: 20 parallel holds on one seat → exactly one 201**
- [x] Web: seat grid with status, selection, basket, hold countdown, release

**Done when:** the concurrency test is green and the DB shows exactly one `HELD`
row after 20 simultaneous attempts. ✅ **Both, and stable across three
consecutive runs.**

Also covered: overlapping seat pairs requested in opposite orders produce one
201 and one 409 with no deadlock and no partial hold; an expired hold is
bookable without the sweeper having run; the seat map reports an expired hold
as `AVAILABLE`; the sweeper clears expired rows and leaves live ones; the
response body contains no `heldByUserId` at all; and the per-request cap,
duplicate seat ids, foreign seats and missing auth are all rejected.

> `DELETE /holds/:id` in the original route sketch became
> `DELETE /shows/:id/holds`. Holds live on `ShowSeat` rows, so a hold "id"
> would need a `Hold` table duplicating state that `ShowSeat` already owns —
> exactly the second source of truth ADR-001 exists to avoid.

## Phase 4 — Booking, QR, email

- [x] `POST /bookings` — locked, requires seats `HELD` by caller and unexpired
- [x] `reference` generator (typo-resistant alphabet) + `qrToken` (32 random bytes)
- [x] `GET /bookings` (history), `GET /bookings/:id` (owner-checked, carries the QR)
- [x] `POST /bookings/:id/cancel` — owner-checked, refuses after the show starts
- [x] `GET /verify/:qrToken` — public, reveals nothing about the customer
- [x] BullMQ email worker: renders QR, sends via Resend, 5 attempts with backoff
- [x] `MAIL_REDIRECT_TO` so seeded accounts can receive mail in dev (ADR-021)
- [x] Web: checkout from a hold, ticket page with QR, history, cancel, verify page
- [x] Partial unique index replacing the too-tight `@unique` (ADR-020)
- [x] 17 more tests (52 total), all green

**Done when:** a booking returns immediately, the email lands with a scannable
QR, and killing the mail provider does not fail the booking. ✅ **All three —
and the third was proven accidentally**: Resend rejected the first send because
the demo address is not the account owner, the job retried five times and
failed, and the booking stayed confirmed throughout.

Also covered: booking someone else's held seat is refused and leaves their hold
intact; an expired hold cannot be booked; two simultaneous bookings of one seat
produce exactly one; a stranger gets 403 on both reading and cancelling; a
cancelled ticket verifies as invalid rather than vanishing; and a released seat
can be booked again — the case the old `@unique` made impossible.

## Phase 5 — Waitlist and time-limited offers ⭐ evaluation-critical

- [x] `POST /shows/:id/waitlist` — sold-out categories only, no duplicate entries
- [x] `GET /waitlist/me` (with derived queue position), `DELETE /waitlist/:id`
- [x] `advanceWaitlist(showSeatId)` — FIFO by `joinedAt`, `FOR UPDATE SKIP LOCKED`
- [x] Cancellation calls it; offer expiry calls the **same** function (rule 3)
- [x] `POST /waitlist/offers/:token/accept` — all five checks
- [x] `GET /waitlist/offers/:token` — public read, `410` once expired
- [x] Sweeper branch: expired offers → `EXPIRED` → `advanceWaitlist()`
- [x] Offer email with a time-limited link
- [x] Leaving while holding an offer hands the seat straight on
- [x] **Waitlist test: three waiting, cancel one → earliest `joinedAt` only**
- [x] Web: waitlist panel under the seat map, offer page with countdown
- [x] 14 more tests (66 total), all green

**Done when:** an ignored offer walks down the queue automatically, and an
expired token is refused. ✅ **Both** — one test drives a seat through alice →
bob → cara → back on general sale purely by letting each offer lapse.

Also covered: joining is refused while seats remain; a refresh cannot buy a
second place in line; a stranger holding the emailed link gets 403 and the seat
is untouched; a token expired by one second is refused; a used token no longer
resolves at all; and leaving frees the position for those behind.

## Phase 6 — Real-time seat map

- [x] Socket.IO server on the same port, `show:{showId}` rooms
- [x] `seat:sync` on join, `seat:update` after every committed mutation —
      holds, releases, bookings, cancellations, hold sweeps, offer expiry
- [x] `@socket.io/redis-adapter` wired, with both connections closed on shutdown
- [x] Web: live seat map with a connection indicator, polling fallback
- [x] Verify across two clients: A holds → B sees it without asking
- [x] No socket auth — deliberate, see ADR-025
- [x] 6 more tests (72 total), all green

**Done when:** no broadcast fires from inside a transaction, and two API
instances still deliver every update. ✅ **Both** — every `broadcast*` call
sits after its `$transaction` resolves, and the Redis adapter is wired and
confirmed at boot.

Also covered: a room only hears about its own show; the broadcast's keys are
exactly `['id','status']`, so no viewer-specific field can creep in; and a
release broadcasts `AVAILABLE` to everyone watching.

## Phase 7 — Organiser dashboard and polish

- [x] `GET /organiser/events/:id/summary` — revenue, seats sold, capacity,
      bookings, cancellations and waitlist depth, by category **and** by show
- [x] Web: dashboard with a summary row, per-category bars and a per-show table
- [x] Design system applied consistently (established Phase 1, not regenerated)
- [x] Loading / empty / error states on every screen, accessible seat grid
- [x] Mobile: seat map and every table scroll in their own container
- [x] 7 more tests (79 total), all green

**Done when:** revenue reconciles against `priceAtBooking` sums, and cancelled
bookings are excluded. ✅ **Both, asserted three ways** — the summary is
compared against the raw `BookingSeat` rows, the per-category totals and the
per-show totals must each sum to the headline figure.

Also covered: a customer gets 403 and another organiser gets 403 on someone
else's revenue; an unsold event reports zeroes without dividing by zero; and
re-pricing a category to 999 does not move a single rupee of past revenue.

## Phase 8 — Deploy, document, verify

- [ ] API → Render (env vars, build + start, worker process)
- [ ] Web → Vercel (`VITE_API_URL`)
- [ ] DB → Supabase, migrations applied via `DIRECT_URL`
- [ ] **Daily keep-alive cron hitting the deployed `/health`** — Supabase pauses
      a free project after 7 days of no queries and restoring it is manual
- [ ] Smoke-test the full flow live: browse → hold → book → email → cancel → offer
- [ ] Re-run the concurrency test against production
- [ ] README: setup, `.env.example`, API docs, DB schema, hold + waitlist logic
- [ ] `SYSTEM_DESIGN.md` — 800 words max
- [ ] Zip the source

**Done when:** all four deliverables exist and the hosted URL works from a
device that never ran the project.

## Phase 9 — Hardening (optional, if time allows)

- [ ] Rich seed script with a realistic demo show
- [ ] Dockerfile + docker-compose for one-command local run
- [ ] Graceful shutdown (drain HTTP, close BullMQ workers, disconnect Prisma)
- [ ] Structured logging with request IDs
- [ ] GitHub Actions CI running both test suites
- [ ] Load test on the hold endpoint
- [ ] OpenAPI spec
- [ ] Error tracking (Sentry free tier)

---

## Cross-cutting, do not let these slip

- [ ] `.env` never committed; `.env.example` always current
- [ ] `DATABASE_URL` on `:6543` with `?pgbouncer=true`, `DIRECT_URL` on `:5432`,
      neither pointing at `db.<ref>.supabase.co`
- [ ] `heldByUserId` never in a client payload
- [ ] Both test suites green before every phase close
- [ ] `docs/CONTEXT.md` updated at the end of every session
- [ ] Every new user rule appended to `docs/RULES.md`
