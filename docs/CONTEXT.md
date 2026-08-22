# Context Log

Rolling session journal. Newest entry on top. Read the top entry to know
exactly where the project stands; read `docs/TODO.md` to know what is next.

Update this at the end of every working session — a session that ends without
an entry here costs the next one twenty minutes of rediscovery.

---

## Current state

|                 |                                                                                                                     |
| --------------- | ------------------------------------------------------------------------------------------------------------------- |
| **Phase**       | **2 — COMPLETE.** Phase 3 (seat map, holds, concurrency) is next — evaluation-critical.                             |
| **Runnable?**   | Yes, end to end: browse → seat map → hold → book → QR ticket by email → view → cancel → seat back on sale.          |
| **Repo**        | Local git initialised. Remote `https://github.com/Rupin-Gupta/Ticket-Booking.git` — **not pushed yet, user pushes** |
| **Blocked on**  | Nothing. All three accounts are configured and working.                                                             |
| **Next action** | Phase 5: `advanceWaitlist()` FIFO with SKIP LOCKED, offer expiry sweeper branch, accept-offer endpoint.             |

Demo logins (`npm run db:seed -w apps/api`), all `password123`:
`admin@ticket.dev`, `organiser@ticket.dev`, `customer@ticket.dev`,
`customer2@ticket.dev`. The login screen lists them as one-click buttons.
Run `npm test -w apps/api` for the auth suite.

`/health` reports which of database / redis / auth / email are configured and
round-trips a `SELECT 1`, and the web placeholder renders that as a checklist —
so the remaining setup is visible without reading code.

---

## 2026-08-22 — Session 8: Phase 4, booking, QR and email

**Schema bug found by a test (ADR-020).** `BookingSeat.showSeatId @unique` —
described in my own docs as the seatbelt — meant a show-seat could appear in
**one booking ever**. The row survives cancellation on purpose (revenue history
and the cancellation email both need it), so the constraint occupied the seat
permanently and a cancelled seat could never be resold. The test asserting "a
released seat can be booked again" caught it.

Replaced with the invariant that was actually intended: a nullable `releasedAt`
plus a **partial** unique index, `WHERE "releasedAt" IS NULL`. Prisma cannot
express that, so migration `20260822120000_booking_seat_release` is hand-written
and the index is invisible to `schema.prisma` — a future `migrate dev` may
report it as drift and try to drop it. Flagged in three places.
`ShowSeat.bookingSeat` also became `bookingSeats[]`; the relation is
one-to-many across time.

**Rule 5 got proved by accident.** Resend rejected the first real send —
`onboarding@resend.dev` only delivers to the account owner — so the job failed
five times with backoff while the booking stayed confirmed the whole time. That
is exactly the property queuing exists for, demonstrated rather than asserted.
`MAIL_REDIRECT_TO` (ADR-021) now makes the demo deliverable in development;
it is refused under `NODE_ENV=production`.

**Two sweeper defects fixed**

- It fired a tick at import, before anything had connected, logging "can't
  reach database server" on every boot. Removed; the first sweep is one
  interval away, well inside any hold's TTL.
- Supabase's pooler recycles idle connections, so a quiet sweep found its
  socket closed (`P1017`). One retry is enough for Prisma to reconnect.

**Design notes worth keeping**

- The QR encodes `/verify/{token}`, never booking data. Raw JSON in a QR is
  forgeable by anyone with a generator; the short human reference is guessable.
- `qrToken` is absent from the history response and present only on the single
  booking its owner opens. It is a bearer credential for entry.
- The booking reference alphabet excludes I, O, 0 and 1 — it gets read aloud.
- `/verify` is public (door staff are not logged in) and returns nothing about
  the customer. A QR gets photographed and forwarded.
- A cancelled ticket still resolves, marked invalid. The door needs to tell
  "not a ticket" apart from "a cancelled ticket".

**Verified**

52/52 tests. Typecheck clean. Web builds (303 kB / 96 kB gzipped). Live: booked
BK-KU2QG, seats went BOOKED, owner saw the QR token and a stranger got 403,
`/verify` returned valid with no email in the body, cancelling released both
seats and invalidated the code, and the freed seat was immediately re-bookable
by another customer. A real email with the QR was delivered.

**Next session starts with**

