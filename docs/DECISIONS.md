# Decision Log

One entry per non-obvious choice: what was decided, what it beat, and why.
Append; never rewrite history. If a decision is reversed, add a new entry that
supersedes the old one and mark the old one.

Format: **ADR-nnn — Title** · Status · Date

---

## ADR-001 — Postgres row locks for seat state, not Redis

**Accepted** · 2026-08-22

Seat holds are enforced with `SELECT ... FOR UPDATE` inside a Prisma
transaction. Redis carries job queues and the Socket.IO adapter only.

_Alternative:_ a Redis `SET NX PX` lock per seat, which is the common
"distributed lock" answer and is faster.

_Why not:_ it creates two sources of truth for one fact. Redis eviction, a
network partition, or a lock TTL that expires mid-transaction all produce a seat
that Redis says is free and Postgres says is held. The row lock is already
transactional, already durable, already rolls back with the transaction, and
costs one extra query. Postgres does not need help being the authority on its
own rows.

_Cost:_ holds serialise per seat under contention. Irrelevant at this scale —
contention on a single seat is a handful of requests, not thousands.

---

## ADR-002 — `OFFERED` is its own seat status

**Accepted** · 2026-08-22

`SeatStatus` is `AVAILABLE | HELD | OFFERED | BOOKED`.

_Alternative:_ reuse `HELD` with `heldByUserId` set to the waitlisted customer.

_Why not:_ the two states expire differently. An expired `HELD` seat becomes
`AVAILABLE`. An expired `OFFERED` seat must walk the waitlist queue. Sharing one
status forces the sweeper to infer which kind of expiry it is looking at from a
nullable column — exactly the ambiguity that produces a seat quietly dropping
out of the waitlist flow.

---

## ADR-003 — Lazy expiry is the guarantee; the sweeper is the polish

**Accepted** · 2026-08-22

Every transaction that reads a seat for mutation treats an expired
`holdExpiresAt` / `offerExpiresAt` as free. A ~10s BullMQ repeatable job also
sweeps expired rows and broadcasts.

_Alternative:_ a per-seat `setTimeout` scheduled at hold time, or a Postgres
`pg_cron` job.

_Why not:_ `setTimeout` dies with the process and does not exist on the second
instance. `pg_cron` **is** available on Supabase, but expiring an offer means
calling `advanceWaitlist()` — TypeScript, with the email enqueue attached — so a
SQL-only job would have to reimplement half of it in PL/pgSQL, in a place no
test can reach and the code review never sees.

_What this buys:_ if every background job is dead, the system is still correct —
no seat is ever permanently locked by an abandoned checkout. The sweeper only
makes the truth visible to other viewers sooner.

---

## ADR-004 — One `advanceWaitlist()` for both cancellation and offer expiry

**Accepted** · 2026-08-22

Booking cancellation and offer-expiry both call the same function.

_Alternative:_ separate `onCancellation()` and `onOfferExpired()` handlers, each
doing its own "find the next person" logic.

_Why not:_ they are the same operation — a seat became free, find the next in
line. Two copies drift: a fix to the FIFO ordering or the `SKIP LOCKED` clause
lands in one and not the other, and the bug only shows up on the rarer path.

---

## ADR-005 — Email is queued, never sent inline

**Accepted** · 2026-08-22

The booking transaction commits and the request returns; a BullMQ worker renders
the QR and sends the mail with retry and backoff.

_Alternative:_ `await mailer.send(...)` in the booking handler.

_Why not:_ it makes an external SMTP provider a hard dependency of confirming a
booking. A provider timeout would either fail a booking the database already
recorded, or hang the request for thirty seconds. The customer's seat is
reserved either way; the email is allowed to be a second late.

---

## ADR-006 — QR encodes a verification URL, not booking data

**Accepted** · 2026-08-22

The QR contains `{WEB_URL}/verify/{qrToken}` where `qrToken` is 32 random bytes.

_Alternative:_ encode the booking JSON, or encode the human-readable reference.

_Why not:_ raw JSON in a QR is forgeable by anyone with a QR generator — a
scanner reading it has no way to tell a real ticket from a printed one. The
human reference (`BK-7F3K2`) is short and semi-guessable. A random token forces
verification to go through the server, which is the only party that knows what
is real.

---

## ADR-007 — Argon2id over bcrypt

**Accepted** · 2026-08-22

Passwords hashed with the `argon2` package, Argon2id variant.

_Alternative:_ bcrypt, which is the more common default.

_Why:_ OWASP's Password Storage Cheat Sheet lists Argon2id first and bcrypt as
the legacy fallback. bcrypt also silently truncates input past 72 bytes, which
means a long passphrase is quietly weaker than it looks. If bcrypt is ever
substituted, the 72-byte cap must be enforced explicitly rather than relied on.

