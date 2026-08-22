# Ticket Booking System

Ticket booking platform for movies and concerts. Customers pick seats from a
visual map, held seats auto-release when checkout is abandoned, sold-out shows
run a FIFO waitlist that auto-assigns freed seats via time-limited offers, and
every confirmed booking emails a QR code ticket.

> **Status:** Phase 0 — scaffolding done. `npm install && npm run dev` runs both
> apps today; the database is not wired yet (see [Accounts](#accounts)). Live
> task list in [docs/TODO.md](docs/TODO.md), current state in
> [docs/CONTEXT.md](docs/CONTEXT.md).

## Documentation map

| File                                         | What lives there                                                                     |
| -------------------------------------------- | ------------------------------------------------------------------------------------ |
| [CLAUDE.md](CLAUDE.md)                       | Load-bearing project memory: stack, non-negotiable rules, Prisma schema. Read first. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the system is built: layers, seat lifecycle, concurrency, waitlist, realtime.    |
| [docs/CONTEXT.md](docs/CONTEXT.md)           | Rolling session log — what changed, what is in flight, what is next.                 |
| [docs/DECISIONS.md](docs/DECISIONS.md)       | ADR log. Every non-obvious choice with its alternative and rationale.                |
| [docs/RULES.md](docs/RULES.md)               | Working agreement between the user and Claude. Grows as new rules are set.           |
| [docs/DEBUGGING.md](docs/DEBUGGING.md)       | Symptom → cause → fix log. Every bug that cost more than five minutes.               |
| [docs/TODO.md](docs/TODO.md)                 | Phase-by-phase checklist, the single source of truth for progress.                   |
| [docs/API.md](docs/API.md)                   | Endpoint reference. Written as endpoints ship, not before.                           |
| SYSTEM_DESIGN.md                             | Deliverable #4, the 800-word write-up. Written in Phase 8.                           |

## Stack

Node + TypeScript + Express · React + Vite · PostgreSQL (Supabase) + Prisma ·
Redis (Upstash) + BullMQ · Socket.IO · JWT + Argon2id · `qrcode` + Nodemailer/Resend.

Monorepo via npm workspaces: `apps/api`, `apps/web`, `packages/shared`.

## Setup

Requires Node 20.12 or newer (the API uses Node's native `.env` loading).

```bash
npm install                              # installs all workspaces, generates the Prisma client
cp apps/api/.env.example apps/api/.env   # see "Accounts" below before filling it in
npm run dev                              # API on :4000, web on :5173
```

Open http://localhost:5173 — the page reports which services are wired and
which are still missing. The API runs without a database, so this works before
any account exists.

### Accounts

Three free-tier services. None expire, but see the Supabase warning below:

| Service                          | Fills                        | Notes                                                                                                                  |
| -------------------------------- | ---------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| [Supabase](https://supabase.com) | `DATABASE_URL`, `DIRECT_URL` | Dashboard → Connect. See the connection-string rules below — getting these wrong is the most common failure here.      |
| [Upstash](https://upstash.com)   | `REDIS_URL`                  | Redis for job queues and the Socket.IO adapter. Needed from Phase 3.                                                   |
| [Resend](https://resend.com)     | `RESEND_API_KEY`             | Needed from Phase 4. `onboarding@resend.dev` sends without a verified domain, but only to the account owner's address. |

**Supabase gives you three connection strings. Take these two, and not the third:**

| Variable       | Which string                      | Port   | Used by                                               |
| -------------- | --------------------------------- | ------ | ----------------------------------------------------- |
| `DATABASE_URL` | Transaction pooler                | `6543` | The running app                                       |
| `DIRECT_URL`   | Session pooler                    | `5432` | `prisma migrate`                                      |
| —              | ~~Direct~~ `db.<ref>.supabase.co` | —      | **Never.** IPv6-only; works locally, fails on Render. |

`DATABASE_URL` must end in `?pgbouncer=true` — the transaction pooler cannot do
prepared statements, and Prisma uses them by default.

> ⚠️ **Supabase pauses a free project after 7 days with no database activity,
> and restoring it is manual.** `/health` runs a `SELECT 1`, so a daily ping of
> the deployed API keeps it alive. That cron is set up in Phase 8 — before
> submitting or demoing, confirm it is actually running.

Also generate a signing secret: `openssl rand -base64 48` → `JWT_SECRET`.

### Once the database is up

```bash
npm run db:migrate     # creates the schema on Supabase (uses DIRECT_URL)
npm run db:studio      # optional: browse the data
```

### Other commands

```bash
npm run typecheck      # all three workspaces
npm run format         # prettier
npm run build          # production build of the web app
```

## Deliverables checklist

- [ ] 1. Zip file with complete source code
- [ ] 2. README with setup guide, `.env.example`, API docs, DB schema, seat hold + waitlist explanation
- [ ] 3. Hosted application URL
- [ ] 4. System design write-up (800 words max)
