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

---

## ADR-022 — Booking creation extracted to `bookings/write.ts`

**Accepted** · 2026-08-22

`writeBooking()`, `bookingSelect` and `toBookingView()` live in their own module
rather than in `bookings/service.ts`.

_Why:_ two paths now turn seats into a booking — normal checkout from a hold,
and accepting a waitlist offer. `bookings/service` must import
`waitlist/service` for `advanceWaitlist()`, so `waitlist/service` cannot import
`bookings/service` back without a cycle. Both import this instead.

_The point is not the cycle:_ it is that there is **one** implementation of
"turn seats into a booking". Two copies would drift on exactly the things that
matter — the price snapshot, the QR token, flipping the seats to BOOKED.

`writeBooking()` deliberately performs no validation. Its two callers verify
different preconditions — a live hold owned by the caller, versus a valid,
unexpired, correctly-addressed offer — and folding both into one function would
produce a parameter that means "which kind of check to run".

---

## ADR-023 — Queue position is derived, never stored

**Accepted** · 2026-08-22

A customer's place in line is computed as
`count(WAITING entries with an earlier joinedAt) + 1`.

_Alternative:_ a `position` column maintained on write.

_Why not:_ every departure from the middle of a queue would have to renumber
everyone behind, in a transaction, or the numbers silently rot. `joinedAt`
already totally orders the queue and is the same column `advanceWaitlist()`
sorts by — so the number a customer sees and the order the server actually uses
cannot disagree.

_Cost:_ one extra count per entry when listing. Irrelevant at this scale, and
the index on `(showId, categoryId, status, joinedAt)` already covers it.

---

## ADR-024 — Broadcasts carry `{ id, status }` only; ownership is reconciled client-side

**Accepted** · 2026-08-22

`seat:update` sends `{ id, status }` per seat. It does **not** carry
`heldByMe` or `holdExpiresAt`.

_Why:_ a broadcast is one payload delivered to many viewers, and both of those
fields answer "is this MINE" — a different answer per person. Including them
would mean either emitting a separate payload per socket, or leaking one
customer's countdown to everyone watching. `heldByUserId` was never a candidate;
rule 8 forbids it leaving the server at all.

_How ownership survives:_ the REST response is the source of truth for
`heldByMe`, and `useLiveSeats` re-applies it — an incoming update keeps
`heldByMe` while the seat is still `HELD` and drops it, with the countdown, the
moment the seat moves to anything else.

_Tested:_ the realtime suite asserts the broadcast's keys are exactly
`['id', 'status']`, so a future field cannot be added by accident.

---

## ADR-025 — No authentication on the socket connection

**Accepted** · 2026-08-22

Socket.IO accepts connections without a token. Rooms are keyed `show:{id}` and
any client may join any of them, capped at ten rooms per socket.

_Why:_ everything this layer emits is already served by
`GET /shows/:id/seats` without a token, and after ADR-024 the payload contains
nothing viewer-specific. A handshake we never read from would be security
theatre — the protection that matters is that there is nothing private in the
payload to begin with.

_What would change this:_ the moment a broadcast carries anything per-customer —
an offer notification, a personal countdown — this needs a JWT handshake and
per-user rooms. Reconsider before adding any such event.

_The cap exists_ because one socket has no business watching hundreds of shows,
and an unbounded join loop is a cheap way to consume server memory.

---

## ADR-026 — Revenue is summed from `priceAtBooking`, never the category price

**Accepted** · 2026-08-22

`GET /organiser/events/:id/summary` sums `BookingSeat.priceAtBooking` for
bookings whose status is `CONFIRMED`.

_Why not the category's current price:_ the two are different numbers the
moment an organiser re-prices anything, and only one of them is what the
customer paid. A dashboard that recalculated from the current price would
rewrite last month's revenue every time a price changed. Tested directly: the
suite re-prices Premium to 999 and asserts the reported revenue does not move.

_Why cancelled bookings are excluded by booking **status**, not by
`releasedAt`:_ status is the authoritative record of whether the money was
kept. `releasedAt` exists to free the seat, which is a related but separate
fact — and the `BookingSeat` row deliberately survives cancellation, so
filtering on row existence would count cancelled sales as revenue.