Phase 5 — `advanceWaitlist()`, FIFO by `joinedAt` with `FOR UPDATE SKIP
LOCKED`, the offer-expiry sweeper branch calling the _same_ function as
cancellation, the accept-offer endpoint with all five checks, and the waitlist
ordering test.

---

## 2026-08-22 — Session 7: Phase 3, seat map, holds, concurrency

**The graded phase.** Seat map endpoint, the locked hold transaction, lazy
expiry, the sweeper, the 20-parallel-request test, and the seat grid UI.

**The failure that mattered, and the fix**

First run of the concurrency test: **exactly one 201** — the safety property
held, no double-sell — but **seven of twenty returned 500** instead of a clean 409.

Cause was time, not logic. Single requests take ~1.1s against Supabase in
another region, and the transaction made four round trips while holding row
locks. Twenty contenders serialise by design, so the later ones blew past
Prisma's defaults: 2s to acquire a connection, 5s to finish a transaction.

Fixed by shortening the transaction rather than lengthening the timeout:

- the `FOR UPDATE` query now **also reads the columns it checks**, so the lock
  is held for two round trips instead of four
- `FOR UPDATE OF ss` locks only `ShowSeat`, not the joined `Seat` rows, which
  would have serialised unrelated shows in the same venue
- the abuse cap moved outside the transaction (ADR-019) — it is not a
  correctness invariant, and every query under a lock is time other contenders
  spend blocked
- `maxWait` and `timeout` set explicitly, because Prisma's defaults assume
  uncontended work

The `FOR UPDATE`, the status re-read and the write stay together permanently.
Only the cap moved.

Now stable: 20/20 correct across three consecutive runs.

**Sweeper is not BullMQ (ADR-018)**

Upstash meters 500,000 commands per month, cumulatively. An idle BullMQ
worker's blocking poll costs ~518,000 on its own, and a 10-second repeatable
job costs millions — the free tier would die in about three days, silently.
The sweep is one idempotent indexed `UPDATE`; it runs on `setInterval` against
a database we are already connected to. Redis stays for email (Phase 4) and the
Socket.IO adapter (Phase 6), where it is genuinely needed.

**Route deviation**

`DELETE /holds/:id` became `DELETE /shows/:id/holds`. Holds live on `ShowSeat`
rows, so a hold "id" would need a `Hold` table duplicating state `ShowSeat`
already owns — the second source of truth ADR-001 exists to avoid.

**Frontend**

Seat map as a grid of real buttons, not an SVG: every seat is then focusable
with an accessible name carrying its status, so the map is keyboard-operable
for free. States differ in fill and hatching as well as hue — held-by-someone
-else is hatched, held-by-you is solid — because colour alone fails for roughly
one man in twelve. Countdown derives from the server's absolute expiry on every
tick, so a slept laptop cannot show time remaining on a seat already released.
Polling at 8s until Socket.IO replaces it in Phase 6.

**Verified**

35/35 tests green, concurrency suite stable over three runs. Typecheck clean.
Web builds (271 kB / 85 kB gzipped). Live: two real customers racing one seat
over HTTP → 201/409, `SEAT_UNAVAILABLE`; seat map has no `heldByUserId`
anywhere in the response body; the holder sees `heldByMe` and a countdown while
the other customer sees neither; release freed exactly one seat.

**Next session starts with**

Phase 4 — `POST /bookings` from held seats, `qrToken`, the queued email worker,
booking history and cancel. **Needs `RESEND_API_KEY`.**

---

## 2026-08-22 — Session 6: Phase 2, venues, events, shows

**Schema gap found and closed.** Seats belong to a venue, price categories
belong to an event, and nothing connected them — so `instantiateShowSeats()`
had no way to decide what a seat costs. `SeatCategory.sections String[]` closes
it (ADR-016), validated on write: sections must exist in the venue and no two
categories may claim the same one. Migration `20260822102226_category_sections`.

**Backend**

Venues module (admin-only writes, public reads) with bulk seat-block creation
that stacks blocks below whatever exists, so a caller never computes an offset
and sections cannot overlap. Events module with public browsing and filtering,
organiser-owned writes, category pricing, and show creation.

`instantiateShowSeats()` runs inside the same transaction that creates the
show, and refuses outright if any section is unpriced. A show whose seats
failed to generate is worse than no show — it renders as a bookable date over
an empty seat map.

