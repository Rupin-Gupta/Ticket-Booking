# Context Log

Rolling session journal. Newest entry on top. Read the top entry to know
exactly where the project stands; read `docs/TODO.md` to know what is next.

Update this at the end of every working session — a session that ends without
an entry here costs the next one twenty minutes of rediscovery.

---

## Current state

|                 |                                                                                                                     |
| --------------- | ------------------------------------------------------------------------------------------------------------------- |
| **Phase**       | 0 — Foundations, scaffolding done                                                                                   |
| **Runnable?**   | Yes. `npm install && npm run dev` → API on :4000, web on :5173. No database yet.                                    |
| **Repo**        | Local git initialised. Remote `https://github.com/Rupin-Gupta/Ticket-Booking.git` — **not pushed yet, user pushes** |
| **Blocked on**  | Three accounts, user action: Supabase, Upstash, Resend                                                              |
| **Next action** | User creates Supabase + Upstash, fills `apps/api/.env`, then `npm run db:migrate`. Phase 1 (auth) after that.       |

`/health` reports which of database / redis / auth / email are still
unconfigured, and the web placeholder renders that as a checklist — so the
remaining setup is visible without reading code.

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
