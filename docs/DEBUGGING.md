# Debugging Log

Two halves: **traps** we expect and have already written the fix for, and
**incidents** — real bugs that cost real time, logged as
symptom → cause → fix so they are never re-debugged from scratch.

Log an incident whenever a bug takes more than five minutes.

---

## Debugging method

Follow `superpowers:systematic-debugging` for anything non-trivial:

1. Reproduce it deterministically before changing a single line.
2. Read the actual error and the actual stack. Do not pattern-match to a guess.
3. Form one hypothesis, and find the cheapest way to falsify it.
4. Fix the cause, not the symptom.
5. Leave a check behind that fails if it regresses.
6. Log it here.

Concurrency bugs specifically: if it only reproduces sometimes, that is data,
not noise. Add `Promise.all` parallelism and a loop until it reproduces every
time, _then_ debug it.

---

## Expected traps

These are known before they happen. Each one is a documented afternoon someone
else has already lost.

### The hosted app is dead a week after you last touched it

**Symptom:** the deployed URL returns database errors; Supabase dashboard shows
the project as paused.
**Cause:** Supabase pauses a free project after **7 days with no database
activity**. Visiting the dashboard does not count — only queries do.
**Fix:** restore it manually from the dashboard (~30s), then make sure the daily
keep-alive is actually running. `/health` round-trips a `SELECT 1` precisely so
a cron ping counts as activity. **Check this before submitting or demoing.**

### Prisma migrate hangs or errors against Supabase

**Symptom:** `prisma migrate dev` hangs, or errors about advisory locks or
prepared statements.
**Cause:** running migrations through the **transaction** pooler (port 6543).
Migrations take advisory locks, which are session state, and transaction mode
throws session state away between statements.
**Fix:** `DATABASE_URL` = transaction pooler `:6543?pgbouncer=true` for the app,
`DIRECT_URL` = session pooler `:5432` for migrations. Both in `schema.prisma`:

```prisma
datasource db {
  provider  = "postgresql"
  url       = env("DATABASE_URL")
  directUrl = env("DIRECT_URL")
}
```

### Works from the laptop, cannot connect from Render

**Symptom:** `P1001: Can't reach database server` on Render only; the exact same
connection string works locally.
**Cause:** using Supabase's third connection string, the direct one on
`db.<ref>.supabase.co`. It is IPv6-only without the paid IPv4 add-on.
**Fix:** neither `DATABASE_URL` nor `DIRECT_URL` may point at it. Both use the
`aws-0-<region>.pooler.supabase.com` host — 6543 for the app, 5432 for
migrations.

### Database password containing `@`, `#`, `/` or `?`

**Symptom:** connection refused, or the URL parses with a truncated host — a
password with `@` in it makes the parser treat the wrong `@` as the host
delimiter.
**Cause:** connection strings are URLs. Special characters in the password have
to be percent-encoded.
**Fix:** `@` → `%40`, `#` → `%23`, `/` → `%2F`, `?` → `%3F`. Encode only the
password, never the `@` that separates credentials from the host. `env.ts`
validates all three connection strings with `z.string().url()`, so a broken one
now fails loudly at boot instead of at first query.

### `prisma migrate` sits there forever with no output

**Symptom:** `npm run db:migrate` produces nothing and never returns. Looks
exactly like the pooler hang above, and is not.
**Cause:** `prisma migrate dev` prompts interactively for a migration name.
With no TTY the prompt is invisible and it waits forever.
**Also:** `npm run db:migrate -- --name init` from the repo root used to be
swallowed by the outer `npm run` before it reached Prisma. The root script now
ends in `--` so arguments forward properly.
**Fix:** always pass a name — `npm run db:migrate -- --name add_something`.
Before blaming the pooler, check whether the connection line printed: if it
says `Datasource "db": PostgreSQL database ... at ...:5432`, the connection
succeeded and something else is blocking.

### Emails only arrive for one address

**Symptom:** `validation_error — You can only send testing emails to your own
email address`.
**Cause:** Resend's shared `onboarding@resend.dev` sender only delivers to the
account owner until a domain is verified.
**Fix:** set `MAIL_REDIRECT_TO` to your own address for development (ADR-021),
or verify a domain and change `MAIL_FROM`. Note that the booking still succeeds
either way — the failure is confined to the queued job, which is the point.

### A migration silently drops the booking seatbelt

**Symptom:** after a `prisma migrate dev`, two live `BookingSeat` rows can
point at one `showSeatId`.
**Cause:** `BookingSeat_showSeatId_live_key` is a **partial** unique index.
Prisma cannot represent one, so it does not appear in `schema.prisma` and
migrate treats it as drift to be removed.
**Fix:** re-add it in the same migration:

```sql
CREATE UNIQUE INDEX "BookingSeat_showSeatId_live_key"
  ON "BookingSeat"("showSeatId") WHERE "releasedAt" IS NULL;
```

