# System Design

## Seat hold and TTL

A physical `Seat` belongs to a venue and carries no status — a chair does not
know whether it is sold. At show-creation time `instantiateShowSeats()`
generates one `ShowSeat` row per seat per show, inside the same transaction that
creates the show. That row carries `status`, `heldByUserId`, `holdExpiresAt` and
`offerExpiresAt`, and it is what every hold, booking and offer locks.

Expiry works at two levels, and only one of them is the guarantee.

**Lazy expiry is the correctness mechanism.** Every read and every mutation
passes rows through `effectiveStatus()`, which treats `HELD` past
`holdExpiresAt` as `AVAILABLE`. A seat is therefore bookable the instant its
lease lapses even if every background job is dead. No abandoned checkout can
lock a seat permanently.

**The sweeper is the visibility mechanism.** A ten-second interval flips expired
rows and broadcasts the change, so other people's screens stop showing a seat as
grey. It is a plain `setInterval` running two indexed `UPDATE`s, not a queued
job: an idle ARQ worker's blocking poll costs roughly 518,000 Redis commands
a month against Upstash's 500,000 free-tier allowance, so a queue here would
exhaust the tier in about three days — silently. Redis is kept for the email
queue and the Socket.IO adapter, where it earns its place.

## Concurrency

The bug being defended against is check-then-write: two requests both read
`AVAILABLE`, both write `HELD`, the second silently wins, and two customers own
one seat with nothing in the logs.

`POST /shows/:id/holds` runs one transaction that opens with

```sql
SELECT … FROM "ShowSeat" ss JOIN "Seat" s …
WHERE ss.id = ANY($1) AND ss."showId" = $2
ORDER BY ss.id
FOR UPDATE OF ss
```

The second contender blocks at `FOR UPDATE` until the first commits, then reads
`HELD` and receives a clean `409`.

Three details are load-bearing. `ORDER BY ss.id` prevents the deadlock where two
customers request the same pair of seats in opposite orders — Postgres resolves
a deadlock by killing a transaction, turning a clean conflict into a 500.
`FOR UPDATE OF ss` locks only `ShowSeat`, not the joined `Seat`, which would
serialise unrelated shows in the same venue. And the query both locks and reads,
so the lock is held for two round trips rather than four; with four, twenty
contenders exceeded SQLAlchemy's transaction timeout and seven of twenty returned
500 instead of 409. Holds are all-or-nothing: a partial hold is worse UX than a
clean rejection and leaks seats when the cart is abandoned.

Defence in depth sits underneath: `@@unique([showId, seatId])`, a partial unique
index guaranteeing at most one _live_ `BookingSeat` per seat, `FOR UPDATE SKIP
LOCKED` on the waitlist pick, and rate limits plus per-customer hold caps — a
lock stops a race, not one script calmly holding the venue.

A test fires twenty parallel holds at one seat over real HTTP against real
Postgres and asserts exactly one `201`, nineteen `409`s, and one `HELD` row.

## Waitlist auto-assignment

`advanceWaitlist(tx, showSeatId)` is the **only** implementation of "a seat
became free, find the next customer". Cancellation calls it; offer expiry calls
the same function. Two copies drift on precisely the clauses that matter, and
the bug then appears only on the rarer path.

It locks the freed seat, then selects the next entry:

```sql
… WHERE "showId" = $1 AND "categoryId" = $2 AND status = 'WAITING'
ORDER BY "joinedAt" ASC LIMIT 1
FOR UPDATE SKIP LOCKED
```

`ORDER BY joinedAt` is the FIFO promise. `SKIP LOCKED` means a concurrent
advance steps over a row already being offered rather than blocking and handing
one customer two offers. With nobody waiting, the seat returns to general sale.

## Time-limited offers

An offered seat becomes `OFFERED`, not `HELD`. The two expire differently — an
expired hold returns to `AVAILABLE`, an expired offer must walk the queue — and
one status for both would force the sweeper to guess which it found.

The entry receives `offerExpiresAt` and an `offerToken` of 32 CSPRNG bytes,
emailed as a link. Accepting checks five things: the token resolves, the entry
is still `OFFERED`, it has not expired, the seat is still `OFFERED`, and **the
caller is the customer it was offered to** — the token arrives by email, and
email gets forwarded. Success clears the token; it is single use.

Expiry marks the entry `EXPIRED` and calls `advanceWaitlist()` again rather than
freeing the seat. That is the loop: an ignored offer walks down the queue by
itself, reaching general sale only when the line is genuinely empty. A test
drives one seat through three customers to general sale purely by letting each
offer lapse.

Emails are queued and sent after commit — a confirmed booking must never depend
on a mail provider, and an email about a seat a rollback took back is worse than
a late one.
