# Feature backlog — differentiation beyond the brief

The brief is complete and deployed. Everything here is optional work aimed at
one question: **what does this do that BookMyShow, Ticketmaster and DICE do
not?**

Each entry states honestly whether it is genuinely novel, and what the evidence
is. Ideas that turned out to be already shipped are kept at the bottom rather
than deleted — knowing what exists is the point of the exercise.

---

## Prior art — what the real platforms already do

Checked before proposing anything, because "unique" claims are cheap.

| Platform         | Ships                                                                                                                                                                                                                                                                                                         |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **DICE**         | Wait List where fans **return tickets that resell to the next person in the queue**; limited ticket transfer between accounts; QR codes that only activate on the day of the event, to kill screenshot resale ([source](https://dicefm.zendesk.com/hc/en-gb/articles/19958073128849-The-wait-list-explained)) |
| **Ticketmaster** | SafeTix rotating barcodes that change every few minutes so a screenshot is useless; Verified Fan ML bot-screening before sale; dynamic and "Platinum" market pricing; **manual** seat upgrades via box-office staff ([source](https://www.ticketmaster.com/safetix))                                          |
| **BookMyShow**   | Standard seat map booking. **Does not** show which seats are wheelchair accessible, or whether a venue has ramps — the subject of a public petition with 1,800+ signatures ([source](https://www.change.org/p/bookmyshow-bookmyshow-display-wheelchair-accessible-information-on-your-website-app))           |

**Three ideas died on contact with this research:**

- ~~Transfer with visible revocation~~ — SafeTix's rotating barcode already
  solves screenshot fraud more thoroughly.
- ~~Conditional cancellation ("only cancel if someone takes it")~~ — DICE's Wait
  List is exactly this.
- ~~Dead-seat heatmap as _novel_~~ — venue analytics tools have done this for
  years. Still worth building here, but as a good feature, not a unique one.

---

## The pure-unique one

### 1. Hesitation Index — seat quality from abandoned holds ⭐

**Nobody has this, because nobody keeps the data.**

This system models a hold as a first-class row with a TTL. Every time somebody
selects a seat, thinks, and walks away, that is recorded. No mainstream platform
surfaces or uses abandoned-hold telemetry — most treat an expired hold as
garbage to sweep away.

It is a genuinely different signal from cancellation:

| Signal              | Means                            | When          |
| ------------------- | -------------------------------- | ------------- |
| Cancellation rate   | Regret **after** paying          | Post-purchase |
| **Hesitation rate** | Rejected **while** looking at it | Pre-purchase  |

A seat that is repeatedly held and abandoned is one people consider and turn
down — obstructed view, beside a speaker stack, next to the toilets. The venue
knows; the platform never asks.

Two products fall out of one signal:

- **For customers:** "this seat is passed over 4× more often than others in its
  row" — an honest warning no platform gives.
- **For the waitlist:** per-seat freeing likelihood rather than per-category.
  A habitually-abandoned seat that is held right now is _likely to free up_,
  which makes the odds estimate in #5 far sharper.

**Needs:** a `SeatEvent` table (or a counter pair on `ShowSeat`), written where
the sweeper and the release path already run. No new infrastructure.

---

## The rest, ordered by build sequence

### 2. Cancellation-rate seat quality

The other half of the quality signal, and the cheaper half — the data is already
in `Booking` and `BookingSeat`, entirely unread. Ships as a warning badge on the
seat map and a column in the organiser dashboard. **Roughly an afternoon**, and
it makes the app feel smarter immediately.

### 3. Preference-aware waitlist with honest odds

"2 seats together, rows A–F, under ₹500" instead of "notify me if anything
frees". A single seat skips you without costing your place in line.

Plus a real probability from that show's own history: _"3 ahead of you, ~40%
chance."_ Platforms don't tell you your odds because false hope converts better.

Turns `advanceWaitlist()` from a queue pop into constraint matching, which needs
care: an offer that skips people must still be provably fair. **Extends the
existing FIFO waitlist rather than replacing it.**

### 4. Seat-swap matchmaking

Two bookings each ended up split 2+1 and 1+2. The system finds the
mutually-improving trade, both parties confirm, seats exchange atomically and
both QRs re-issue.

**I could find no platform that does this.** Needs a matching pass over bookings
and an atomic multi-booking mutation — the same locking discipline as holds,
applied across two owners.

### 5. Automatic pre-show upgrades

Venues have "papered the house" manually for a century: when a show is
under-sold, move early buyers into the good empty seats so the room looks full
and those buyers feel rewarded.

Ticketmaster supports seat upgrades **only as a manual box-office action**.
Automating it — opt-in, consent-based, running on the existing sweeper, with QR
re-issue — is not shipped anywhere found.

### 6. Shared-hold group booking

Not "one person buys four seats" — four people, four browsers, one shared clock,
atomic commit. If one drops, all four release rather than leaving three friends
in and one out.

The concurrency is real: the locked hold transaction extended to N parties with
a shared deadline. Weaker novelty claim than the others (group carts exist in
other domains), strongest engineering claim.

### 7. Accessibility-aware seating

The one with **documented, citable demand**: a public petition against
BookMyShow for not showing which seats are wheelchair accessible, and it still
depends on venues supplying the data.

- Accessibility attributes on `Seat` — wheelchair space, companion seat, step-free
- Wheelchair space + companion seat held and booked **atomically**, never
  separable by another booking or by the waitlist
- Filter the map to accessible seats

Ticketmaster does sell accessible seating, so this is not unique — but it is
badly served, legally required in several jurisdictions, and the _atomic
companion pairing_ is the part that is genuinely missing.

### 8. Dead-seat heatmap (organiser)

Which seats never sell, across every show, so pricing follows real demand rather
than the architect's section lines. **Not novel** — venue analytics tools do
this — but it is a strong organiser feature and it composes with #1 and #2 into
one coherent "seat intelligence" story.

---

## Build order and reasoning

| #   | Feature                    | Novel?      | Effort    | Why here                                |
| --- | -------------------------- | ----------- | --------- | --------------------------------------- |
| 2   | Cancellation-rate quality  | Partly      | ~½ day    | Data already exists; instant payoff     |
| 1   | **Hesitation Index**       | **Yes**     | ~1 day    | The headline. Needs event capture first |
| 8   | Dead-seat heatmap          | No          | ~½ day    | Completes the seat-intelligence trio    |
| 3   | Preference waitlist + odds | Yes         | ~2 days   | Builds on 1 and 2 for the odds model    |
| 4   | Seat-swap matchmaking      | **Yes**     | ~2 days   | Hardest matching logic                  |
| 5   | Pre-show upgrades          | Yes         | ~1 day    | Reuses the sweeper                      |
| 7   | Accessibility seating      | Underserved | ~1 day    | Schema change; do before 6              |
| 6   | Shared-hold group booking  | Partly      | ~2–3 days | Hardest concurrency; last               |

Items 1, 2 and 8 form one coherent theme — **seat intelligence from data every
platform already has and none of them reads.** That is a stronger story than
eight separate features, and it is the honest differentiator.

---

## Prerequisite before any of this

**Tests and production share one Supabase project.** Running `npm test` writes
into the database serving the live site. A second free Supabase project and a
`DATABASE_URL_TEST` fixes it. Do this first — every feature below adds tests.