Check for it after any schema change: `\d "BookingSeat"` in psql, or query
`pg_indexes`.

### The sweeper logs `P1017: Server has closed the connection`

**Symptom:** occasional sweeper errors, then it recovers on its own.
**Cause:** Supabase's transaction pooler recycles idle connections, so a sweep
after a quiet period finds its socket closed.
**Fix:** already handled — the sweeper retries once, which is enough for Prisma
to reconnect. It also no longer fires a tick at import time, which used to log
"can't reach database server" on every boot before anything had connected.

### `prepared statement "s0" already exists`

**Symptom:** intermittent Prisma errors under any real concurrency, only in
production.
**Cause:** `?pgbouncer=true` missing from `DATABASE_URL`. The transaction pooler
cannot support prepared statements, and Prisma uses them by default.
**Fix:** add `?pgbouncer=true` to the pooled string. It is not optional.

### Concurrency test passes but the race is real

**Symptom:** 20 parallel holds, all pass, no 409s — and the test still goes
green.
**Cause:** the requests were not actually concurrent. Sequential `await` in a
loop, or SQLite/one connection in the test setup, serialises them for free.
**Fix:** fire with `Promise.allSettled`, run against real Postgres, and assert
the DB state (`exactly one HELD row`) rather than only the HTTP codes. A test
that only counts 201s can be satisfied by an accident.

### Deadlock on multi-seat holds

**Symptom:** intermittent `deadlock detected` under load, surfacing as a 500.
**Cause:** two transactions locking the same seats in opposite orders.
**Fix:** `ORDER BY id` in the `FOR UPDATE` query, always. Not optional.

### Socket updates work locally, drop in production

**Symptom:** one browser sees updates, another does not; works perfectly on
localhost.
**Cause:** more than one API instance, no Redis adapter — each process
broadcasts only to its own connected sockets.
**Fix:** `@socket.io/redis-adapter`, wired from the start.

### Seat map shows a seat as held after it expired

**Symptom:** a seat's `holdExpiresAt` is in the past but the grid still greys it.
**Cause:** the read path returns raw `status` without considering expiry, and
the sweeper has not run yet.
**Fix:** the seat map projects an _effective_ status — an expired lease renders
as available. Do not wait on the sweeper for what a read can compute.

### Waitlist offers two people the same seat

**Symptom:** two customers get an offer email for one seat; the second accept
fails confusingly.
**Cause:** two concurrent `advanceWaitlist()` calls both selected the same
`WaitlistEntry`.
**Fix:** `FOR UPDATE SKIP LOCKED` on the queue pick. The second caller skips the
locked row and takes the next one.

### Broadcast fires for a rolled-back transaction

**Symptom:** a seat flickers to held in every browser, then flips back.
**Cause:** `io.emit` called inside `$transaction`, before the commit.
**Fix:** collect the events during the transaction, emit after it resolves.

### `Decimal` arithmetic returns a string or loses precision

**Symptom:** revenue totals come out as `"1200"` concatenated, or off by cents.
**Cause:** Prisma `Decimal` is a `Decimal.js` instance, not a JS number.
**Fix:** use `.add()` / `.mul()` for arithmetic, `.toFixed(2)` at the boundary.
Never `Number(price) + Number(other)` on money.

### CORS blocks the deployed frontend

**Symptom:** works locally, browser blocks every request from the Vercel domain.
**Cause:** `cors()` origin allowlist does not include the deployed web URL, or
credentials are not enabled.
**Fix:** drive the allowlist from `WEB_URL` env, include the Vercel preview
pattern if previews are used.

### Render free-tier cold start looks like a hang

**Symptom:** the first request after idle takes ~50s.
**Cause:** free-tier instances spin down. Not a bug.
**Fix:** warm it before demoing; note it in the README.

### `npm audit` reports 3 high advisories on a clean install

**Symptom:** `npm install` ends with "3 high severity vulnerabilities";
`npm audit fix` does not clear them.
**Cause:** `deepmerge-ts` (stack exhaustion on recursive object graphs) reaches
us through `@prisma/config` → `prisma`, which is a **devDependency** — the CLI
that runs migrations, not anything in the request path. No fixed release exists
yet: prisma 7.9.1, the latest, is still inside the advisory range.
**Fix:** none available; do not `npm audit fix --force`, it downgrades Prisma
below the version the schema needs. Recheck at Phase 8 before submitting.

### Strict-mode errors that look like TypeScript being difficult

Both of these hit during Phase 0 and both were real:

**`Type 'undefined' cannot be used as an index type`** — a mapped type over an
object with optional properties keeps the `?`, so every indexed access carries a
stray `undefined`. Add `-?` to the mapped type: `[K in keyof T]-?: …`.

