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

|     | Endpoint              | Role   | Notes                                                                                        |
| --- | --------------------- | ------ | -------------------------------------------------------------------------------------------- |
|     | `POST /auth/register` | public | Role hard-coded `CUSTOMER` server-side. A client-supplied `role` is ignored, never honoured. |
|     | `POST /auth/login`    | public | Returns `accessToken` (HS256, 15 min) + user. Rate limited.                                  |
|     | `GET /auth/me`        | any    | Current user from the token.                                                                 |

## Venues (admin)

|     | Endpoint                 | Role  | Notes                                                        |
| --- | ------------------------ | ----- | ------------------------------------------------------------ |
|     | `POST /venues`           | ADMIN |                                                              |
|     | `GET /venues`            | any   |                                                              |
|     | `GET /venues/:id`        | any   | Includes seat layout.                                        |
|     | `PATCH /venues/:id`      | ADMIN |                                                              |
|     | `POST /venues/:id/seats` | ADMIN | Bulk create from a rows × columns spec; fills `posX`/`posY`. |

## Events and shows

|     | Endpoint                      | Role      | Notes                                                                      |
| --- | ----------------------------- | --------- | -------------------------------------------------------------------------- |
|     | `GET /events`                 | public    | Filters: `type`, `venueId`, `from`, `to`, `q`. Paginated.                  |
|     | `GET /events/:id`             | public    | Includes categories, prices, upcoming shows.                               |
|     | `POST /events`                | ORGANISER | Organiser becomes the owner.                                               |
|     | `PATCH /events/:id`           | ORGANISER | Ownership-checked, not just role-checked.                                  |
|     | `POST /events/:id/categories` | ORGANISER | `{ name, price }`, unique per event.                                       |
|     | `POST /events/:id/shows`      | ORGANISER | Creates the show **and** instantiates every `ShowSeat` in one transaction. |
|     | `GET /shows/:id`              | public    |                                                                            |

## Seat map and holds ⭐

|     | Endpoint                | Role     | Notes                                                                                                                |
| --- | ----------------------- | -------- | -------------------------------------------------------------------------------------------------------------------- |
|     | `GET /shows/:id/seats`  | public   | Explicit select. Returns effective status — an expired lease reads as `AVAILABLE`. **Never returns `heldByUserId`.** |
|     | `POST /shows/:id/holds` | CUSTOMER | `{ seatIds[] }`. Locked transaction, all-or-nothing. `201` with `holdExpiresAt`, or `409`. Capped and rate limited.  |
|     | `GET /holds/me`         | CUSTOMER | Active holds for the current user.                                                                                   |
|     | `DELETE /holds/:id`     | CUSTOMER | Explicit release; frees the seats immediately.                                                                       |

```http
POST /api/v1/shows/{showId}/holds
{ "seatIds": ["<showSeatId>", "<showSeatId>"] }

201 → { "holdId": "...", "seatIds": [...], "holdExpiresAt": "2026-08-22T10:14:00Z" }
409 → { "error": { "code": "SEAT_UNAVAILABLE", "message": "..." } }
```

## Bookings

|     | Endpoint                    | Role     | Notes                                                                                                                           |
| --- | --------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------- |
|     | `POST /bookings`            | CUSTOMER | `{ showId, seatIds[] }`. Requires the seats be `HELD` by the caller and unexpired. Returns immediately; the QR email is queued. |
|     | `GET /bookings`             | CUSTOMER | Booking history.                                                                                                                |
|     | `GET /bookings/:id`         | CUSTOMER | Owner-checked.                                                                                                                  |
|     | `POST /bookings/:id/cancel` | CUSTOMER | Owner-checked. Frees each seat through `advanceWaitlist()`.                                                                     |
|     | `GET /verify/:qrToken`      | public   | What the QR resolves to. Returns validity + show + seats, never the customer's email.                                           |

## Waitlist ⭐

|     | Endpoint                              | Role     | Notes                                                                                                    |
| --- | ------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------- |
|     | `POST /shows/:id/waitlist`            | CUSTOMER | `{ categoryId }`. Only when the category is sold out. `409` on duplicate entry.                          |
|     | `GET /waitlist/me`                    | CUSTOMER | Entries with queue position.                                                                             |
|     | `DELETE /waitlist/:id`                | CUSTOMER | Leave the queue.                                                                                         |
|     | `GET /waitlist/offers/:token`         | public   | Offer detail for the emailed link. `410` once expired.                                                   |
|     | `POST /waitlist/offers/:token/accept` | CUSTOMER | Books the offered seat. Checks token, entry status, expiry, seat status, and caller identity — all five. |

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