_Aggregated in JS, not SQL:_ Prisma cannot group by a relation's column, and an
event has hundreds of seats rather than millions. The ceiling is named in the
code; a raw `GROUP BY` is the upgrade if a venue ever gets large enough to
notice.

---

## ADR-027 — psycopg3, never asyncpg

**Accepted** · 2026-08-23

The database driver is `psycopg3` with `prepare_threshold=None`.

_Why not asyncpg:_ it is the obvious default for async Python, and it is wrong
on this infrastructure. Supabase's transaction pooler is pgbouncer, which cannot
carry a prepared statement across pooled connections — the statement is prepared
on one backend and executed on another that has never heard of it.
[supabase/supabase#39227](https://github.com/supabase/supabase/issues/39227) is
open and documents asyncpg leaking prepared statements through that pooler
**even with `statement_cache_size=0` set**, starting at roughly 100 concurrent
requests. That is precisely the shape of this project's graded test.

_How it was decided:_ spiked before a line of application code existed. A
throwaway script raced 20, then 100, then 250 concurrent contenders for a single
seat row through the real pooler. Every run: one winner, zero errors. asyncpg was
not adopted on the strength of that evidence rather than on preference.

_Honest cost:_ the issue reporter's own aside — "it doesn't happen with apps I
build using drizzle and typescript" — is true. The Python port introduces a class
of pooler risk the Node stack did not have. psycopg3 avoids it; the risk is
documented rather than hidden.

_Also required:_ `?pgbouncer=true` must be stripped from the connection string.
It was a Prisma-only flag, and psycopg forwards unknown query parameters to the
server, which rejects them. `to_sqlalchemy_url()` removes it, so an existing
`.env` keeps working.

---

## ADR-028 — The database schema was not renamed during the port

**Accepted** · 2026-08-23

Tables stay quoted PascalCase (`"ShowSeat"`), columns stay camelCase
(`"holdExpiresAt"`), enums stay native Postgres types. SQLAlchemy models map onto
them with an explicit name per attribute.

_Alternative:_ rename everything to snake_case, which is idiomatic Python and
would have removed ~40 explicit `mapped_column(...)` names.

_Why not:_ the port's entire value was provable equivalence — the existing tests
were the specification, and a failure had to mean "port bug" and nothing else. A
simultaneous schema rename makes every failure ambiguous. It would also have
invalidated the hand-written partial unique index and the three existing Prisma
migrations, and the API still has to emit camelCase JSON regardless, because the
React app is not part of the port.

_Consequence:_ one explicit column name per attribute. Mechanical, paid once,
and it made the diff reviewable.

---

## ADR-029 — ARQ replaces BullMQ; the sweeper still does not use either

**Accepted** · 2026-08-23

BullMQ is Node-only. The email queue is now ARQ: same shape — Redis-backed,
retries with exponential backoff, a separate worker process — different library.

_Why ARQ over Celery:_ Celery is sync-first and chattier with Redis, and Upstash
bills per command against a 500,000/month free allowance. ARQ is async-native,
which matches FastAPI, and small enough to read in one sitting.

_What did **not** change:_ the sweeper still runs as an interval loop against
Postgres, not as a queued job — ADR-018's arithmetic is unchanged by the language.
An idle Redis-polling worker costs ~518,000 commands a month on its own.

_Also unchanged:_ both job processors re-read their subject rather than trusting
a payload serialised minutes ago. By the time a retry runs, the booking may be
cancelled or the offer already passed on, and emailing a dead link is worse than
emailing nothing.

---

## ADR-030 — Tests run against a container, and refuse to run against production

**Accepted** · 2026-08-23

`config.active_database_url()` requires `DATABASE_URL_TEST` under
`NODE_ENV=test` and **will not fall back** to `DATABASE_URL`. `docker-compose.yml`
provides that database on port 5433, with its data on tmpfs.

_Why it exists:_ before the port, `npm test` wrote into the database serving the
live site. That was a standing hazard on the backlog for weeks. Building config
from scratch made it free to fix properly rather than retrofit.

_Why a container rather than a second Supabase project:_ no network latency, no
free-tier quota, and `docker compose down -v` guarantees a clean slate. The
suite went from ~9s of network round trips to ~9s total for 120 tests.

_Extended to Redis:_ `enqueue_email()` returns early under test for the same
reason. `REDIS_URL` points at the live Upstash instance, and a suite that
enqueues thousands of jobs there is the same mistake in a different system. It
also made the booking tests eight times slower.

_Consequence:_ a fresh clone must start the container before `npm test`. The
error names the fix rather than failing obscurely.

---

## ADR-031 — Stage layout is stored venue geometry, not a render-time projection

**Accepted** · 2026-08-24

`Venue.stageLayout` decides how the venue builder generates coordinates. A
centre-stage venue's seats are written with radial `posX`/`posY` at build time.

_Alternative, and an earlier draft of this design:_ layout as a per-event
projection, computing radial positions at render time so one hall could be
staged both ways.

_Why not:_ it solved a problem nobody has. A hall built in the round **is** in
the round. Storing the geometry means the seat map renderer needs no special
case at all — it already draws whatever coordinates it is given — and the two
layouts differ only in the stage marker.

_Consequence:_ a venue cannot be re-staged after its seats exist. Build a second
venue instead. That is the honest model: re-staging a real room means moving real
chairs.

---

## ADR-032 — Venue double-booking is prevented by a partial exclusion constraint

**Accepted** · 2026-08-24

Two layers. `assert_venue_free()` inside the show-creation transaction locks the
venue's scheduled shows and produces a message naming the clash. Underneath, a
Postgres GiST exclusion constraint on `("venueId", tsrange("startsAt",
"occupiesUntil"))`, partial on `status = 'SCHEDULED'`.

_Why the occupied window is not the show:_ the room has to empty, be cleaned and
be reset. `occupiesUntil = startsAt + duration + venue.turnaroundMinutes`, with
turnaround on the venue because a stadium needs longer than a screening room.

_Why `venueId` is denormalised onto `Show`:_ an exclusion constraint spans one
table. Safe because `Event.venueId` is already immutable — moving an event would
orphan every `ShowSeat` generated against the old venue's seats. The same trade
`priceAtBooking` makes.

_Why partial on status:_ cancelling a show frees its slot automatically, with no
cleanup code. House style, shared with `BookingSeat_showSeatId_live_key` — guard
the live rows, let the dead ones stay for history.

_`tsrange`, not `tstzrange`:_ the columns are `TIMESTAMP(3)` **without** time
zone, and the range type has to match the column type or the constraint will not
build.

_Both layers earn their place._ In the common case the app-level check wins and
returns a message naming the clashing show. In a genuine race for an empty slot
the row lock holds nothing — there are no rows yet to lock — and the constraint
is the only thing deciding a winner. It is not belt-and-braces; drop it and
concurrent organisers double-book.

_Cost:_ SQLAlchemy cannot express it, so it is hand-written and invisible to the
models. Recorded in `docs/DEBUGGING.md` as drift a future autogenerate will try
to drop.

---

## ADR-033 — Holds expire on two clocks

**Accepted** · 2026-08-24

Abandonment gives the full `HOLD_TTL_SECONDS` (300). An explicit back or cancel
shortens the hold to `RELEASE_GRACE_SECONDS` (15) rather than deleting it.

_Why not delete:_ keeping the owner lets `extend_hold()` restore the full TTL if
the customer returns, so a mis-clicked Back is recoverable rather than a lost
seat. Deleting makes that impossible.

_Why not zero:_ bouncing back and forward should not cost somebody their seats to
a faster customer.

_Why this needed no new mechanism:_ `effective_status()` already treats a lapsed
lease as free, so the seat becomes bookable at exactly fifteen seconds without
the sweeper being involved at all. One number changed.

_The delayed broadcast re-reads rather than assuming._ Releasing schedules a
`seat:update` for the moment the grace window ends, and that callback re-reads
the seats' effective status instead of shipping a fixed `AVAILABLE`. A customer
who extends inside the window would otherwise have every viewer told their seat
was free — a lie no sweeper tick would correct until the real TTL. Same reason
the sweeper re-verifies at fire time.
