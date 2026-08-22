# API Reference

Base URL: `{API_URL}/api/v1`. JSON in, JSON out. Auth is
`Authorization: Bearer <accessToken>`.

> **Planned surface.** Endpoints are marked ✅ once implemented and verified.
> Request/response bodies are filled in as each ships — this file is the
> contract, so update it in the same commit as the route.

## Conventions

**Error shape** — every failure, from every route:

```json
{ "error": { "code": "SEAT_UNAVAILABLE", "message": "Seat A12 is no longer available." } }
```

| Status | Meaning here                                                   |
| ------ | -------------------------------------------------------------- |
| `400`  | Zod validation failed                                          |
| `401`  | Missing / invalid / expired token                              |
| `403`  | Authenticated but wrong role, or not the owner of the resource |
| `404`  | Resource does not exist                                        |
| `409`  | Lost the race — seat taken, already waitlisted, offer expired  |
| `429`  | Rate limited                                                   |

Times are ISO 8601 UTC. Money is a decimal string, never a float.

---

## Auth

|     | Endpoint              | Role   | Notes                                                                                                                                                                                                                                          |
| --- | --------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ✅  | `POST /auth/register` | public | `{ email, password, name }` → `201 { user, accessToken }`. Role hard-coded `CUSTOMER`; the Zod schema has no `role` field at all, so a supplied one is stripped before the service sees it. `409 EMAIL_TAKEN` on duplicate. Max 5/hour per IP. |
| ✅  | `POST /auth/login`    | public | `{ email, password }` → `200 { user, accessToken }` (HS256, 15 min). `401` with an identical code and message for both a wrong password and an unknown email. Max 10 per 15 min per IP.                                                        |
| ✅  | `GET /auth/me`        | any    | `200 { user }`. `401` if the token is missing, expired, forged, or its account has since been deleted.                                                                                                                                         |

**Demo accounts** — created by `npm run db:seed -w apps/api`, all with password
`password123`: `admin@ticket.dev`, `organiser@ticket.dev`,
`customer@ticket.dev`, `customer2@ticket.dev`. Organiser and admin exist only
here; no API route can grant either role.

## Venues

|     | Endpoint                   | Role   | Notes                                                                                                                                                      |
| --- | -------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ✅  | `GET /venues`              | public | Name, address, seat count.                                                                                                                                 |
| ✅  | `GET /venues/:id`          | public | Includes the full seat layout with `posX`/`posY`.                                                                                                          |
| ✅  | `GET /venues/:id/sections` | public | Distinct section names — what a category may claim.                                                                                                        |
| ✅  | `POST /venues`             | ADMIN  | `{ name, address }`.                                                                                                                                       |
| ✅  | `PATCH /venues/:id`        | ADMIN  | Partial. An omitted field is left alone, never blanked.                                                                                                    |
| ✅  | `POST /venues/:id/seats`   | ADMIN  | `{ section, rows, seatsPerRow }` → a grid, rows labelled A onwards, placed below existing blocks so sections stack. `409 SEATS_ALREADY_EXIST` on a repeat. |

## Events and shows

|     | Endpoint                      | Role      | Notes                                                                                                                                                           |
| --- | ----------------------------- | --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ✅  | `GET /events`                 | public    | Filters `type`, `venueId`, `from`, `to`, `q` (case-insensitive title). `limit` ≤ 50, `offset`. Returns `{ events, total, limit, offset }`. Only upcoming shows. |
| ✅  | `GET /events/:id`             | public    | Categories with their `sections`, plus upcoming shows and each one's seat count.                                                                                |
| ✅  | `GET /events/mine`            | ORGANISER | The caller's own events. ADMIN sees all.                                                                                                                        |
| ✅  | `POST /events`                | ORGANISER | `{ venueId, title, type, description? }`. Caller becomes the owner.                                                                                             |
| ✅  | `PATCH /events/:id`           | ORGANISER | Ownership-checked in the service. `venueId` cannot change — it would orphan every `ShowSeat` already generated.                                                 |
| ✅  | `POST /events/:id/categories` | ORGANISER | `{ name, price, sections[] }`. `400 UNKNOWN_SECTION` if the venue lacks one; `409 SECTION_ALREADY_PRICED` if another category claims it.                        |
| ✅  | `POST /events/:id/shows`      | ORGANISER | `{ startsAt }`, must be future. Creates the show **and** every `ShowSeat` in one transaction; returns `seatCount`. `400 SECTION_NOT_PRICED` rolls it all back.  |
| ✅  | `GET /shows/:id`              | public    | Show, its event, venue, pricing, and seat count.                                                                                                                |

## Seat map and holds ⭐

