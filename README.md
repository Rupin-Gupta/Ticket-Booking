# Ticket Booking System

Ticket booking platform for movies and concerts. Customers pick seats from a
visual map, held seats auto-release when checkout is abandoned, sold-out shows
run a FIFO waitlist that auto-assigns freed seats via time-limited offers, and
every confirmed booking emails a QR code ticket.

> **Status:** Phase 0 — foundations. Nothing is running yet. See
> [docs/TODO.md](docs/TODO.md) for the live task list and
> [docs/CONTEXT.md](docs/CONTEXT.md) for where the last session left off.

## Documentation map

| File | What lives there |
| --- | --- |
| [CLAUDE.md](CLAUDE.md) | Load-bearing project memory: stack, non-negotiable rules, Prisma schema. Read first. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the system is built: layers, seat lifecycle, concurrency, waitlist, realtime. |
| [docs/CONTEXT.md](docs/CONTEXT.md) | Rolling session log — what changed, what is in flight, what is next. |
| [docs/DECISIONS.md](docs/DECISIONS.md) | ADR log. Every non-obvious choice with its alternative and rationale. |
| [docs/RULES.md](docs/RULES.md) | Working agreement between the user and Claude. Grows as new rules are set. |
| [docs/DEBUGGING.md](docs/DEBUGGING.md) | Symptom → cause → fix log. Every bug that cost more than five minutes. |
| [docs/TODO.md](docs/TODO.md) | Phase-by-phase checklist, the single source of truth for progress. |
| [docs/API.md](docs/API.md) | Endpoint reference. Written as endpoints ship, not before. |
| SYSTEM_DESIGN.md | Deliverable #4, the 800-word write-up. Written in Phase 8. |

## Stack

Node + TypeScript + Express · React + Vite · PostgreSQL (Neon) + Prisma ·
Redis (Upstash) + BullMQ · Socket.IO · JWT + Argon2id · `qrcode` + Nodemailer/Resend.

Monorepo via npm workspaces: `apps/api`, `apps/web`, `packages/shared`.

## Setup

> Filled in as Phase 0 lands. Placeholder so the shape is visible.

```bash
npm install
cp apps/api/.env.example apps/api/.env   # then fill in the values
npm run db:migrate -w apps/api
npm run dev                              # api + web together
```

## Deliverables checklist

- [ ] 1. Zip file with complete source code
- [ ] 2. README with setup guide, `.env.example`, API docs, DB schema, seat hold + waitlist explanation
- [ ] 3. Hosted application URL
- [ ] 4. System design write-up (800 words max)