Ownership is checked in the service, not the route. `requireRole(['ORGANISER'])`
says "some organiser"; `assertOwns()` says "the organiser who owns this event".
The test asserts the target row is unchanged after a 403, not just the status.

**Frontend**

Events browse with search, type, venue and date filters; event detail with
pricing and a show picker; admin venue builder with a live layout preview
rendered from the stored `posX`/`posY`; organiser screen for pricing sections
and scheduling shows. The organiser screen names which sections are still
unpriced and disables the show form until none are, because the server would
refuse anyway and a pre-empted 400 is better than a hit one.

**The bug worth remembering (ADR-017)**

`router.post('/x', middlewareArray, handler)` selects an Express overload that
stops inferring the handler's parameters — `req` and `res` silently become
`any`. Spreading the array restored inference and immediately surfaced **eight
real type errors** that the implicit `any` had been hiding: `req.params.id` is
genuinely `string | string[] | undefined`, and passing `description: undefined`
into a Prisma update is genuinely rejected under `exactOptionalPropertyTypes`.

Fixed properly rather than re-suppressed: `lib/http.ts` now has `param()`,
which validates instead of casting the union away, and `compact()`, which
strips `undefined` keys so a PATCH that omitted a field cannot blank a column.

An implicit `any` in a route handler is never cosmetic — it is the compiler
switched off exactly where request data enters.

**Correction to my own reporting:** I said "0 type errors" for the API earlier
in this session. That reading was wrong; re-running gave 19. The errors above
were present, not newly introduced.

**Verified**

23/23 tests green. Typecheck clean across all three workspaces. Web builds
(265 kB JS / 83 kB gzipped). Live against the running stack: public browsing
without a token, `type` and case-insensitive `q` filters, 403 for a customer
and an organiser on `POST /venues`, 403 for a customer on `/events/mine`, and
`GET /shows/:id` reporting 100 generated `ShowSeat` rows.

**Next session starts with**

Phase 3, the evaluation-critical one — seat map endpoint, the locked hold
transaction with `ORDER BY id FOR UPDATE`, lazy expiry, the BullMQ sweeper, and
the 20-parallel-request concurrency test. **Needs `REDIS_URL` from Upstash.**

---

## 2026-08-22 — Session 5: Phase 1, auth and the design system

**Backend**

Auth module (routes / service / schema), plus the primitives everything later
reuses: `lib/password.ts`, `lib/jwt.ts`, `middleware/auth.ts`,
`middleware/rateLimit.ts`. Seed script for admin, organiser and two customers.

The decisions that matter, since this is the layer the rest of the API trusts:

- The register schema has **no `role` field at all**. Zod strips unknown keys,
  so a body carrying `"role":"ADMIN"` never reaches the service. Not parsing it
  is a stronger guarantee than parsing and ignoring it.
- Login returns one identical code and message for unknown email and wrong
  password, and `verifyPassword()` hashes against a decoy when no user is found
  so both paths cost the same time. Matching text with mismatched timing is
  still an enumeration oracle.
- HS256 pinned on sign and verify. Verify is the half that matters.
- Duplicate email caught via the unique index (P2002), not a findFirst first —
  check-then-insert races two simultaneous signups.
- Passwords capped at 128 bytes; Argon2 on a megabyte of input is a real DoS.
- Rate limits skip under `NODE_ENV=test`, or the Phase 3 concurrency suite
  would be throttled by our own defence.

**Frontend — design system, built with the `ui-ux-pro-max` skill**

`styles/tokens.css` is the single source of colour, spacing, type and z-index,
in light and dark. Primitives: Button, Field, Alert, Card, plus a hand-rolled
SVG icon set. App shell with skip link, theme toggle (light / dark / system),
`AuthProvider`, `RequireAuth`.

The seat-status colours are already in the token file, with both themes — see
ADR-015. They are the palette the product is recognised by and had to be chosen
as one set with the brand, not bolted on in Phase 3.

No Tailwind, no component library — ADR-014.

**What changed structurally**

Prisma now loads at boot instead of lazily. The API had to start without a
database in Phase 0; from Phase 1 every route needs one, so
`requireEnv('DATABASE_URL')` fails immediately with a message naming the fix
rather than starting fine and 500-ing on every request.

**Found while testing**

A route appended after `createApp()` is shadowed by its `notFound` handler and
404s. Correct behaviour, and the reason `requireRole` gets its own minimal app
in the test rather than a route bolted onto the real one.

**Verified**