|     | Endpoint                  | Role     | Notes                                                                                                                                                                                                                                      |
| --- | ------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| ✅  | `GET /shows/:id/seats`    | public   | Explicit select. Returns **effective** status — an expired lease reads as `AVAILABLE` regardless of the stored row. `heldByUserId` is never serialised; a signed-in caller gets `heldByMe` and, only for their own seats, `holdExpiresAt`. |
| ✅  | `POST /shows/:id/holds`   | any auth | `{ seatIds[] }`. Locked transaction, all-or-nothing. `201 { showId, seatIds, holdExpiresAt }`, or `409 SEAT_UNAVAILABLE`. Capped at `MAX_SEATS_PER_HOLD` per request and `MAX_ACTIVE_HOLDS_PER_USER` shows at once; 20/min per IP.         |
| ✅  | `GET /holds/me`           | any auth | Active, unexpired holds with seat labels, prices and the show they belong to.                                                                                                                                                              |
| ✅  | `DELETE /shows/:id/holds` | any auth | Releases the caller's own held seats on that show. `{ released: n }`. Scoped by `heldByUserId` — it can never free somebody else's seat.                                                                                                   |

```http
POST /api/v1/shows/{showId}/holds
{ "seatIds": ["<showSeatId>", "<showSeatId>"] }

201 → { "holdId": "...", "seatIds": [...], "holdExpiresAt": "2026-08-22T10:14:00Z" }
409 → { "error": { "code": "SEAT_UNAVAILABLE", "message": "..." } }
```

## Bookings

|     | Endpoint                    | Role     | Notes                                                                                                                                                                                                                         |
| --- | --------------------------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ✅  | `POST /bookings`            | any auth | `{ showId, seatIds[] }`. Locked transaction; every seat must be `HELD` **by the caller** and unexpired, else `409 HOLD_NOT_VALID`. Price is frozen onto the row. Returns immediately — the QR email is queued, never awaited. |
| ✅  | `GET /bookings`             | any auth | Own history, newest first. Deliberately **without** `qrToken`.                                                                                                                                                                |
| ✅  | `GET /bookings/:id`         | any auth | Owner-checked (`403` otherwise). Includes `qrToken` when confirmed — this is the only route that returns it.                                                                                                                  |
| ✅  | `POST /bookings/:id/cancel` | any auth | Owner-checked. Marks the booking cancelled, releases the seats, marks each `BookingSeat.releasedAt`. `409 ALREADY_CANCELLED` / `409 SHOW_ALREADY_STARTED`. Phase 5 routes the freed seat through `advanceWaitlist()`.         |
| ✅  | `GET /verify/:qrToken`      | public   | What the QR resolves to. `{ valid, status, reference, eventTitle, venue, startsAt, seats }` — never the customer's name or email. A cancelled ticket resolves with `valid: false` rather than 404.                            |

## Waitlist ⭐

|     | Endpoint                              | Role     | Notes                                                                                                                                                                                                                                |
| --- | ------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| ✅  | `POST /shows/:id/waitlist`            | any auth | `{ categoryId }`. `409 SEATS_STILL_AVAILABLE` while anything in the category is takeable (an expired lease counts as takeable). `409 ALREADY_WAITING` on a duplicate. Returns `{ id, position }`.                                    |
| ✅  | `GET /waitlist/me`                    | any auth | Live entries only, with a **derived** queue position (ADR-023) and, for an offered entry, that customer's own `offerToken`.                                                                                                          |
| ✅  | `DELETE /waitlist/:id`                | any auth | Owner-checked. Leaving while holding an offer hands the seat straight on rather than stranding it — `{ left, passedOn }`.                                                                                                            |
| ✅  | `GET /waitlist/offers/:token`         | public   | Offer detail for the emailed link — the link is often opened on a phone that is not signed in. `410 OFFER_EXPIRED` once it lapses, `404` if the token is not recognised.                                                             |
| ✅  | `POST /waitlist/offers/:token/accept` | any auth | Books the offered seat. **Five checks:** token resolves, entry still `OFFERED`, not expired, seat still `OFFERED`, and the caller is the customer it was offered to (`403` otherwise). Single use — the token is cleared on success. |

## Organiser

|     | Endpoint                            | Role      | Notes                                                                             |
| --- | ----------------------------------- | --------- | --------------------------------------------------------------------------------- |
|     | `GET /organiser/events`             | ORGANISER | Own events only.                                                                  |
|     | `GET /organiser/events/:id/summary` | ORGANISER | Bookings, seats sold, revenue by category, per show. Excludes cancelled bookings. |

## Realtime (Socket.IO)

Namespace `/`, JWT passed in the handshake.

| Direction       | Event         | Payload                                  |
| --------------- | ------------- | ---------------------------------------- |
| client → server | `show:join`   | `{ showId }`                             |
| client → server | `show:leave`  | `{ showId }`                             |
| server → client | `seat:sync`   | Full seat snapshot, sent on join         |
| server → client | `seat:update` | One seat, after every committed mutation |

Broadcasts go to room `show:{showId}`, always **after** the transaction commits.