---

## ADR-008 — Neon over Render Postgres and Supabase

**Superseded by [ADR-013](#adr-013--supabase-as-the-database) · 2026-08-22** —
kept for the reasoning about Render Postgres, which still stands.

Database is Neon free tier, with `DATABASE_URL` (pooled) for the app and
`DIRECT_URL` (unpooled) for migrations.

_Why:_ Render's free Postgres expires after 30 days, which would kill a hosted
submission mid-evaluation. Neon's free tier has no expiry. Supabase is fine too
but bundles auth and storage this project does not use.

_Trap it introduces:_ running `prisma migrate` against the pooled URL fails or
hangs, because migrations take advisory locks a pooler mangles. Documented in
`docs/DEBUGGING.md` before it costs an afternoon.

---

## ADR-009 — All-or-nothing seat holds

**Accepted** · 2026-08-22

A hold request for `[A, B, C]` either holds all three or holds none, with the
lock set sorted by id.

_Alternative:_ hold whichever seats are free and report the rest as taken.

_Why not:_ partial success is worse UX than a clean rejection — the customer
wanted three seats together — and it leaks seats when they abandon the
half-filled cart. Sorting the lock set by id also removes the deadlock where two
customers request the same pair in opposite orders.

---

## ADR-010 — Run TypeScript directly with `tsx`, no build step

**Accepted** · 2026-08-22

`apps/api` runs `tsx src/server.ts` in development and in production. There is
no `tsc` emit, no `dist/`. Type safety comes from `npm run typecheck`, which is
a separate gate rather than a prerequisite for starting the server.

_Alternative:_ `tsc` build to `dist/`, run the compiled JavaScript.

_Why not, for now:_ a build step is one more thing that behaves differently on
Render than it does locally — path resolution, missing `.js` extensions,
`prisma generate` ordering. Skipping it removes a whole class of deploy failure
for a startup cost measured in tens of milliseconds.

_Ceiling:_ `tsx` becomes a production dependency, and startup does compile work
on every cold boot. If Render cold starts get slow enough to matter, add
`tsc --outDir dist` and switch `start` to `node dist/server.js` — nothing in the
source has to change.

---

## ADR-011 — No ESLint; TypeScript strict plus Prettier instead

**Accepted** · 2026-08-22

`tsconfig.base.json` runs strict with `noUncheckedIndexedAccess`,
`exactOptionalPropertyTypes`, `noImplicitOverride`, and
`noFallthroughCasesInSwitch`. Prettier handles formatting. No ESLint, no
plugins, no flat-config file.

_Alternative:_ the usual stack of `eslint`, `typescript-eslint`,
`eslint-config-prettier` and a React plugin.

_Why not:_ four dependencies and a config that needs maintaining, to catch a
class of problem the compiler already catches at this project's size. The strict
flags above found two real bugs during Phase 0 scaffolding on their own.

_Add it when:_ more than one person is writing code here, or a specific rule is
wanted that types cannot express — exhaustive `useEffect` dependencies being the
most likely reason.

---

## ADR-012 — `Show.bookings` added to the schema in `CLAUDE.md`

**Accepted** · 2026-08-22

`Booking` has `show Show @relation(...)`, but `Show` had no `bookings Booking[]`
back-relation. Prisma refuses to generate a client without both sides.

_Why it is logged:_ `CLAUDE.md` calls its schema authoritative, so a silent
divergence between it and `prisma/schema.prisma` is exactly the kind of drift
that costs an hour later. `Booking.@@index([customerId, createdAt])` was added
at the same time — booking history is queried by customer, newest first.

---

## ADR-013 — Supabase as the database

**Accepted** · 2026-08-22 · supersedes [ADR-008](#adr-008--neon-over-render-postgres-and-supabase)

Database is Supabase free tier. `DATABASE_URL` is the **transaction** pooler
(port 6543, `?pgbouncer=true`) for the app; `DIRECT_URL` is the **session**
pooler (port 5432, same host) for `prisma migrate`.

_Chosen by:_ the repo owner, after the trade-off below was put on the table.

_What it costs:_ Supabase pauses a free project after **7 days with no database
activity**, and bringing it back is a manual restore from the dashboard. Waking
takes ~30 seconds after that. For a project that gets submitted and then graded
whenever the evaluator gets to it, an idle week is realistic.

_Mitigation, and it is not optional:_ `/health` now round-trips a `SELECT 1`, so
any daily ping counts as database activity and resets the 7-day timer. A GitHub
Actions cron hitting the deployed `/health` once a day is enough. This lands in
Phase 8 with the deploy, and it is the difference between a working submission
and a dead one. Note that dashboard visits do **not** count — only queries do.

_What it buys:_ Supabase's own dashboard and SQL editor are genuinely better for
demonstrating the schema and inspecting seat state live during a walkthrough,
and the project is one place rather than a database plus a separate console.

_Why the third connection string is banned:_ Supabase also offers a direct
connection on `db.<ref>.supabase.co`. It is IPv6-only without the paid IPv4
add-on, so it works from a laptop and fails from Render. Neither `DATABASE_URL`
nor `DIRECT_URL` may point at it.

_Why the transaction pooler is safe for seat locking:_ transaction mode assigns
a connection for the lifetime of a transaction, so `BEGIN … SELECT … FOR UPDATE
… COMMIT` holds its lock exactly as it would on a direct connection. What
transaction mode discards is session state between transactions — which is why
migrations, whose advisory locks are session state, need the session pooler.

_Still true from ADR-008:_ Render's free Postgres expires after 30 days and is
not an option for this project.

---

## ADR-014 — Plain CSS custom properties, no Tailwind and no component library

**Accepted** · 2026-08-22

`apps/web/src/styles/tokens.css` holds every colour, size, duration and
z-index. Components are hand-written with a small stylesheet each. No Tailwind,
no shadcn, no MUI.

_Alternative:_ Tailwind, which the design-system tooling assumes by default.

_Why not:_ tokens in CSS custom properties give the one thing this project
actually needs — a seat that is `--seat-held` in both themes without a class
being recomputed — with zero build configuration and zero dependencies. Tailwind
would add a config file, a build step and a purge story to solve a problem four
components do not have.

_What it buys specifically:_ the seat map in Phase 3 renders hundreds of cells
whose colour is driven by data. A CSS variable per status is the natural fit;
mapping four statuses onto utility class strings is not.

_Add a library when:_ a date picker, a combobox or a modal is needed. Those are
genuinely hard to get right for accessibility, and hand-rolling them would be
the wrong kind of lazy.

---

## ADR-015 — Seat-status colours chosen in Phase 1, not Phase 3

**Accepted** · 2026-08-22

`--seat-free`, `--seat-held`, `--seat-offered` and `--seat-booked` are defined
in `tokens.css` now, with light and dark values, even though nothing renders a
seat until Phase 3.

_Why:_ they are the palette the product is recognised by, and they have to hold
contrast against the surfaces and the brand blue as one set. Picked later, in
isolation, they would either clash or force the rest of the palette to move.

_Constraint they must keep:_ status is never signalled by colour alone. Every
seat also carries a text label and a distinct shape state, because roughly one
man in twelve cannot reliably separate the teal from the amber.

---

## ADR-016 — `SeatCategory.sections` maps price bands to venue sections

**Accepted** · 2026-08-22

`SeatCategory` gains `sections String[]`, naming which venue sections that
price band covers. Validated on write: every section must exist in the event's
venue, and no two categories in one event may claim the same section.

_The gap it fills:_ seats belong to a **venue**, price categories belong to an
**event**. Nothing connected the two, so `instantiateShowSeats()` had no way to
decide what a seat costs. The schema in `CLAUDE.md` did not cover this.

_Alternatives:_ match `SeatCategory.name` to `Seat.section` by string equality —
brittle, and forces a venue's physical naming onto every event's price list.
Or pass the mapping in the show-creation request — repeats it for every show
and lets two shows of one event disagree.

_Why validate at category-write time rather than show-creation:_ a seat that
cannot be priced is discovered when someone tries to sell it. Catching it when
the category is defined means seat generation never meets a seat it cannot
price.

_Consequence:_ creating a show is refused outright while any section is
unpriced, rather than generating a partial seat map that would quietly sell
some seats and skip others.

---

## ADR-017 — Middleware arrays are spread, not passed as arrays

**Accepted** · 2026-08-22

Route definitions use `router.post('/x', ...adminOnly, handler)`, never
`router.post('/x', adminOnly, handler)`.

_Why it matters, and it is not style:_ passing the array selects an Express
type overload that stops inferring the final handler's parameters, so `req` and
`res` silently become `any`. That masked eight genuine type errors —
`req.params.id` really is `string | string[] | undefined`, and passing
`description: undefined` into a Prisma update really is rejected under
`exactOptionalPropertyTypes`. Spreading restored inference and surfaced all of
them.

_What it produced:_ `lib/http.ts` with `param()`, which validates a route
parameter instead of casting the union away, and `compact()`, which strips
`undefined` keys so a PATCH that omitted a field cannot blank the column.

_Rule going forward:_ an implicit `any` in a route handler is never cosmetic.
It is the compiler being switched off exactly where request data enters.

---

## ADR-018 — The sweeper is a `setInterval` on Postgres, not a BullMQ job

**Accepted** · 2026-08-22

Expired holds are released by a plain interval in the API process running one
indexed `UPDATE`. Redis and BullMQ stay, scoped to the email queue (Phase 4)
and the Socket.IO adapter (Phase 6).

_Why the change:_ Upstash's free tier meters **500,000 commands per month,
cumulative**. An idle BullMQ worker's blocking poll costs roughly 518,000 a
month on its own — the whole allowance, with zero jobs run — and a repeatable
job firing every ten seconds costs millions more. The free tier would be
exhausted in about three days, and the failure mode is silent: emails simply
stop.

_Why it is safe:_ the sweep is one idempotent statement whose `WHERE` clause is
its own guard, so several instances running it converge rather than conflict.
Correctness never depended on it anyway — `effectiveStatus()` treats an expired
lease as free on every read, so a seat is bookable the moment its clock lapses
even if the sweeper never runs. The sweep only makes that visible on other
people's screens sooner.

_Rule 4 still holds:_ it asks for "a scheduler **or** database-level expiry".
This is the scheduler; the lazy check is the database-level half.

_Ceiling:_ one interval per instance, so N instances do N redundant sweeps.
Harmless at any scale this project will see. If it ever matters, a Postgres
advisory lock around the sweep makes exactly one instance do the work.

---

## ADR-019 — The hold cap is checked outside the locking transaction

**Accepted** · 2026-08-22

`MAX_ACTIVE_HOLDS_PER_USER` is verified before `$transaction` opens, not inside
it.

_Why:_ every query inside a lock-holding transaction is time that every other
contender spends blocked. With the check inside, the hold path made four round
trips while holding row locks; against Supabase in another region that is over
a second of lock time each, and twenty contenders serialised past Prisma's 5s
transaction timeout. Seven of twenty requests returned 500 instead of a clean
409 — the safety property held, exactly one hold succeeded, but legitimate
contenders were being errored.

_What it costs:_ a narrow race in which a customer submitting two requests at
the same instant ends up holding one more show than the cap allows. That is an
abuse cap, not a correctness invariant — being off by one is not a
double-booked seat.

_What else came out of the same fix:_ the lock query now also reads the columns
it checks, so the transaction is two round trips rather than four, and
`maxWait` / `timeout` are set explicitly because Prisma's defaults assume
uncontended work.

**Never move a correctness check out of the transaction for speed.** The
`FOR UPDATE`, the status re-read and the write stay together permanently.

---

## ADR-020 — The seatbelt is a partial unique index, not `@unique`

**Accepted** · 2026-08-22 · supersedes the `showSeatId @unique` in ADR-001's schema

`BookingSeat.showSeatId` is no longer unique. Instead it carries a nullable
`releasedAt`, and a hand-written partial index enforces the real invariant:

```sql
CREATE UNIQUE INDEX "BookingSeat_showSeatId_live_key"
  ON "BookingSeat"("showSeatId") WHERE "releasedAt" IS NULL;
```

_The bug:_ a plain `@unique` meant a show-seat could appear in **one booking
ever**. The row survives cancellation on purpose — revenue history and the
cancellation email both need to know what was booked and at what price — so
the constraint kept occupying the seat forever and a cancelled seat could never
be sold again. Found by a test asserting a released seat goes back on sale.

_Why not just delete the row:_ that throws away the price paid, which is the
only record of what a booking was worth before an organiser re-priced the
category.

_Why not drop the constraint entirely:_ the `FOR UPDATE` transaction is the
primary guard, but a database-level guarantee that survives an application bug
is worth keeping. "At most one live claim per seat" is exactly that guarantee,
stated precisely instead of approximately.

_Cost:_ Prisma cannot express a partial unique index, so it is invisible to
`schema.prisma` and a future `migrate dev` may report it as drift and try to
drop it. Recorded in the schema file, the migration, and `docs/DEBUGGING.md`.
`ShowSeat.bookingSeat` also became `bookingSeats BookingSeat[]` — the relation
is one-to-many across time.

---

## ADR-021 — `MAIL_REDIRECT_TO` for development only

**Accepted** · 2026-08-22

Outside production, every email can be redirected to one address, with the
intended recipient preserved in the subject line.

_Why it exists:_ Resend's shared `onboarding@resend.dev` sender only delivers
to the address that owns the account. Without this the seeded demo customers
(`customer@ticket.dev` and friends) can never receive anything, so the QR
ticket — a graded deliverable — cannot be demonstrated without either verifying
a domain or hard-coding a personal address into the seed script.

_Why it is refused in production:_ silently redirecting a real customer's
ticket away from them is far worse than not sending it. The check is on
`NODE_ENV`, not on the variable being absent.

_Not a substitute for:_ verifying a domain before the app is genuinely public.
