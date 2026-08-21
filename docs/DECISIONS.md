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
instance. `pg_cron` is not available on Neon's free tier and puts business logic
somewhere no test can reach it.

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

**Accepted** · 2026-08-22

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
