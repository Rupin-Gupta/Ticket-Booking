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
- [ ] **Neon project created, `DATABASE_URL` + `DIRECT_URL` set** ← user action
- [ ] **Upstash Redis created, `REDIS_URL` set** ← user action
- [ ] `JWT_SECRET` generated (`openssl rand -base64 48`) ← user action
- [ ] First migration applied (`npm run db:migrate`) — needs Neon first

**Done when:** `npm run dev` starts both apps, `/health` returns 200, and
`prisma migrate dev` applies cleanly against Neon.

**Verified so far:** `npm run typecheck` clean across all three workspaces ·
`/health` 200 with a config checklist · vite proxy reaches the API ·
CORS returns no allow-origin for a foreign origin · helmet headers present ·
404s return the standard error shape. Migration is the only item left, and it
is blocked on the three accounts above.

## Phase 1 — Auth and roles

- [ ] Argon2id hash/verify helpers
- [ ] `POST /auth/register` — role hard-coded `CUSTOMER`
- [ ] `POST /auth/login` — JWT, `HS256` pinned, 15 min expiry
- [ ] `GET /auth/me`
- [ ] `requireAuth` + `requireRole([...])` middleware
- [ ] Zod validation on every body, central error handler → consistent error shape
- [ ] Rate limit on `/auth/login`
- [ ] Seed script: one admin, one organiser, two customers
- [ ] Web: register / login / logout, token storage, protected routes

**Done when:** a customer cannot reach an organiser route, and a register
request carrying `"role":"ADMIN"` still produces a `CUSTOMER`.

## Phase 2 — Venues, events, shows

- [ ] Admin: venue CRUD + bulk seat creation (rows × columns → `posX`/`posY`)
- [ ] Organiser: event CRUD, ownership-checked
- [ ] Seat categories per event with pricing
- [ ] Show creation → `instantiateShowSeats()` in one transaction
- [ ] Public: `GET /events` with filters (type, date, venue, search), `GET /events/:id`
- [ ] Web: event list + filters, event detail, show picker
- [ ] Web: admin venue builder, organiser event form

**Done when:** creating a show materialises exactly one `ShowSeat` per venue
seat, priced by category, and re-running it is refused by the unique constraint.

## Phase 3 — Seat map, holds, concurrency ⭐ evaluation-critical

- [ ] `GET /shows/:id/seats` — explicit select, no `heldByUserId`
- [ ] `POST /shows/:id/holds` — locked transaction, `ORDER BY id FOR UPDATE`
- [ ] Lazy expiry treated as free everywhere a seat is read for mutation
- [ ] `DELETE /holds/:id` — explicit release
- [ ] `MAX_SEATS_PER_HOLD` + `MAX_ACTIVE_HOLDS_PER_USER` caps
- [ ] Rate limit on hold creation
- [ ] BullMQ repeatable sweeper — expired holds → `AVAILABLE`
- [ ] **Concurrency test: 20 parallel holds on one seat → exactly one 201**
- [ ] Web: seat grid with status colours, selection, hold countdown timer

**Done when:** the concurrency test is green and the DB shows exactly one `HELD`
row after 20 simultaneous attempts.

## Phase 4 — Booking, QR, email

- [ ] `POST /bookings` — locked, requires seats `HELD` by caller and unexpired
- [ ] `reference` generator + `qrToken` (32 random bytes)
- [ ] `GET /bookings` (history), `GET /bookings/:id`
- [ ] `POST /bookings/:id/cancel` — owner-checked
- [ ] `GET /verify/:qrToken` — the URL the QR encodes
- [ ] BullMQ email worker: render QR, send via Resend, retry + backoff
- [ ] Web: checkout, confirmation, booking history, cancel flow

**Done when:** a booking returns immediately, the email lands with a scannable
QR, and killing the mail provider does not fail the booking.

## Phase 5 — Waitlist and time-limited offers ⭐ evaluation-critical

- [ ] `POST /shows/:id/waitlist` — sold-out categories only, no duplicate entries
- [ ] `GET /waitlist/me`, `DELETE /waitlist/:id`
- [ ] `advanceWaitlist(showSeatId)` — FIFO, `FOR UPDATE SKIP LOCKED`
- [ ] Cancellation calls it; offer expiry calls the _same_ function
- [ ] `POST /waitlist/offers/:token/accept` — all five checks
- [ ] Sweeper branch: expired offers → `EXPIRED` → `advanceWaitlist()`
- [ ] Offer email with time-limited link
- [ ] **Waitlist test: three waiting, cancel one → earliest `joinedAt` only**
- [ ] Web: join waitlist, offer landing page with countdown

**Done when:** an ignored offer walks down the queue automatically, and an
expired token is refused.

## Phase 6 — Real-time seat map

- [ ] Socket.IO server, `show:{showId}` rooms, JWT handshake auth
- [ ] `seat:sync` on join, `seat:update` after every committed mutation
- [ ] `@socket.io/redis-adapter` wired
- [ ] Web: live seat map, polling fallback on disconnect
- [ ] Verify across two browsers: A holds → B sees grey within a second

**Done when:** no broadcast fires from inside a transaction, and two API
instances still deliver every update.

## Phase 7 — Organiser dashboard and polish

- [ ] `GET /organiser/events/:id/summary` — bookings, seats sold, revenue by category
- [ ] Web: organiser dashboard with the numbers and a per-show breakdown
- [ ] `ui-ux-pro-max` pass over the whole frontend
- [ ] Loading / empty / error states everywhere, accessible seat grid
- [ ] Mobile layout for the seat map

**Done when:** revenue reconciles against `priceAtBooking` sums, and cancelled
bookings are excluded.

## Phase 8 — Deploy, document, verify

- [ ] API → Render (env vars, build + start, worker process)
- [ ] Web → Vercel (`VITE_API_URL`)
- [ ] DB → Neon, migrations applied via `DIRECT_URL`
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
- [ ] `heldByUserId` never in a client payload
- [ ] Both test suites green before every phase close
- [ ] `docs/CONTEXT.md` updated at the end of every session
- [ ] Every new user rule appended to `docs/RULES.md`