10/10 auth tests green. Typecheck clean in all three workspaces. Production
build of the web app succeeds (247 kB JS, 79 kB gzipped). Live check against
the running stack: seeded customer and organiser log in with the right roles,
`/auth/me` resolves, a wrong password 401s, and the vite proxy carries the
bearer token through.

**Next session starts with**

Phase 2 — admin venue CRUD with bulk seat creation, organiser events and
per-category pricing, and `instantiateShowSeats()` in one transaction.

---

## 2026-08-22 — Session 4: database live, Phase 0 closed

**Did**

- Owner filled in `apps/api/.env`. Migration `20260822094817_init` applied to
  Supabase — all 10 tables, 5 enums, every index.
- Verified the schema **through the pooled `:6543` connection**, not just the
  migration connection. That is the check that actually proves
  `?pgbouncer=true` works; querying only via `DIRECT_URL` would have passed
  while leaving the app's own path untested.
- `ShowSeat` confirmed carrying `ShowSeat_showId_seatId_key` and
  `ShowSeat_showId_status_idx` — the two the hold path depends on.

**Three real problems, all fixed**

1. **Blank env values crashed the boot.** `JWT_SECRET=""` from the copied
   example reached `z.string().min(32)` before `.optional()` applied. All
   optional vars now go through `blankAsUnset()`; the connection strings also
   gained `.url()`, so a malformed one fails at boot rather than at first query.
2. **Password contained `@`.** Connection strings are URLs, so it has to be
   `%40` or the parser takes the wrong `@` as the host delimiter. Both strings
   were re-encoded in place, and `.env.bak` was deleted rather than left
   sitting on disk holding the raw password.
3. **`npm run db:migrate -- --name init` silently swallowed the flag** — the
   outer `npm run` ate it, so Prisma fell back to prompting interactively for a
   name with no TTY and hung with zero output. Root script now ends in `--` so
   arguments forward. Verified: a second run reports "Already in sync" with no
   prompt.

**Correction worth recording:** the zero-output hang looked exactly like the
documented pooler trap and was called as such. It was not. The log showed
`Datasource "db": … at …:5432` — the connection had already succeeded. Check
whether the datasource line printed before blaming the pooler; that is now in
`docs/DEBUGGING.md`.

**Next session starts with**

Phase 1 — Argon2id helpers, `POST /auth/register` (role hard-coded `CUSTOMER`),
`POST /auth/login` with HS256 pinned, `requireAuth` / `requireRole`, seed
script, and the web login flow.

---

## 2026-08-22 — Session 3: database switched to Supabase

**Decided by the user.** Neon (ADR-008) is out, Supabase is in — ADR-013
supersedes it. The trade-off was put on the table first: Supabase pauses a free
project after 7 days of no database activity and unpausing is manual, where Neon
auto-wakes. The owner made the call; mitigation below is what makes it safe.

**Changed**

- `apps/api/.env.example`, `prisma/schema.prisma`, `README.md`,
  `docs/ARCHITECTURE.md` §9, `CLAUDE.md` rule 14 — all now describe the
  Supabase two-string setup and name the third string as banned.
- `/health` now round-trips `SELECT 1` and reports `up` / `unreachable` /
  `not-configured`. This is not decoration: it is the endpoint the daily
  keep-alive hits, and a query is the only thing that resets Supabase's 7-day
  idle timer. Prisma is imported dynamically inside the handler so the API
  still boots with no `DATABASE_URL`.
- `docs/DEBUGGING.md` — three new traps: the 7-day pause, the IPv6-only direct
  string that works locally and fails on Render, and
  `prepared statement "s0" already exists` when `?pgbouncer=true` is missing.
- Phase 8 in `docs/TODO.md` now carries the keep-alive cron as a real task.
- ADR-003's reasoning was corrected while nearby: `pg_cron` **is** available on
  Supabase, so the reason not to use it is no longer availability — it is that
  offer expiry has to call `advanceWaitlist()`, TypeScript with an email
  enqueue attached, which a SQL job would have to reimplement out of reach of
  every test.

**The rules that now matter**

| Variable       | String                 | Port   | For                               |
| -------------- | ---------------------- | ------ | --------------------------------- |
| `DATABASE_URL` | Transaction pooler     | `6543` | The app. Needs `?pgbouncer=true`. |
| `DIRECT_URL`   | Session pooler         | `5432` | `prisma migrate` only             |
| —              | `db.<ref>.supabase.co` | —      | **Never.** IPv6-only.             |