**`Argument of type '{ body: string | undefined }' is not assignable`** —
`exactOptionalPropertyTypes` means "absent" and "present but undefined" are
different. Build the object without the key rather than setting the key to
`undefined`.

---

## Incidents

_No incidents yet — nothing has run._

<!--
### YYYY-MM-DD — One-line title
**Symptom:** what was observed
**Cause:** what was actually wrong
**Fix:** what changed, with file:line
**Guard:** the test or assertion that now catches it
**Cost:** how long it took
-->

---

# Python port — traps met and fixed

Everything below cost real time during the 2026-08-23 TypeScript → Python port.
All are fixed; they are recorded because each would be rediscovered painfully.

### `ModuleNotFoundError: greenlet` from `create_async_engine`

**Symptom:** SQLAlchemy imports fine, then blows up the moment an async engine is
created.
**Cause:** SQLAlchemy 2.0 made greenlet an _optional_ extra.
**Fix:** depend on `sqlalchemy[asyncio]`, not `sqlalchemy`.

### A naive `isoformat()` moves every countdown by the viewer's UTC offset

**Symptom:** hold timers are hours out — but only for users outside UTC, so it
never reproduces for a developer in UTC.
**Cause:** the columns are `TIMESTAMP(3)` **without** time zone, so Python reads
naive datetimes. `datetime.isoformat()` on a naive value emits
`2026-08-23T10:00:00` with no zone, and `new Date(...)` in the browser reads that
as _local_ time.
**Fix:** every timestamp crossing the wire goes through `models.iso()`, which
appends the `Z` and the milliseconds exactly as JavaScript's `toISOString()`
does. Rule 17.

### Prices arrive with thirty trailing zeros

**Symptom:** `"450.000000000000000000000000000000"` in JSON.
**Cause:** the column is `Numeric(65, 30)`; `str(Decimal)` prints all of it.
**Fix:** `models.money()`. Note it uses `format(value, "f")` and **not**
`str(value.normalize())` — normalize renders whole numbers in exponent form, so
`Decimal("450.00")` becomes `4.5E+2`. Verified against `decimal.js`, which is
what `Prisma.Decimal` was, so the output is byte-identical to the old API.

### `Decimal("NaN")` returns 500 instead of 400

**Symptom:** a price of `"NaN"` produced an unhandled `InvalidOperation`.
**Cause:** `Decimal("NaN")` _parses_ happily; it is the subsequent `parsed < 0`
that raises rather than returning False.
**Fix:** check `is_finite()` **before** any comparison.

### `model_validate` says a field is required when it is right there

**Symptom:** `SeatOut` raised "Field required" for `posX` on an ORM object that
plainly has the value.
**Cause:** `from_attributes=True` looks up the _Python attribute_, which is
`pos_x`; the column and the JSON are both `posX`.
**Fix:** `Field(validation_alias="pos_x")`.

### Autogenerated migrations silently omit every composite constraint

**Symptom:** the baseline migration created all ten tables and none of the
`@@unique`s or indexes.
**Cause:** they were never declared on the models. Against the _live_ database
this is invisible — Prisma already created them — but a fresh test database ends
up materially weaker than production, so tests pass against a schema that is not
the real one.
**Fix:** `__table_args__` on all six affected models, then regenerate.

### A future autogenerate will try to drop the partial index

**Symptom:** `alembic revision --autogenerate` proposes dropping
`BookingSeat_showSeatId_live_key`.
**Cause:** it is a _partial_ unique index, which SQLAlchemy's declarative layer
cannot express, so autogenerate sees an object it does not know about.
**Fix:** it is written by hand in the baseline migration. If autogenerate
proposes removing it, that is drift — delete the proposal, not the index. Same
trap Prisma had; only the tool changed.

### Tests were writing to the production Redis queue

**Symptom:** the booking tests took 11.85s where every other file took under one.
**Cause:** `enqueue_email()` was reaching the live Upstash instance once per
booking. Slow, and it was enqueueing real jobs.
**Fix:** `enqueue_email()` returns early under `NODE_ENV=test`. 11.85s → 1.47s.

### `mv apps/api-py apps/api` nested instead of replacing

**Symptom:** `apps/api/api-py/` after the swap.
**Cause:** `git rm -r apps/api` removes _tracked_ files; the directory survived
because `.env` and `.env.render` were untracked, so `mv` moved the source
_into_ it.
**Fix:** flatten by hand. Worth knowing before doing this to a directory holding
untracked secrets — check `ls -a` first, not `git status`.

### A moved venv keeps absolute paths in its console scripts

**Symptom:** `.venv/bin/pytest` breaks after moving the project directory.
**Cause:** shebangs are absolute. `./.venv/bin/python -m pytest` still works,
which makes this easy to miss until CI uses the console script.
**Fix:** recreate the venv after any move. It costs a minute.
