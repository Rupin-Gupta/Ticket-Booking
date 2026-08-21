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

### Prisma migrate hangs or errors against Neon

**Symptom:** `prisma migrate dev` hangs, or `advisory lock` / prepared-statement
errors.
**Cause:** running migrations through the pooled connection string.
**Fix:** `DATABASE_URL` = pooled (`-pooler` in the host) for the app,
`DIRECT_URL` = unpooled for migrations. Both in `schema.prisma`:

```prisma
datasource db {
  provider  = "postgresql"
  url       = env("DATABASE_URL")
  directUrl = env("DIRECT_URL")
}
```

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