**Not done, flagged**

Local Postgres in Docker for development and the concurrency test was offered
and not taken up. It matters more now than it did with Neon: 20 parallel hold
requests through a hosted transaction pooler is slower and less deterministic
than the same test against a local container, and every test run is also
database activity on a free project. Revisit before Phase 3.

---

## 2026-08-22 — Session 2: Phase 0 scaffolding

**Did**

- npm workspaces monorepo: `apps/api`, `apps/web`, `packages/shared`.
  Shared base tsconfig, strict plus `noUncheckedIndexedAccess` and
  `exactOptionalPropertyTypes`.
- API: Express 5, helmet, CORS allowlist from `WEB_URL`, JSON limit,
  request logger, `ApiError` class, central error handler (Zod-aware),
  `/health` with a config checklist, graceful shutdown on SIGINT/SIGTERM.
- `src/env.ts`: zod-validated env using Node's native `process.loadEnvFile()`
  — no dotenv dependency. Infra vars are optional at boot with a `requireEnv()`
  helper, so a fresh clone runs before any account exists.
- Prisma schema written out from `CLAUDE.md`, with `directUrl` wired for the host.
- `packages/shared`: enums, `SeatView` (no `heldByUserId`, by construction),
  `ApiErrorBody`, socket event names.
- Web: Vite + React 19 + react-router, fetch wrapper with `ApiClientError`,
  dev proxy so there is no CORS in local dev. Placeholder page renders the
  API config checklist.

**Verified**

`npm run typecheck` clean in all three workspaces. `/health` 200. Vite proxy
reaches the API. CORS returns no allow-origin header for a foreign origin.
Helmet headers present, `x-powered-by` removed. Unknown routes return the
standard error shape.

**Found and fixed**

- `CLAUDE.md`'s schema was missing `Show.bookings` — `Booking.show` has no
  valid opposite side without it, and Prisma refuses to generate. Added.
- Two real strict-mode type errors caught before they shipped: a mapped type
  needing `-?`, and `exactOptionalPropertyTypes` rejecting `body: undefined`.

**Decided**

- No ESLint. TypeScript strict plus Prettier covers it at this size — ADR-011.
- Run TypeScript directly with `tsx` in production too; no build step — ADR-010.

**Known, accepted**

`npm audit` reports 3 high advisories, all one transitive dep (`deepmerge-ts`)
of the Prisma **CLI**, a devDependency. No fixed release exists yet — latest
prisma 7.9.1 is still inside the advisory range. Not reachable from request
data. Logged in `docs/DEBUGGING.md`; recheck at Phase 8.

**Next session starts with**

`npm run db:migrate` once Supabase is up, then Phase 1 — Argon2id, JWT, role
middleware, seed script.

---

## 2026-08-22 — Session 1: planning and docs

**Did**

- Read the brief and `CLAUDE.md`; confirmed stack and the fifteen
  non-negotiable rules as given, no changes proposed.
- Wrote the documentation set: `README.md`, `docs/ARCHITECTURE.md`,
  `docs/CONTEXT.md`, `docs/DECISIONS.md`, `docs/RULES.md`,
  `docs/DEBUGGING.md`, `docs/TODO.md`, `docs/API.md`.
- Published the **Ticket Booking Blueprint** artifact — visual walkthrough of
  the seat lifecycle, concurrency mechanism, waitlist flow, and build phases.
  Source lives at `docs/blueprint.html`, republishable to the same URL.
- `git init`, `.gitignore`, first commit. Not pushed — user pushes.

**Decided**

- Postgres-only seat locking, no Redis lock. See ADR-001.
- `OFFERED` as a first-class seat status, distinct from `HELD`. ADR-002.
- Lazy expiry as the correctness guarantee, sweeper as the UX guarantee. ADR-003.
- Email queued through BullMQ, never inline in the request. ADR-005.

**Open questions for the user**

- Resend vs Gmail SMTP for the email provider — Resend assumed; needs a domain
  or the shared sandbox sender. Confirm before Phase 4.
- Is a payment step wanted anywhere? The brief does not ask for one; assumed no.

**Next session starts with**

Phase 0 scaffolding — workspaces, TS config, Express + Vite skeletons. Nothing
needs a hosted account yet.
