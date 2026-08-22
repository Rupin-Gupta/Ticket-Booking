# Venue Capabilities and Booking Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give venues capabilities an admin controls (stage layout, permitted event types, turnaround), stop two organisers double-booking a venue, and replace the single-page hold with a three-page flow whose seats expire on two different clocks.

**Architecture:** Extends the existing model rather than restructuring it. Stage layout is *stored geometry* — the venue builder writes radial coordinates and the seat map renderer stays unchanged. Venue scheduling gets an application-level check for good error messages plus a Postgres GiST exclusion constraint as the guarantee, partial on `status` so cancelling frees the slot. The two-clock TTL reuses lazy expiry: going back *shortens* a hold rather than deleting it.

**Tech Stack:** Node 20+, TypeScript (strict, `exactOptionalPropertyTypes`), Express 5, Prisma 6 + PostgreSQL (Supabase), React 19 + Vite, `node:test`, npm workspaces.

**Spec:** `docs/superpowers/specs/2026-08-23-venue-capabilities-and-booking-flow-design.md`

## Global Constraints

- **Never change the hold transaction's locking discipline.** The `FOR UPDATE`, the status re-read and the write stay together in `apps/api/src/modules/seats/service.ts`. Only the abuse cap may sit outside it (ADR-019).
- **`apps/api/tests/concurrency/holds.test.ts` must stay green after every task.** It is the regression guard for the whole plan.
- Money is a decimal string end to end; use `Prisma.Decimal` arithmetic, never `Number`.
- Every raw query is a tagged-template `$queryRaw`. `$queryRawUnsafe` and `Prisma.raw` are banned near request data (CLAUDE.md rule 13).
- `heldByUserId` never leaves the server (CLAUDE.md rule 8).
- Run `npm run format` before every commit. `npm run typecheck` must report 0 errors.
- Tests run with `NODE_ENV=test`. After Task 1 they refuse to run without `DATABASE_URL_TEST`.
- Commit messages: imperative subject under 72 chars, body explains *why*. End with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

## File Structure

**Created**

| File | Responsibility |
| --- | --- |
| `apps/api/src/lib/geometry.ts` | Pure seat-coordinate maths for both stage layouts. No I/O, so it is unit-testable without a database |
| `apps/api/src/modules/venues/scheduling.ts` | Venue availability: occupied-window maths and the overlap check |
| `apps/api/tests/geometry.test.ts` | Unit tests for coordinate generation |
| `apps/api/tests/venues.capabilities.test.ts` | Venue layout, allowed types, turnaround |
| `apps/api/tests/concurrency/scheduling.test.ts` | Double-booking, including the parallel case |
| `apps/api/tests/holds.grace.test.ts` | Two-clock TTL behaviour |
| `apps/web/src/pages/CheckoutPage.tsx` | Page 2 of the booking flow |
| `apps/web/src/pages/checkout.css` | Its styles |

**Modified**

| File | Change |
| --- | --- |
| `apps/api/prisma/schema.prisma` | `StageLayout`, `ShowStatus` enums; `Venue` and `Show` columns |
| `apps/api/src/env.ts` | Test connection strings, `RELEASE_GRACE_SECONDS`, `HOLD_TTL_SECONDS` default |
| `apps/api/src/lib/prisma.ts` | Select the connection string by `NODE_ENV` |
| `apps/api/src/modules/venues/schema.ts` | Capability fields, arc input |
| `apps/api/src/modules/venues/service.ts` | Capability validation, delegate coordinates to `geometry.ts` |
| `apps/api/src/modules/events/schema.ts` | `durationMinutes` on show creation |
| `apps/api/src/modules/events/service.ts` | Event-type gate; scheduling fields and overlap check |
| `apps/api/src/modules/seats/service.ts` | `releaseHolds` becomes a grace release; add `extendHold` |
| `apps/api/src/modules/seats/routes.ts` | Extend endpoint |
| `apps/web/src/pages/ShowPage.tsx` | Continue navigates instead of holding in place |
| `apps/web/src/main.tsx` | Checkout route |

---

## Task 1: Separate test database

**Files:**
- Modify: `apps/api/src/env.ts`
- Modify: `apps/api/src/lib/prisma.ts`
- Modify: `apps/api/package.json` (scripts)
- Modify: `apps/api/.env.example`

**Interfaces:**
- Consumes: nothing
- Produces: `activeDatabaseUrl(): string` exported from `apps/api/src/env.ts`

**Prerequisite (human):** create a second free Supabase project, then add its two connection strings to `apps/api/.env` as `DATABASE_URL_TEST` (transaction pooler, `:6543`, `?pgbouncer=true`) and `DIRECT_URL_TEST` (session pooler, `:5432`).

- [ ] **Step 1: Add the test connection strings to the env schema**

In `apps/api/src/env.ts`, inside the `z.object({ ... })`, immediately after the `REDIS_URL` line:

```ts
  // --- Test database. A separate Supabase project, so `npm test` never writes
  // into the database serving the live site. Optional in the schema because
  // production has no use for them; activeDatabaseUrl() is what refuses to run
  // tests without them.
  DATABASE_URL_TEST: blankAsUnset(z.string().url().optional()),
  DIRECT_URL_TEST: blankAsUnset(z.string().url().optional()),
```

- [ ] **Step 2: Add the selector at the end of `env.ts`**

Append to `apps/api/src/env.ts`:

```ts
/**
 * The connection string this process should use.
 *
 * Under NODE_ENV=test this REFUSES to fall back to DATABASE_URL. A test suite
 * that quietly writes to production is worse than one that will not start —
 * the failure is loud, immediate, and names the fix.
 */
export function activeDatabaseUrl(): string {
  if (env.NODE_ENV === 'test') {
    if (!env.DATABASE_URL_TEST) {
      throw new Error(
        'DATABASE_URL_TEST is not set. Tests refuse to run against the production ' +
          'database. Create a second Supabase project and add DATABASE_URL_TEST ' +
          'and DIRECT_URL_TEST to apps/api/.env — see apps/api/.env.example.',
      );
    }
    return env.DATABASE_URL_TEST;
  }
  return requireEnv('DATABASE_URL');
}
```

- [ ] **Step 3: Use it in the Prisma client**

Replace the body of `apps/api/src/lib/prisma.ts` below the doc comment. The whole file becomes:

```ts
import { PrismaClient } from '@prisma/client';
import { activeDatabaseUrl, env, isProd } from '../env.js';

/**
 * One client for the process.
 *
 * The connection string comes from activeDatabaseUrl(), which routes tests to a
 * separate database and refuses to fall back to production.
 *
 * `globalThis` cache is for tsx watch: a reload without it leaks a connection
 * pool per restart until Postgres refuses new connections.
 */
const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

export const prisma =
  globalForPrisma.prisma ??
  new PrismaClient({
    datasourceUrl: activeDatabaseUrl(),
    log: ['warn', 'error'],
  });

if (!isProd && env.NODE_ENV !== 'test') globalForPrisma.prisma = prisma;
```

- [ ] **Step 4: Verify the guard fires**

Run: `cd apps/api && NODE_ENV=test DATABASE_URL_TEST= npx tsx -e "import('./src/lib/prisma.ts')"`

Expected: throws with `DATABASE_URL_TEST is not set. Tests refuse to run against the production database.`

- [ ] **Step 5: Add the test-migration script**

In `apps/api/package.json`, add to `scripts`:

```json
    "db:deploy:test": "cross-env-free node -e \"const{execSync}=require('node:child_process');process.loadEnvFile('.env');execSync('npx prisma migrate deploy',{stdio:'inherit',env:{...process.env,DATABASE_URL:process.env.DATABASE_URL_TEST,DIRECT_URL:process.env.DIRECT_URL_TEST}})\"",
```

Note: the script name `cross-env-free` is not a package — it is `node -e` doing the env swap inline, so no dependency is added.

Correct the entry to exactly:

```json
    "db:deploy:test": "node -e \"const{execSync}=require('node:child_process');process.loadEnvFile('.env');execSync('npx prisma migrate deploy',{stdio:'inherit',env:{...process.env,DATABASE_URL:process.env.DATABASE_URL_TEST,DIRECT_URL:process.env.DIRECT_URL_TEST}})\"",
```

- [ ] **Step 6: Migrate the test database**

Run: `cd apps/api && npm run db:deploy:test`
Expected: all four migrations applied to the new project.

- [ ] **Step 7: Document the variables**

In `apps/api/.env.example`, after the `DIRECT_URL` line:

```
# --- Test database (a SECOND Supabase project) -------------------------------
# Tests refuse to run without these rather than falling back to the database
# above. A suite that quietly writes to production is worse than one that will
# not start. Apply migrations with: npm run db:deploy:test
DATABASE_URL_TEST=""
DIRECT_URL_TEST=""
```

- [ ] **Step 8: Run the whole suite against the new database**

Run: `cd apps/api && NODE_ENV=test npm test`
Expected: 79 passing, 0 failing.

- [ ] **Step 9: Commit**

```bash
git add apps/api/src/env.ts apps/api/src/lib/prisma.ts apps/api/package.json apps/api/.env.example
git commit -m "$(cat <<'EOF'
Route tests to a separate database

Tests wrote into the database serving the live site. activeDatabaseUrl() now
selects by NODE_ENV and refuses to fall back to DATABASE_URL under test — a
suite that quietly writes to production is worse than one that will not start,
so the failure is loud and names the fix.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Seat geometry as a pure module

**Files:**
- Create: `apps/api/src/lib/geometry.ts`
- Create: `apps/api/tests/geometry.test.ts`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `type SeatPosition = { row: string; number: number; posX: number; posY: number }`
  - `generateEndStageBlock(input: { rows: number; seatsPerRow: number; startY: number }): SeatPosition[]`
  - `generateCentreStageBlock(input: { rows: number; seatsPerRow: number; startRadius: number; arcStartDegrees: number; arcSpanDegrees: number }): SeatPosition[]`
  - `ROW_LABELS: string`

Extracted as a pure module because coordinate maths is the one part of this milestone testable without a database, and a round-trip to Supabase per assertion would make these tests slow for no reason.

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/geometry.test.ts`:

```ts
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  generateCentreStageBlock,
  generateEndStageBlock,
  ROW_LABELS,
} from '../src/lib/geometry.js';

describe('end-stage geometry', () => {
  test('produces rows x seatsPerRow seats labelled from A', () => {
    const seats = generateEndStageBlock({ rows: 3, seatsPerRow: 4, startY: 0 });
    assert.equal(seats.length, 12);
    assert.equal(seats[0]!.row, 'A');
    assert.equal(seats[11]!.row, 'C');
    assert.equal(seats[11]!.number, 4);
  });

  test('centres each row on x = 0 so unequal rows stay aligned', () => {
    const four = generateEndStageBlock({ rows: 1, seatsPerRow: 4, startY: 0 });
    const six = generateEndStageBlock({ rows: 1, seatsPerRow: 6, startY: 0 });
    const mean = (s: { posX: number }[]) => s.reduce((n, x) => n + x.posX, 0) / s.length;
    assert.equal(mean(four), 0);
    assert.equal(mean(six), 0);
  });

  test('startY offsets every row so blocks stack', () => {
    const seats = generateEndStageBlock({ rows: 2, seatsPerRow: 2, startY: 7 });
    assert.deepEqual([...new Set(seats.map((s) => s.posY))], [7, 8]);
  });
});

describe('centre-stage geometry', () => {
  test('every seat sits on its row radius', () => {
    const seats = generateCentreStageBlock({
      rows: 2,
      seatsPerRow: 8,
      startRadius: 5,
      arcStartDegrees: 0,
      arcSpanDegrees: 360,
    });
    const radius = (s: { posX: number; posY: number }) => Math.hypot(s.posX, s.posY);
    const rowA = seats.filter((s) => s.row === 'A');
    const rowB = seats.filter((s) => s.row === 'B');
    for (const s of rowA) assert.ok(Math.abs(radius(s) - 5) < 1e-9);
    for (const s of rowB) assert.ok(Math.abs(radius(s) - 6) < 1e-9);
  });

  test('a quarter arc stays inside its wedge', () => {
    const seats = generateCentreStageBlock({
      rows: 1,
      seatsPerRow: 10,
      startRadius: 4,
      arcStartDegrees: 0,
      arcSpanDegrees: 90,
    });
    // Angles in [0, 90) put every seat in the positive quadrant.
    for (const s of seats) {
      assert.ok(s.posX > 0, `posX ${s.posX} should be positive`);
      assert.ok(s.posY > 0, `posY ${s.posY} should be positive`);
    }
  });

  test('seat count and labelling match the end-stage contract', () => {
    const seats = generateCentreStageBlock({
      rows: 2,
      seatsPerRow: 3,
      startRadius: 3,
      arcStartDegrees: 0,
      arcSpanDegrees: 180,
    });
    assert.equal(seats.length, 6);
    assert.equal(seats[0]!.row, 'A');
    assert.equal(seats[5]!.row, 'B');
    assert.equal(ROW_LABELS[0], 'A');
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/geometry.test.ts`
Expected: FAIL — `Cannot find module '../src/lib/geometry.js'`

- [ ] **Step 3: Write the implementation**

Create `apps/api/src/lib/geometry.ts`:

```ts
/**
 * Seat coordinate generation for both stage layouts.
 *
 * Pure functions, no I/O — coordinates are the one part of venue building that
 * can be tested without a database, and a round trip per assertion would make
 * those tests slow for nothing.
 *
 * posX / posY are grid units, not pixels. The frontend decides how big a seat
 * is, which is why a radial layout needs no renderer change: it writes the same
 * two numbers, just arranged in a circle.
 */

export const ROW_LABELS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

export type SeatPosition = { row: string; number: number; posX: number; posY: number };

/** A rectangular block. Rows stack downwards, each centred on x = 0. */
export function generateEndStageBlock(input: {
  rows: number;
  seatsPerRow: number;
  startY: number;
}): SeatPosition[] {
  const seats: SeatPosition[] = [];
  for (let r = 0; r < input.rows; r++) {
    for (let n = 1; n <= input.seatsPerRow; n++) {
      seats.push({
        row: ROW_LABELS[r]!,
        number: n,
        // Centring on zero keeps rows of different widths aligned.
        posX: n - (input.seatsPerRow + 1) / 2,
        posY: input.startY + r,
      });
    }
  }
  return seats;
}

/**
 * A block arranged around a central stage.
 *
 * Rows become radii and seats spread along an arc. Seats sit at the *centre* of
 * their angular slot rather than on its edge, so a full 360° block does not put
 * the first and last seat on top of each other.
 */
export function generateCentreStageBlock(input: {
  rows: number;
  seatsPerRow: number;
  startRadius: number;
  arcStartDegrees: number;
  arcSpanDegrees: number;
}): SeatPosition[] {
  const seats: SeatPosition[] = [];
  for (let r = 0; r < input.rows; r++) {
    const radius = input.startRadius + r;
    for (let n = 1; n <= input.seatsPerRow; n++) {
      const degrees =
        input.arcStartDegrees + (input.arcSpanDegrees * (n - 0.5)) / input.seatsPerRow;
      const radians = (degrees * Math.PI) / 180;
      seats.push({
        row: ROW_LABELS[r]!,
        number: n,
        posX: radius * Math.cos(radians),
        posY: radius * Math.sin(radians),
      });
    }
  }
  return seats;
}
```

- [ ] **Step 4: Run the tests**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/geometry.test.ts`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/lib/geometry.ts apps/api/tests/geometry.test.ts
git commit -m "$(cat <<'EOF'
Extract seat geometry as a pure, testable module

Coordinate maths is the one part of venue building testable without a database,
so it moves out of the service. Adds centre-stage generation: rows become radii
and seats spread along an arc, seated at the centre of their angular slot so a
full 360-degree block does not put the first and last seat on top of each other.

Both layouts emit plain posX/posY grid units, which is why a radial venue needs
no seat map renderer change at all.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Venue capabilities — schema and validation

**Files:**
- Modify: `apps/api/prisma/schema.prisma`
- Create: `apps/api/prisma/migrations/20260823090000_venue_capabilities/migration.sql`
- Modify: `apps/api/src/modules/venues/schema.ts`
- Modify: `apps/api/src/modules/venues/service.ts`
- Create: `apps/api/tests/venues.capabilities.test.ts`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Venue.stageLayout: StageLayout`, `Venue.allowedEventTypes: EventType[]`, `Venue.turnaroundMinutes: number`
  - `createVenueSchema` accepting all three
  - Error codes `CENTRE_STAGE_CANNOT_SHOW_MOVIES`

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/venues.capabilities.test.ts`:

```ts
import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { after, before, describe, test } from 'node:test';
import type { Server } from 'node:http';
import type express from 'express';
import { createApp } from '../src/app.js';
import { prisma } from '../src/lib/prisma.js';

const RUN = randomBytes(5).toString('hex');
const tag = (s: string) => `${s} ${RUN}`;
const emailFor = (who: string) => `vc-${who}-${RUN}@example.test`;
const PASSWORD = 'correct horse battery';

let server: Server;
let base: string;
let admin: string;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const json = async (res: Response): Promise<any> => res.json();

const call = (method: string, path: string, body?: unknown, token?: string) =>
  fetch(base + '/api/v1' + path, {
    method,
    headers: {
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });

before(async () => {
  const app = createApp();
  server = await new Promise<Server>((resolve) => {
    const s = (app as express.Express).listen(0, () => resolve(s));
  });
  const addr = server.address();
  if (typeof addr === 'string' || addr === null) throw new Error('no port');
  base = `http://127.0.0.1:${addr.port}`;

  const email = emailFor('admin');
  await call('POST', '/auth/register', { email, password: PASSWORD, name: 'Admin' });
  await prisma.user.update({ where: { email }, data: { role: 'ADMIN' } });
  admin = (await json(await call('POST', '/auth/login', { email, password: PASSWORD })))
    .accessToken;
});

after(async () => {
  await prisma.seat.deleteMany({ where: { venue: { name: { contains: RUN } } } });
  await prisma.venue.deleteMany({ where: { name: { contains: RUN } } });
  await prisma.user.deleteMany({ where: { email: { endsWith: `-${RUN}@example.test` } } });
  await prisma.$disconnect();
  server.close();
});

describe('venue capabilities', () => {
  test('defaults to an end-stage venue allowing both event types', async () => {
    const res = await call('POST', '/venues', { name: tag('Default'), address: 'x' }, admin);
    assert.equal(res.status, 201);
    const { venue } = await json(res);
    assert.equal(venue.stageLayout, 'END_STAGE');
    assert.deepEqual([...venue.allowedEventTypes].sort(), ['CONCERT', 'MOVIE']);
    assert.equal(venue.turnaroundMinutes, 15);
  });

  test('accepts an explicit centre-stage concert venue', async () => {
    const res = await call(
      'POST',
      '/venues',
      {
        name: tag('Round'),
        address: 'x',
        stageLayout: 'CENTRE_STAGE',
        allowedEventTypes: ['CONCERT'],
        turnaroundMinutes: 45,
      },
      admin,
    );
    assert.equal(res.status, 201);
    const { venue } = await json(res);
    assert.equal(venue.stageLayout, 'CENTRE_STAGE');
    assert.equal(venue.turnaroundMinutes, 45);
  });

  test('refuses a centre-stage venue that allows movies', async () => {
    const res = await call(
      'POST',
      '/venues',
      {
        name: tag('Absurd'),
        address: 'x',
        stageLayout: 'CENTRE_STAGE',
        allowedEventTypes: ['MOVIE', 'CONCERT'],
      },
      admin,
    );
    assert.equal(res.status, 400);
    assert.equal((await json(res)).error.code, 'CENTRE_STAGE_CANNOT_SHOW_MOVIES');
  });

  test('refuses an empty allowedEventTypes', async () => {
    const res = await call(
      'POST',
      '/venues',
      { name: tag('Nothing'), address: 'x', allowedEventTypes: [] },
      admin,
    );
    assert.equal(res.status, 400);
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/venues.capabilities.test.ts`
Expected: FAIL — `venue.stageLayout` is `undefined`.

- [ ] **Step 3: Add the schema fields**

In `apps/api/prisma/schema.prisma`, add the enum above `model Venue`:

```prisma
/// Where the stage sits, which decides how the venue builder lays out seats.
enum StageLayout {
  END_STAGE // audience faces one way, like a cinema
  CENTRE_STAGE // in the round, audience surrounds the stage
}
```

and replace `model Venue` with:

```prisma
model Venue {
  id      String  @id @default(uuid())
  name    String
  address String

  /// Admin-owned capabilities. An organiser books a venue; it does not book them.
  stageLayout       StageLayout @default(END_STAGE)
  /// Which event types may be scheduled here. A CENTRE_STAGE venue may not
  /// allow MOVIE — nobody projects a film in the round.
  allowedEventTypes EventType[] @default([MOVIE, CONCERT])
  /// Minutes the room stays unavailable after a show ends, for clearing and
  /// resetting. A stadium needs longer than a screening room.
  turnaroundMinutes Int         @default(15)

  seats  Seat[]
  events Event[]
}
```

- [ ] **Step 4: Write the migration**

Create `apps/api/prisma/migrations/20260823090000_venue_capabilities/migration.sql`:

```sql
-- Venues become admin-owned infrastructure with capabilities, rather than a
-- name and an address. Existing venues keep working: END_STAGE and both event
-- types are exactly what they implicitly were.

CREATE TYPE "StageLayout" AS ENUM ('END_STAGE', 'CENTRE_STAGE');

ALTER TABLE "Venue"
  ADD COLUMN "stageLayout"       "StageLayout" NOT NULL DEFAULT 'END_STAGE',
  ADD COLUMN "allowedEventTypes" "EventType"[] NOT NULL DEFAULT ARRAY['MOVIE','CONCERT']::"EventType"[],
  ADD COLUMN "turnaroundMinutes" INTEGER       NOT NULL DEFAULT 15;
```

- [ ] **Step 5: Apply to both databases**

Run:
```bash
cd apps/api && npm run db:migrate -- --name venue_capabilities && npm run db:deploy:test
```
Expected: applied to development and to the test project.

- [ ] **Step 6: Extend the request schema**

Replace `createVenueSchema` and `updateVenueSchema` in `apps/api/src/modules/venues/schema.ts`:

```ts
export const createVenueSchema = z.object({
  name: z.string().trim().min(1).max(120),
  address: z.string().trim().min(1).max(240),
  stageLayout: z.enum(['END_STAGE', 'CENTRE_STAGE']).default('END_STAGE'),
  // At least one, or the venue can host nothing at all.
  allowedEventTypes: z
    .array(z.enum(['MOVIE', 'CONCERT']))
    .min(1, 'A venue must allow at least one event type.')
    .default(['MOVIE', 'CONCERT']),
  // Long enough to clear and reset the room. Capped at four hours because
  // beyond that the organiser wants a different day, not a longer gap.
  turnaroundMinutes: z.number().int().min(0).max(240).default(15),
});

export const updateVenueSchema = createVenueSchema.partial();
```

- [ ] **Step 7: Validate and persist in the service**

In `apps/api/src/modules/venues/service.ts`, replace `createVenue` and `updateVenue`:

```ts
/**
 * A centre-stage venue may not allow MOVIE. Nobody projects a film in the round,
 * and refusing it here beats discovering it when a cinema's seat map renders as
 * a circle.
 */
function assertCapabilitiesCoherent(input: {
  stageLayout?: 'END_STAGE' | 'CENTRE_STAGE';
  allowedEventTypes?: ('MOVIE' | 'CONCERT')[];
}) {
  if (input.stageLayout === 'CENTRE_STAGE' && input.allowedEventTypes?.includes('MOVIE')) {
    throw ApiError.badRequest(
      'CENTRE_STAGE_CANNOT_SHOW_MOVIES',
      'A centre-stage venue surrounds the stage, so it cannot host a film. Allow CONCERT only, or use END_STAGE.',
    );
  }
}

const venueSelect = {
  id: true,
  name: true,
  address: true,
  stageLayout: true,
  allowedEventTypes: true,
  turnaroundMinutes: true,
} as const;

export function createVenue(input: CreateVenueInput) {
  assertCapabilitiesCoherent(input);
  return prisma.venue.create({ data: input, select: venueSelect });
}

export async function updateVenue(id: string, input: UpdateVenueInput) {
  const existing = await getVenue(id); // 404 before anything else
  // Merge before checking, so changing only one half cannot produce an
  // incoherent pair.
  assertCapabilitiesCoherent({
    stageLayout: input.stageLayout ?? existing.stageLayout,
    allowedEventTypes: input.allowedEventTypes ?? existing.allowedEventTypes,
  });
  return prisma.venue.update({ where: { id }, data: compact(input), select: venueSelect });
}
```

Then widen the `select` in `getVenue` and `listVenues` to include the three new fields by adding them alongside `id`, `name`, `address`.

- [ ] **Step 8: Run the tests**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/venues.capabilities.test.ts`
Expected: PASS, 4 tests.

- [ ] **Step 9: Confirm nothing regressed**

Run: `cd apps/api && NODE_ENV=test npm test`
Expected: 89 passing, 0 failing.

- [ ] **Step 10: Commit**

```bash
npm run format
git add apps/api/prisma apps/api/src/modules/venues apps/api/tests/venues.capabilities.test.ts
git commit -m "$(cat <<'EOF'
Give venues admin-owned capabilities

A venue is now infrastructure with a stage layout, the event types it permits,
and a turnaround window, rather than a name and an address. Existing venues
migrate to END_STAGE allowing both types, which is exactly what they implicitly
were.

One validation earns its place: a CENTRE_STAGE venue may not allow MOVIE.
Nobody projects a film in the round, and refusing it at creation beats
discovering it when a cinema's seat map renders as a circle. updateVenue merges
before checking, so changing one half of the pair cannot produce an incoherent
venue.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Radial seat generation

**Files:**
- Modify: `apps/api/src/modules/venues/schema.ts`
- Modify: `apps/api/src/modules/venues/service.ts`
- Modify: `apps/api/tests/venues.capabilities.test.ts`

**Interfaces:**
- Consumes: `generateEndStageBlock`, `generateCentreStageBlock` from Task 2; `Venue.stageLayout` from Task 3
- Produces: `addSeatBlock` accepting optional `arcStartDegrees` and `arcSpanDegrees`

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/venues.capabilities.test.ts`, inside a new describe block at the end of the file:

```ts
describe('seat generation follows the venue layout', () => {
  test('an end-stage venue produces a grid', async () => {
    const { venue } = await json(
      await call('POST', '/venues', { name: tag('Grid'), address: 'x' }, admin),
    );
    const res = await call(
      'POST',
      `/venues/${venue.id}/seats`,
      { section: 'Stalls', rows: 2, seatsPerRow: 4 },
      admin,
    );
    assert.equal(res.status, 201);

    const seats = await prisma.seat.findMany({ where: { venueId: venue.id } });
    assert.equal(seats.length, 8);
    // A grid has exactly as many distinct posY values as it has rows.
    assert.equal(new Set(seats.map((s) => s.posY)).size, 2);
  });

  test('a centre-stage venue places every seat on its row radius', async () => {
    const { venue } = await json(
      await call(
        'POST',
        '/venues',
        {
          name: tag('Ring'),
          address: 'x',
          stageLayout: 'CENTRE_STAGE',
          allowedEventTypes: ['CONCERT'],
        },
        admin,
      ),
    );
    const res = await call(
      'POST',
      `/venues/${venue.id}/seats`,
      { section: 'Ring A', rows: 2, seatsPerRow: 8, arcStartDegrees: 0, arcSpanDegrees: 360 },
      admin,
    );
    assert.equal(res.status, 201);

    const seats = await prisma.seat.findMany({ where: { venueId: venue.id } });
    assert.equal(seats.length, 16);

    const radii = seats.map((s) => Number(Math.hypot(s.posX, s.posY).toFixed(6)));
    // Two rows means two distinct radii, and a ring is not a grid: many
    // distinct posY values rather than two.
    assert.equal(new Set(radii).size, 2);
    assert.ok(new Set(seats.map((s) => s.posY)).size > 2);
  });

  test('a second centre-stage block sits outside the first', async () => {
    const { venue } = await json(
      await call(
        'POST',
        '/venues',
        {
          name: tag('Rings'),
          address: 'x',
          stageLayout: 'CENTRE_STAGE',
          allowedEventTypes: ['CONCERT'],
        },
        admin,
      ),
    );
    await call(
      'POST',
      `/venues/${venue.id}/seats`,
      { section: 'Inner', rows: 1, seatsPerRow: 6 },
      admin,
    );
    await call(
      'POST',
      `/venues/${venue.id}/seats`,
      { section: 'Outer', rows: 1, seatsPerRow: 6 },
      admin,
    );

    const seats = await prisma.seat.findMany({ where: { venueId: venue.id } });
    const radiusOf = (section: string) =>
      Math.hypot(
        seats.find((s) => s.section === section)!.posX,
        seats.find((s) => s.section === section)!.posY,
      );
    assert.ok(radiusOf('Outer') > radiusOf('Inner'), 'the second ring must sit further out');
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/venues.capabilities.test.ts`
Expected: FAIL — the centre-stage venue still produces a grid, so the radii set has more than 2 entries.

- [ ] **Step 3: Accept the arc in the request schema**

In `apps/api/src/modules/venues/schema.ts`, replace `addSeatBlockSchema`:

```ts
/**
 * Bulk seat creation: one named section.
 *
 * Rows are labelled A, B, C… so 26 is the ceiling — past that the labels would
 * need a second letter and nothing in this project needs a 27-row section.
 * ponytail: if a venue ever does, switch to AA/AB here and nowhere else.
 *
 * The arc fields apply only to a CENTRE_STAGE venue and are ignored otherwise.
 * Defaulting to a full circle means a single-section ring needs no extra input;
 * four 90° blocks build a venue with four wedges.
 */
export const addSeatBlockSchema = z.object({
  section: z.string().trim().min(1).max(40),
  rows: z.number().int().min(1).max(26),
  seatsPerRow: z.number().int().min(1).max(60),
  arcStartDegrees: z.number().min(0).max(360).default(0),
  arcSpanDegrees: z.number().min(1).max(360).default(360),
});
```

- [ ] **Step 4: Branch on layout in the service**

In `apps/api/src/modules/venues/service.ts`, replace the whole of `addSeatBlock` and delete the now-unused local `ROW_LABELS` constant:

```ts
/**
 * Generates a block of seats using whichever layout the venue was built for.
 *
 * A new block is always placed outside or below everything already there, so
 * sections never overlap and the caller never computes an offset.
 */
export async function addSeatBlock(venueId: string, input: AddSeatBlockInput) {
  const venue = await getVenue(venueId);

  const positions =
    venue.stageLayout === 'CENTRE_STAGE'
      ? generateCentreStageBlock({
          rows: input.rows,
          seatsPerRow: input.seatsPerRow,
          startRadius: (await outermostRadius(venueId)) + 2,
          arcStartDegrees: input.arcStartDegrees,
          arcSpanDegrees: input.arcSpanDegrees,
        })
      : generateEndStageBlock({
          rows: input.rows,
          seatsPerRow: input.seatsPerRow,
          startY: (await lowestRow(venueId)) + 2,
        });

  const seats: Prisma.SeatCreateManyInput[] = positions.map((p) => ({
    venueId,
    section: input.section,
    row: p.row,
    number: p.number,
    posX: p.posX,
    posY: p.posY,
  }));

  try {
    const { count } = await prisma.seat.createMany({ data: seats });
    return { created: count, section: input.section, layout: venue.stageLayout };
  } catch (err) {
    // @@unique([venueId, section, row, number]) — re-adding the same block.
    if (err instanceof Prisma.PrismaClientKnownRequestError && err.code === 'P2002') {
      throw ApiError.conflict(
        'SEATS_ALREADY_EXIST',
        `Section "${input.section}" already has seats with those row and number labels.`,
      );
    }
    throw err;
  }
}

/** Lowest occupied grid row, or -2 so the first block starts at y = 0. */
async function lowestRow(venueId: string): Promise<number> {
  const { _max } = await prisma.seat.aggregate({ where: { venueId }, _max: { posY: true } });
  return _max.posY === null ? -2 : _max.posY;
}

/**
 * Radius of the outermost existing seat, or 1 so the first ring starts at 3 —
 * far enough out to leave room for the stage in the middle.
 */
async function outermostRadius(venueId: string): Promise<number> {
  const seats = await prisma.seat.findMany({
    where: { venueId },
    select: { posX: true, posY: true },
  });
  if (seats.length === 0) return 1;
  return Math.max(...seats.map((s) => Math.hypot(s.posX, s.posY)));
}
```

Add the import at the top of the file:

```ts
import { generateCentreStageBlock, generateEndStageBlock } from '../../lib/geometry.js';
```

- [ ] **Step 5: Run the tests**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/venues.capabilities.test.ts`
Expected: PASS, 7 tests.

- [ ] **Step 6: Confirm nothing regressed**

Run: `cd apps/api && NODE_ENV=test npm test`
Expected: 92 passing, 0 failing.

- [ ] **Step 7: Commit**

```bash
npm run format
git add apps/api/src/modules/venues apps/api/tests/venues.capabilities.test.ts
git commit -m "$(cat <<'EOF'
Generate radial seating for centre-stage venues

The venue builder now branches on the venue's stage layout, delegating both
cases to the pure geometry module. A centre-stage block places rows as radii
and spreads seats along an arc, defaulting to a full circle so a single-section
ring needs no extra input while four 90-degree blocks build four wedges.

Each new block is placed outside or below everything already there, so sections
never overlap and the caller never computes an offset — the same guarantee the
grid layout already made, expressed in polar terms.

Because both layouts emit plain posX/posY grid units, the seat map renderer
needs no change whatsoever.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Event-type gate

**Files:**
- Modify: `apps/api/src/modules/events/service.ts`
- Modify: `apps/api/tests/events.test.ts`

**Interfaces:**
- Consumes: `Venue.allowedEventTypes` from Task 3
- Produces: error code `EVENT_TYPE_NOT_ALLOWED`

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/events.test.ts`, inside the existing `describe('events and pricing', ...)` block:

```ts
  test('refuses an event type the venue does not permit', async () => {
    const concertOnly = await prisma.venue.create({
      data: {
        name: tag('ConcertOnly'),
        address: 'x',
        allowedEventTypes: ['CONCERT'],
      },
    });

    const res = await post(
      '/events',
      { venueId: concertOnly.id, title: tag('Film'), type: 'MOVIE' },
      organiserToken,
    );
    assert.equal(res.status, 400);
    const body = await json(res);
    assert.equal(body.error.code, 'EVENT_TYPE_NOT_ALLOWED');
    // The message must name what the venue does allow, or the organiser is
    // left guessing.
    assert.match(body.error.message, /CONCERT/);
  });
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/events.test.ts`
Expected: FAIL — got 201, expected 400.

- [ ] **Step 3: Add the gate**

In `apps/api/src/modules/events/service.ts`, replace `createEvent`:

```ts
export async function createEvent(input: CreateEventInput, caller: Caller) {
  const venue = await prisma.venue.findUnique({
    where: { id: input.venueId },
    select: { id: true, allowedEventTypes: true },
  });
  if (!venue) throw ApiError.badRequest('VENUE_NOT_FOUND', 'No venue with that id.');

  // A venue is admin-owned infrastructure; an organiser books it, and cannot
  // put a film in a room built for concerts.
  if (!venue.allowedEventTypes.includes(input.type)) {
    throw ApiError.badRequest(
      'EVENT_TYPE_NOT_ALLOWED',
      `This venue hosts ${venue.allowedEventTypes.join(' and ')} only.`,
    );
  }

  return prisma.event.create({
    data: { ...compact(input), organiserId: caller.sub },
    select: { id: true, title: true, type: true, description: true, venueId: true },
  });
}
```

- [ ] **Step 4: Run the tests**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/events.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
npm run format
git add apps/api/src/modules/events/service.ts apps/api/tests/events.test.ts
git commit -m "$(cat <<'EOF'
Refuse events a venue does not permit

An organiser books a venue rather than owning it, so a room an admin marked
concert-only cannot host a film. The error names what the venue does allow, so
the organiser is not left guessing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Show scheduling fields

**Files:**
- Modify: `apps/api/prisma/schema.prisma`
- Create: `apps/api/prisma/migrations/20260823100000_show_scheduling/migration.sql`
- Modify: `apps/api/src/modules/events/schema.ts`
- Create: `apps/api/src/modules/venues/scheduling.ts`

**Interfaces:**
- Consumes: `Venue.turnaroundMinutes` from Task 3
- Produces:
  - `Show.venueId`, `Show.durationMinutes`, `Show.endsAt`, `Show.occupiesUntil`, `Show.status`
  - `occupiedWindow(input: { startsAt: Date; durationMinutes: number; turnaroundMinutes: number }): { endsAt: Date; occupiesUntil: Date }`
  - `createShowSchema` requiring `durationMinutes`

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/concurrency/scheduling.test.ts` with just the window maths for now:

```ts
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { occupiedWindow } from '../../src/modules/venues/scheduling.js';

describe('occupied window', () => {
  test('runs to the end of the show plus the venue turnaround', () => {
    const startsAt = new Date('2026-09-01T18:00:00.000Z');
    const { endsAt, occupiesUntil } = occupiedWindow({
      startsAt,
      durationMinutes: 120,
      turnaroundMinutes: 15,
    });
    assert.equal(endsAt.toISOString(), '2026-09-01T20:00:00.000Z');
    assert.equal(occupiesUntil.toISOString(), '2026-09-01T20:15:00.000Z');
  });

  test('a zero turnaround frees the room the moment the show ends', () => {
    const { endsAt, occupiesUntil } = occupiedWindow({
      startsAt: new Date('2026-09-01T18:00:00.000Z'),
      durationMinutes: 90,
      turnaroundMinutes: 0,
    });
    assert.equal(endsAt.getTime(), occupiesUntil.getTime());
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/concurrency/scheduling.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the window maths**

Create `apps/api/src/modules/venues/scheduling.ts`:

```ts
/**
 * Venue availability.
 *
 * The window a show occupies is longer than the show: the room has to empty, be
 * cleaned, and be reset before anybody else can use it. Turnaround is a venue
 * property because a stadium needs longer than a screening room.
 */

export function occupiedWindow(input: {
  startsAt: Date;
  durationMinutes: number;
  turnaroundMinutes: number;
}): { endsAt: Date; occupiesUntil: Date } {
  const endsAt = new Date(input.startsAt.getTime() + input.durationMinutes * 60_000);
  const occupiesUntil = new Date(endsAt.getTime() + input.turnaroundMinutes * 60_000);
  return { endsAt, occupiesUntil };
}
```

- [ ] **Step 4: Run the test**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/concurrency/scheduling.test.ts`
Expected: PASS, 2 tests.

- [ ] **Step 5: Add the schema fields**

In `apps/api/prisma/schema.prisma`, add above `model Show`:

```prisma
enum ShowStatus {
  SCHEDULED
  CANCELLED
}
```

and replace `model Show` with:

```prisma
model Show {
  id      String @id @default(uuid())
  event   Event  @relation(fields: [eventId], references: [id])
  eventId String

  /// Denormalised from event.venue so the venue-overlap exclusion constraint —
  /// which can only span one table — has something to key on. Safe because
  /// Event.venueId is immutable: moving an event would orphan every ShowSeat
  /// generated against the old venue's seats.
  venueId String

  startsAt        DateTime
  /// Supplied by the organiser; there is no sensible default for "how long is
  /// this show".
  durationMinutes Int
  endsAt          DateTime
  /// endsAt plus the venue's turnaround. This, not endsAt, is what blocks the
  /// room for another organiser.
  occupiesUntil   DateTime
  status          ShowStatus @default(SCHEDULED)

  showSeats       ShowSeat[]
  waitlistEntries WaitlistEntry[]
  bookings        Booking[]

  @@index([venueId, startsAt])
}
```

- [ ] **Step 6: Write the migration**

Create `apps/api/prisma/migrations/20260823100000_show_scheduling/migration.sql`:

```sql
-- A show becomes a booking of a venue for a window of time, so two organisers
-- can no longer schedule overlapping shows in one room.
--
-- Columns are added nullable, backfilled, then made NOT NULL, so existing rows
-- survive. Existing shows get a 120-minute duration: there is no way to recover
-- the real value, and two hours is a defensible default for both a film and a
-- gig.

CREATE TYPE "ShowStatus" AS ENUM ('SCHEDULED', 'CANCELLED');

ALTER TABLE "Show"
  ADD COLUMN "venueId"         TEXT,
  ADD COLUMN "durationMinutes" INTEGER,
  ADD COLUMN "endsAt"          TIMESTAMP(3),
  ADD COLUMN "occupiesUntil"   TIMESTAMP(3),
  ADD COLUMN "status"          "ShowStatus" NOT NULL DEFAULT 'SCHEDULED';

UPDATE "Show" s
SET "venueId"         = e."venueId",
    "durationMinutes" = 120,
    "endsAt"          = s."startsAt" + INTERVAL '120 minutes',
    "occupiesUntil"   = s."startsAt" + INTERVAL '120 minutes' + (v."turnaroundMinutes" * INTERVAL '1 minute')
FROM "Event" e
JOIN "Venue" v ON v.id = e."venueId"
WHERE e.id = s."eventId";

ALTER TABLE "Show"
  ALTER COLUMN "venueId"         SET NOT NULL,
  ALTER COLUMN "durationMinutes" SET NOT NULL,
  ALTER COLUMN "endsAt"          SET NOT NULL,
  ALTER COLUMN "occupiesUntil"   SET NOT NULL;

CREATE INDEX "Show_venueId_startsAt_idx" ON "Show"("venueId", "startsAt");
```

- [ ] **Step 7: Apply to both databases**

Run:
```bash
cd apps/api && npm run db:migrate -- --name show_scheduling && npm run db:deploy:test
```
Expected: applied to both. If it fails, the backfill found a show whose event has no venue — investigate rather than forcing it.

- [ ] **Step 8: Require duration on show creation**

In `apps/api/src/modules/events/schema.ts`, replace `createShowSchema`:

```ts
export const createShowSchema = z.object({
  startsAt: z.coerce
    .date()
    .refine((d) => d.getTime() > Date.now(), 'Show must start in the future.'),
  // No default: only the organiser knows how long their show runs, and guessing
  // would silently block the wrong amount of venue time.
  durationMinutes: z
    .number()
    .int()
    .min(5, 'A show must run for at least 5 minutes.')
    .max(24 * 60, 'A show cannot run longer than a day.'),
});
```

- [ ] **Step 9: Confirm the type error appears**

Run: `cd apps/api && npm run typecheck`
Expected: FAIL in `events/service.ts` — `tx.show.create` is missing `venueId`, `durationMinutes`, `endsAt` and `occupiesUntil`. Task 7 fixes it.

- [ ] **Step 10: Commit**

```bash
npm run format
git add apps/api/prisma apps/api/src/modules/events/schema.ts apps/api/src/modules/venues/scheduling.ts apps/api/tests/concurrency/scheduling.test.ts
git commit -m "$(cat <<'EOF'
Model a show as a booking of a venue for a window of time

A show now carries its duration, its end, and the point at which the room
becomes free again — which is later than the end, because the room has to
empty, be cleaned and be reset. Turnaround is a venue property since a stadium
needs longer than a screening room.

venueId is denormalised onto Show so the exclusion constraint in the next
commit, which can only span one table, has something to key on. That is safe
because Event.venueId is already immutable: moving an event would orphan every
ShowSeat generated against the old venue's seats.

Existing rows are backfilled with a 120-minute duration. The real value is
unrecoverable and two hours is defensible for both a film and a gig; columns
are added nullable, backfilled, then made NOT NULL so nothing is lost.

Typecheck fails after this commit until show creation is updated — that is
expected and deliberate.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Prevent double-booking a venue

**Files:**
- Modify: `apps/api/src/modules/venues/scheduling.ts`
- Modify: `apps/api/src/modules/events/service.ts`
- Create: `apps/api/prisma/migrations/20260823110000_show_no_venue_overlap/migration.sql`
- Modify: `apps/api/tests/concurrency/scheduling.test.ts`
- Modify: `apps/api/prisma/seed.ts`

**Interfaces:**
- Consumes: `occupiedWindow` and the `Show` columns from Task 6
- Produces: error code `VENUE_DOUBLE_BOOKED`; `assertVenueFree(tx, input)` in `scheduling.ts`

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/concurrency/scheduling.test.ts` — this needs the full harness, so add the imports and setup at the top of the file below the existing imports:

```ts
import { randomBytes } from 'node:crypto';
import { after, before } from 'node:test';
import type { Server } from 'node:http';
import type express from 'express';
import { createApp } from '../../src/app.js';
import { prisma } from '../../src/lib/prisma.js';

const RUN = randomBytes(5).toString('hex');
const tag = (s: string) => `${s} ${RUN}`;
const emailFor = (who: string) => `sch-${who}-${RUN}@example.test`;
const PASSWORD = 'correct horse battery';

let server: Server;
let base: string;
let organiserA: string;
let organiserB: string;
let venueId: string;
let eventA: string;
let eventB: string;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const json = async (res: Response): Promise<any> => res.json();

const call = (method: string, path: string, body?: unknown, token?: string) =>
  fetch(base + '/api/v1' + path, {
    method,
    headers: {
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });

async function makeOrganiser(who: string) {
  const email = emailFor(who);
  await call('POST', '/auth/register', { email, password: PASSWORD, name: who });
  await prisma.user.update({ where: { email }, data: { role: 'ORGANISER' } });
  return (await json(await call('POST', '/auth/login', { email, password: PASSWORD })))
    .accessToken as string;
}

/** An event with every section priced, so show creation is not blocked by pricing. */
async function makePricedEvent(token: string, title: string) {
  const { event } = await json(
    await call('POST', '/events', { venueId, title: tag(title), type: 'CONCERT' }, token),
  );
  await call(
    'POST',
    `/events/${event.id}/categories`,
    { name: 'Floor', price: '100', sections: ['Floor'] },
    token,
  );
  return event.id as string;
}

before(async () => {
  const app = createApp();
  server = await new Promise<Server>((resolve) => {
    const s = (app as express.Express).listen(0, () => resolve(s));
  });
  const addr = server.address();
  if (typeof addr === 'string' || addr === null) throw new Error('no port');
  base = `http://127.0.0.1:${addr.port}`;

  organiserA = await makeOrganiser('a');
  organiserB = await makeOrganiser('b');

  const venue = await prisma.venue.create({
    data: {
      name: tag('Shared'),
      address: 'x',
      allowedEventTypes: ['CONCERT'],
      turnaroundMinutes: 15,
    },
  });
  venueId = venue.id;
  await prisma.seat.createMany({
    data: [1, 2, 3, 4].map((n) => ({
      venueId,
      section: 'Floor',
      row: 'A',
      number: n,
      posX: n,
      posY: 0,
    })),
  });

  eventA = await makePricedEvent(organiserA, 'A');
  eventB = await makePricedEvent(organiserB, 'B');
});

after(async () => {
  await prisma.showSeat.deleteMany({ where: { show: { venueId } } });
  await prisma.show.deleteMany({ where: { venueId } });
  await prisma.seatCategory.deleteMany({ where: { event: { venueId } } });
  await prisma.event.deleteMany({ where: { venueId } });
  await prisma.seat.deleteMany({ where: { venueId } });
  await prisma.venue.delete({ where: { id: venueId } });
  await prisma.user.deleteMany({ where: { email: { endsWith: `-${RUN}@example.test` } } });
  await prisma.$disconnect();
  server.close();
});
```

Then append these describe blocks at the end of the file:

```ts
/** Days out, so nothing collides with other suites' fixtures. */
const at = (dayOffset: number, hour: number) => {
  const d = new Date();
  d.setDate(d.getDate() + 30 + dayOffset);
  d.setUTCHours(hour, 0, 0, 0);
  return d.toISOString();
};

describe('a venue cannot be double booked', () => {
  test('a second overlapping show is refused', async () => {
    const first = await call(
      'POST',
      `/events/${eventA}/shows`,
      { startsAt: at(0, 18), durationMinutes: 120 },
      organiserA,
    );
    assert.equal(first.status, 201);

    // Starts an hour in, while the first show is still running.
    const clash = await call(
      'POST',
      `/events/${eventB}/shows`,
      { startsAt: at(0, 19), durationMinutes: 60 },
      organiserB,
    );
    assert.equal(clash.status, 409);
    assert.equal((await json(clash)).error.code, 'VENUE_DOUBLE_BOOKED');
  });

  test('a show starting inside the turnaround window is refused', async () => {
    await call(
      'POST',
      `/events/${eventA}/shows`,
      { startsAt: at(1, 18), durationMinutes: 60 },
      organiserA,
    );
    // Ends 19:00; turnaround runs to 19:15. Starting at 19:05 is too soon.
    const tooSoon = await call(
      'POST',
      `/events/${eventB}/shows`,
      { startsAt: at(1, 19), durationMinutes: 60 },
      organiserB,
    );
    // 19:00 start is exactly at the end but inside turnaround → refused.
    assert.equal(tooSoon.status, 409);
  });

  test('a show starting after the turnaround is accepted', async () => {
    await call(
      'POST',
      `/events/${eventA}/shows`,
      { startsAt: at(2, 18), durationMinutes: 60 },
      organiserA,
    );
    // Ends 19:00, free from 19:15. 20:00 is clear.
    const later = await call(
      'POST',
      `/events/${eventB}/shows`,
      { startsAt: at(2, 20), durationMinutes: 60 },
      organiserB,
    );
    assert.equal(later.status, 201);
  });

  test('two organisers booking the same slot simultaneously: exactly one wins', async () => {
    const slot = at(3, 18);
    const [a, b] = await Promise.all([
      call('POST', `/events/${eventA}/shows`, { startsAt: slot, durationMinutes: 90 }, organiserA),
      call('POST', `/events/${eventB}/shows`, { startsAt: slot, durationMinutes: 90 }, organiserB),
    ]);

    const codes = [a.status, b.status].sort();
    assert.deepEqual(codes, [201, 409], `expected one 201 and one 409, got ${codes.join(',')}`);

    const count = await prisma.show.count({
      where: { venueId, startsAt: new Date(slot), status: 'SCHEDULED' },
    });
    assert.equal(count, 1, 'two shows were scheduled in one venue at one time');
  });

  test('the database refuses an overlap even when the application is bypassed', async () => {
    const slot = new Date(at(4, 18));
    await call(
      'POST',
      `/events/${eventA}/shows`,
      { startsAt: slot.toISOString(), durationMinutes: 60 },
      organiserA,
    );

    // Written straight to the table — no service, no validation.
    await assert.rejects(
      prisma.show.create({
        data: {
          eventId: eventB,
          venueId,
          startsAt: slot,
          durationMinutes: 60,
          endsAt: new Date(slot.getTime() + 60 * 60_000),
          occupiesUntil: new Date(slot.getTime() + 75 * 60_000),
        },
      }),
      'the exclusion constraint should have refused this',
    );
  });

  test('cancelling a show frees its slot', async () => {
    const slot = at(5, 18);
    const { show } = await json(
      await call(
        'POST',
        `/events/${eventA}/shows`,
        { startsAt: slot, durationMinutes: 60 },
        organiserA,
      ),
    );

    // Blocked while scheduled.
    const blocked = await call(
      'POST',
      `/events/${eventB}/shows`,
      { startsAt: slot, durationMinutes: 60 },
      organiserB,
    );
    assert.equal(blocked.status, 409);

    await prisma.show.update({ where: { id: show.id }, data: { status: 'CANCELLED' } });

    // The constraint is partial on status, so the slot frees with no cleanup.
    const freed = await call(
      'POST',
      `/events/${eventB}/shows`,
      { startsAt: slot, durationMinutes: 60 },
      organiserB,
    );
    assert.equal(freed.status, 201);
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/concurrency/scheduling.test.ts`
Expected: FAIL — overlapping shows are currently both created.

- [ ] **Step 3: Write the exclusion-constraint migration**

Create `apps/api/prisma/migrations/20260823110000_show_no_venue_overlap/migration.sql`:

```sql
-- The guarantee that survives an application bug: no two SCHEDULED shows may
-- occupy one venue at overlapping times.
--
-- Prisma cannot express an exclusion constraint, so this is hand-written and
-- invisible to schema.prisma — a future `migrate dev` may report it as drift
-- and try to drop it. Recorded in docs/DEBUGGING.md alongside
-- BookingSeat_showSeatId_live_key, which has the same problem.

-- Equality on a text column inside a GiST exclusion constraint needs this.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- WHERE status = 'SCHEDULED' is the elegant part: a cancelled show stops
-- blocking its slot automatically, with no cleanup code anywhere. Same house
-- style as the BookingSeat seatbelt — guard the live rows, let the dead ones
-- stay for history.
ALTER TABLE "Show" ADD CONSTRAINT "show_no_venue_overlap"
  EXCLUDE USING gist (
    "venueId"                             WITH =,
    tsrange("startsAt", "occupiesUntil")  WITH &&
  ) WHERE (status = 'SCHEDULED');
```

Note: `tsrange`, not `tstzrange` — Prisma maps `DateTime` to `timestamp(3)` without a time zone, so the range type must match the column type.

- [ ] **Step 4: Apply to both databases**

Run:
```bash
cd apps/api && npx prisma migrate deploy && npm run db:deploy:test
```
Expected: applied. If it errors with `conflicting key value violates exclusion constraint`, existing seeded shows already overlap — inspect them, cancel or move one, and re-run. **Do not weaken the constraint to make it apply.**

- [ ] **Step 5: Add the application-level check**

Append to `apps/api/src/modules/venues/scheduling.ts`:

```ts
import type { Prisma } from '@prisma/client';
import { ApiError } from '../../lib/errors.js';

/**
 * Refuses to schedule a show that overlaps another in the same venue.
 *
 * Runs inside the caller's transaction, locking the venue's scheduled shows
 * first so two simultaneous organisers serialise here rather than both passing
 * the check and racing to insert.
 *
 * The exclusion constraint underneath is the real guarantee; this exists to
 * turn a database error into a message that names the clashing show.
 */
export async function assertVenueFree(
  tx: Prisma.TransactionClient,
  input: { venueId: string; startsAt: Date; occupiesUntil: Date },
) {
  const clashes = await tx.$queryRaw<{ id: string; startsAt: Date; occupiesUntil: Date }[]>`
    SELECT id, "startsAt", "occupiesUntil"
    FROM "Show"
    WHERE "venueId" = ${input.venueId}
      AND status = 'SCHEDULED'
      AND "startsAt" < ${input.occupiesUntil}
      AND "occupiesUntil" > ${input.startsAt}
    ORDER BY "startsAt"
    FOR UPDATE`;

  const clash = clashes[0];
  if (clash) {
    throw ApiError.conflict(
      'VENUE_DOUBLE_BOOKED',
      `This venue is already booked from ${clash.startsAt.toISOString()} until ${clash.occupiesUntil.toISOString()}, including turnaround.`,
    );
  }
}
```

- [ ] **Step 6: Use it in show creation**

In `apps/api/src/modules/events/service.ts`, replace `createShow`:

```ts
export async function createShow(eventId: string, input: CreateShowInput, caller: Caller) {
  const event = await assertOwns(eventId, caller);

  const venue = await prisma.venue.findUniqueOrThrow({
    where: { id: event.venueId },
    select: { turnaroundMinutes: true },
  });

  const { endsAt, occupiesUntil } = occupiedWindow({
    startsAt: input.startsAt,
    durationMinutes: input.durationMinutes,
    turnaroundMinutes: venue.turnaroundMinutes,
  });

  // One transaction: a show whose seats failed to generate is worse than no
  // show at all — it renders as a bookable date with an empty seat map.
  return prisma.$transaction(
    async (tx) => {
      await assertVenueFree(tx, { venueId: event.venueId, startsAt: input.startsAt, occupiesUntil });

      const show = await tx.show.create({
        data: {
          eventId,
          venueId: event.venueId,
          startsAt: input.startsAt,
          durationMinutes: input.durationMinutes,
          endsAt,
          occupiesUntil,
        },
        select: { id: true, startsAt: true, endsAt: true, occupiesUntil: true },
      });

      const seatCount = await instantiateShowSeats(tx, {
        showId: show.id,
        eventId,
        venueId: event.venueId,
      });

      return { ...show, seatCount };
    },
    { maxWait: 15_000, timeout: 20_000 },
  );
}
```

Add the import at the top of the file:

```ts
import { assertVenueFree, occupiedWindow } from '../venues/scheduling.js';
```

- [ ] **Step 7: Update the seed to supply durations**

In `apps/api/prisma/seed.ts`, inside the show-creation loop, replace the `tx.show.create` call:

```ts
      const { endsAt, occupiesUntil } = occupiedWindow({
        startsAt,
        durationMinutes: 169, // Interstellar's actual runtime, because why not
        turnaroundMinutes: 15,
      });
      const show = await tx.show.create({
        data: {
          eventId: event.id,
          venueId: venue.id,
          startsAt,
          durationMinutes: 169,
          endsAt,
          occupiesUntil,
        },
      });
```

and add at the top of the file:

```ts
import { occupiedWindow } from '../src/modules/venues/scheduling.js';
```

- [ ] **Step 8: Run the scheduling tests**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/concurrency/scheduling.test.ts`
Expected: PASS, 8 tests.

- [ ] **Step 9: Run everything, including the seat concurrency guard**

Run: `cd apps/api && NODE_ENV=test npm test`
Expected: 101 passing, 0 failing. **`concurrency: one seat, twenty simultaneous customers` must still be green.**

- [ ] **Step 10: Record the constraint in the debugging log**

In `docs/DEBUGGING.md`, immediately after the `### A migration silently drops the booking seatbelt` section, add:

```markdown
### A migration silently drops the venue overlap constraint

**Symptom:** after a `prisma migrate dev`, two shows can be scheduled in one
venue at overlapping times.
**Cause:** `show_no_venue_overlap` is a GiST **exclusion constraint**. Prisma
cannot represent one, so it does not appear in `schema.prisma` and migrate
treats it as drift to be removed.
**Fix:** re-add it in the same migration:

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;
ALTER TABLE "Show" ADD CONSTRAINT "show_no_venue_overlap"
  EXCLUDE USING gist (
    "venueId"                            WITH =,
    tsrange("startsAt", "occupiesUntil") WITH &&
  ) WHERE (status = 'SCHEDULED');
```

Note `tsrange`, not `tstzrange` — Prisma maps `DateTime` to `timestamp(3)`
without a time zone, and the range type must match the column type.
```

- [ ] **Step 11: Commit**

```bash
npm run format
git add apps/api docs/DEBUGGING.md
git commit -m "$(cat <<'EOF'
Stop two organisers double-booking a venue

Show stored only startsAt, so overlapping shows in one hall both succeeded.
Invisible while an organiser implicitly owned a venue; a real defect now they
are tenants sharing one.

Two layers, as everywhere else in this codebase. assertVenueFree runs inside
the show-creation transaction and locks the venue's scheduled shows first, so
two simultaneous organisers serialise there rather than both passing the check
and racing to insert; it exists to turn a database error into a message naming
the clashing show and when the room actually frees. Underneath, a Postgres GiST
exclusion constraint provides the guarantee that survives an application bug —
tested by writing an overlap straight to the table, bypassing the service
entirely.

The constraint is partial on status, which is the elegant part: cancelling a
show stops it blocking its slot with no cleanup code anywhere. Same house style
as BookingSeat_showSeatId_live_key — guard the live rows, let the dead ones stay
for history. Both are invisible to schema.prisma, so both are now recorded in
DEBUGGING.md as drift a future migrate dev may try to drop.

tsrange rather than tstzrange, because Prisma maps DateTime to timestamp(3)
without a time zone and the range type must match the column.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Two-clock hold expiry

**Files:**
- Modify: `apps/api/src/env.ts`
- Modify: `apps/api/.env.example`
- Modify: `apps/api/src/modules/seats/service.ts`
- Modify: `apps/api/src/modules/seats/routes.ts`
- Create: `apps/api/tests/holds.grace.test.ts`

**Interfaces:**
- Consumes: nothing new
- Produces:
  - `env.RELEASE_GRACE_SECONDS: number`
  - `releaseHolds(showId, userId)` now returns `{ released: number; freeAt: string }` and *shortens* rather than deletes
  - `extendHold(showId, userId): Promise<{ holdExpiresAt: string }>`
  - Route `POST /shows/:id/holds/extend`

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/holds.grace.test.ts`:

```ts
import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { after, before, describe, test } from 'node:test';
import type { Server } from 'node:http';
import type express from 'express';
import { createApp } from '../src/app.js';
import { prisma } from '../src/lib/prisma.js';
import { env } from '../src/env.js';

const RUN = randomBytes(5).toString('hex');
const tag = (s: string) => `${s} ${RUN}`;
const emailFor = (who: string) => `hg-${who}-${RUN}@example.test`;
const PASSWORD = 'correct horse battery';

let server: Server;
let base: string;
let showId: string;
let seatIds: string[];
let alice: string;
let bob: string;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const json = async (res: Response): Promise<any> => res.json();

const call = (method: string, path: string, body?: unknown, token?: string) =>
  fetch(base + '/api/v1' + path, {
    method,
    headers: {
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });

const register = async (who: string) =>
  (
    await json(
      await call('POST', '/auth/register', {
        email: emailFor(who),
        password: PASSWORD,
        name: who,
      }),
    )
  ).accessToken as string;

before(async () => {
  const app = createApp();
  server = await new Promise<Server>((resolve) => {
    const s = (app as express.Express).listen(0, () => resolve(s));
  });
  const addr = server.address();
  if (typeof addr === 'string' || addr === null) throw new Error('no port');
  base = `http://127.0.0.1:${addr.port}`;

  alice = await register('alice');
  bob = await register('bob');

  const organiser = await prisma.user.create({
    data: { email: emailFor('org'), name: 'Org', role: 'ORGANISER', passwordHash: 'unused' },
  });
  const venue = await prisma.venue.create({ data: { name: tag('Grace'), address: 'x' } });
  await prisma.seat.createMany({
    data: [1, 2, 3].map((n) => ({
      venueId: venue.id,
      section: 'Main',
      row: 'A',
      number: n,
      posX: n,
      posY: 0,
    })),
  });
  const event = await prisma.event.create({
    data: { organiserId: organiser.id, venueId: venue.id, title: tag('Grace'), type: 'CONCERT' },
  });
  const category = await prisma.seatCategory.create({
    data: { eventId: event.id, name: 'Main', price: '100', sections: ['Main'] },
  });
  const startsAt = new Date(Date.now() + 40 * 86_400_000);
  const show = await prisma.show.create({
    data: {
      eventId: event.id,
      venueId: venue.id,
      startsAt,
      durationMinutes: 60,
      endsAt: new Date(startsAt.getTime() + 60 * 60_000),
      occupiesUntil: new Date(startsAt.getTime() + 75 * 60_000),
    },
  });
  showId = show.id;
  const seats = await prisma.seat.findMany({ where: { venueId: venue.id } });
  await prisma.showSeat.createMany({
    data: seats.map((s) => ({ showId: show.id, seatId: s.id, categoryId: category.id })),
  });
  seatIds = (await prisma.showSeat.findMany({ where: { showId }, select: { id: true } })).map(
    (r) => r.id,
  );
});

after(async () => {
  await prisma.showSeat.deleteMany({ where: { show: { event: { title: { contains: RUN } } } } });
  await prisma.show.deleteMany({ where: { event: { title: { contains: RUN } } } });
  await prisma.seatCategory.deleteMany({ where: { event: { title: { contains: RUN } } } });
  await prisma.event.deleteMany({ where: { title: { contains: RUN } } });
  await prisma.seat.deleteMany({ where: { venue: { name: { contains: RUN } } } });
  await prisma.venue.deleteMany({ where: { name: { contains: RUN } } });
  await prisma.user.deleteMany({ where: { email: { endsWith: `-${RUN}@example.test` } } });
  await prisma.$disconnect();
  server.close();
});

describe('two clocks', () => {
  test('an abandoned hold runs for the full TTL', async () => {
    const seat = seatIds[0]!;
    const res = await call('POST', `/shows/${showId}/holds`, { seatIds: [seat] }, alice);
    assert.equal(res.status, 201);

    const { holdExpiresAt } = await json(res);
    const seconds = (new Date(holdExpiresAt).getTime() - Date.now()) / 1000;
    assert.ok(
      Math.abs(seconds - env.HOLD_TTL_SECONDS) < 10,
      `expected ~${env.HOLD_TTL_SECONDS}s, got ${seconds}s`,
    );
  });

  test('going back shortens the hold instead of deleting it', async () => {
    const seat = seatIds[1]!;
    await call('POST', `/shows/${showId}/holds`, { seatIds: [seat] }, alice);

    const res = await call('DELETE', `/shows/${showId}/holds`, undefined, alice);
    assert.equal(res.status, 200);
    const body = await json(res);
    assert.equal(body.released, 1);

    const row = await prisma.showSeat.findUniqueOrThrow({
      where: { id: seat },
      select: { status: true, heldByUserId: true, holdExpiresAt: true },
    });
    // Still HELD, still owned — just on a much shorter clock.
    assert.equal(row.status, 'HELD');
    assert.ok(row.heldByUserId, 'the owner is kept so returning can restore the hold');

    const seconds = (row.holdExpiresAt!.getTime() - Date.now()) / 1000;
    assert.ok(
      seconds > 0 && seconds <= env.RELEASE_GRACE_SECONDS + 2,
      `expected <= ${env.RELEASE_GRACE_SECONDS}s, got ${seconds}s`,
    );
  });

  test('after the grace elapses another customer can take the seat', async () => {
    const seat = seatIds[2]!;
    await call('POST', `/shows/${showId}/holds`, { seatIds: [seat] }, alice);
    await call('DELETE', `/shows/${showId}/holds`, undefined, alice);

    // Wind the clock past the grace rather than waiting it out.
    await prisma.showSeat.update({
      where: { id: seat },
      data: { holdExpiresAt: new Date(Date.now() - 1000) },
    });

    // No sweeper run — lazy expiry alone must make it bookable.
    const res = await call('POST', `/shows/${showId}/holds`, { seatIds: [seat] }, bob);
    assert.equal(res.status, 201);
  });

  test('returning within the grace window restores the full TTL', async () => {
    const seat = seatIds[0]!;
    await call('DELETE', `/shows/${showId}/holds`, undefined, alice);
    await call('POST', `/shows/${showId}/holds`, { seatIds: [seat] }, alice);
    await call('DELETE', `/shows/${showId}/holds`, undefined, alice);

    const res = await call('POST', `/shows/${showId}/holds/extend`, undefined, alice);
    assert.equal(res.status, 200);

    const { holdExpiresAt } = await json(res);
    const seconds = (new Date(holdExpiresAt).getTime() - Date.now()) / 1000;
    assert.ok(seconds > env.RELEASE_GRACE_SECONDS + 10, `expected a restored TTL, got ${seconds}s`);
  });

  test('extending is refused when there is nothing held', async () => {
    const res = await call('POST', `/shows/${showId}/holds/extend`, undefined, bob);
    assert.equal(res.status, 409);
    assert.equal((await json(res)).error.code, 'NO_ACTIVE_HOLD');
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/holds.grace.test.ts`
Expected: FAIL — `RELEASE_GRACE_SECONDS` does not exist on `env`.

- [ ] **Step 3: Add the config**

In `apps/api/src/env.ts`, change the `HOLD_TTL_SECONDS` line and add the grace below it:

```ts
  // Five minutes. Long enough to fill in details without rushing; short enough
  // that an abandoned checkout does not hold a seat all afternoon.
  HOLD_TTL_SECONDS: seconds(300),
  // How long seats linger after an explicit "back". Not zero, so bouncing back
  // and forward does not cost a customer their seats to somebody faster.
  RELEASE_GRACE_SECONDS: seconds(15),
  OFFER_TTL_SECONDS: seconds(600),
```

In `apps/api/.env.example`, replace the `HOLD_TTL_SECONDS=600` line:

```
HOLD_TTL_SECONDS=300
RELEASE_GRACE_SECONDS=15
```

- [ ] **Step 4: Make release a grace release**

In `apps/api/src/modules/seats/service.ts`, replace `releaseHolds` entirely:

```ts
/**
 * Explicit "back" or "cancel" from checkout.
 *
 * Shortens the hold rather than deleting it. The seat becomes bookable by
 * anybody else after RELEASE_GRACE_SECONDS — effectiveStatus() enforces that
 * exactly, with no sweeper involved — but the owner is kept, so a customer who
 * bounces back and forward can reclaim it with extendHold() instead of losing
 * their seats to somebody faster.
 *
 * A deleted hold would make that impossible, and would also mean a
 * mis-clicked Back button is irreversible.
 */
export async function releaseHolds(showId: string, userId: string) {
  const freeAt = new Date(Date.now() + env.RELEASE_GRACE_SECONDS * 1000);

  const held = await prisma.showSeat.findMany({
    // Scoped to this user's own holds. Without heldByUserId in the where
    // clause this endpoint would free anyone's seats.
    where: { showId, heldByUserId: userId, status: 'HELD' },
    select: { id: true },
  });
  if (held.length === 0) return { released: 0, freeAt: freeAt.toISOString() };

  const ids = held.map((s) => s.id);
  await prisma.showSeat.updateMany({
    where: { id: { in: ids } },
    data: { holdExpiresAt: freeAt },
  });

  // Others should see them free the moment the grace elapses, so broadcast the
  // status they will have — not the status they have right now.
  setTimeout(
    () => broadcastStatus(showId, ids, 'AVAILABLE'),
    env.RELEASE_GRACE_SECONDS * 1000,
  ).unref();

  return { released: ids.length, freeAt: freeAt.toISOString() };
}

/**
 * Restores a shortened hold to the full TTL.
 *
 * Only touches seats this caller still holds and whose clock has not run out,
 * so it can never resurrect a seat somebody else has taken in the meantime.
 */
export async function extendHold(showId: string, userId: string) {
  const expiresAt = new Date(Date.now() + env.HOLD_TTL_SECONDS * 1000);

  const { count } = await prisma.showSeat.updateMany({
    where: {
      showId,
      heldByUserId: userId,
      status: 'HELD',
      holdExpiresAt: { gt: new Date() },
    },
    data: { holdExpiresAt: expiresAt },
  });

  if (count === 0) {
    throw ApiError.conflict(
      'NO_ACTIVE_HOLD',
      'Your hold has already expired. Pick your seats again.',
    );
  }

  return { holdExpiresAt: expiresAt.toISOString(), seats: count };
}
```

- [ ] **Step 5: Add the extend route**

In `apps/api/src/modules/seats/routes.ts`, after the existing `seatShowRoutes.delete` block:

```ts
seatShowRoutes.post('/:id/holds/extend', requireAuth, async (req, res) => {
  res.json(await service.extendHold(param(req, 'id'), req.user!.sub));
});
```

- [ ] **Step 6: Run the tests**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/holds.grace.test.ts`
Expected: PASS, 5 tests.

- [ ] **Step 7: Run everything**

Run: `cd apps/api && NODE_ENV=test npm test`
Expected: 106 passing, 0 failing. The booking suite's release test still passes because the seat is still freed — just after the grace window.

- [ ] **Step 8: Commit**

```bash
npm run format
git add apps/api/src apps/api/.env.example apps/api/tests/holds.grace.test.ts
git commit -m "$(cat <<'EOF'
Give holds two clocks: five minutes abandoned, fifteen seconds on back

An abandoned checkout and a deliberate "back" are different events and deserve
different treatment. Abandonment keeps the full TTL because the customer may
return; an explicit back shortens the hold to fifteen seconds because they have
decided — but not to zero, so bouncing back and forward does not cost them
their seats to somebody faster.

The implementation adds no mechanism. Release shortens holdExpiresAt rather
than deleting the hold, so effectiveStatus makes the seat bookable at exactly
fifteen seconds with no sweeper involved, while the owner is kept so extendHold
can restore the full TTL if the customer comes back. A deleted hold would make
that impossible and would make a mis-clicked Back irreversible.

extendHold only touches seats the caller still holds on an unexpired clock, so
it can never resurrect a seat somebody else has taken in the meantime.

HOLD_TTL_SECONDS drops from 600 to 300.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Three-page booking flow

**Files:**
- Create: `apps/web/src/pages/CheckoutPage.tsx`
- Create: `apps/web/src/pages/checkout.css`
- Modify: `apps/web/src/pages/ShowPage.tsx`
- Modify: `apps/web/src/main.tsx`

**Interfaces:**
- Consumes: `POST /shows/:id/holds`, `POST /shows/:id/holds/extend`, `DELETE /shows/:id/holds`, `POST /bookings`
- Produces: route `/shows/:id/checkout`

**Design note for the implementer:** page 1 must not lock. Clicking a seat is browsing; locking on browse means one undecided person freezes a row for everybody else. The lock is acquired by **Continue**, and only then.

- [ ] **Step 1: Create the checkout page**

Create `apps/web/src/pages/CheckoutPage.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import type { SeatView } from '@ticket/shared';
import { api } from '../lib/api.js';
import { messageFor, useAuth } from '../auth/AuthContext.js';
import { useAsync } from '../lib/useAsync.js';
import { formatPrice, formatShowDate, formatShowTime } from '../lib/format.js';
import { Alert, Button, Card, Skeleton } from '../components/ui.js';
import { HoldCountdown } from '../components/HoldCountdown.js';
import './checkout.css';

type ShowDetail = {
  id: string;
  startsAt: string;
  event: { id: string; title: string; venue: { name: string } };
};

/**
 * Page 2 of 3. The seats are already held by the time this renders — Continue
 * on page 1 acquired the lock.
 *
 * Leaving does not delete the hold, it shortens it to a grace window, so a
 * customer who bounces back and forward can reclaim their seats rather than
 * losing them to somebody faster.
 */
export function CheckoutPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const navigate = useNavigate();

  const show = useAsync(() => api.get<{ show: ShowDetail }>(`/api/v1/shows/${id}`), [id]);
  const seats = useAsync(() => api.get<{ seats: SeatView[] }>(`/api/v1/shows/${id}/seats`), [id]);

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const mine = (seats.data?.seats ?? []).filter((s) => s.heldByMe);
  const total = mine.reduce((sum, s) => sum + Number(s.price), 0);
  const expiresAt = mine.find((s) => s.holdExpiresAt)?.holdExpiresAt ?? null;

  // Arriving here with nothing held means the hold lapsed, or the URL was
  // opened directly. Send them back rather than showing an empty checkout.
  useEffect(() => {
    if (!seats.loading && seats.data && mine.length === 0) {
      navigate(`/shows/${id}`, { replace: true });
    }
  }, [seats.loading, seats.data, mine.length, navigate, id]);

  // ponytail: no unload handler. sendBeacon cannot send an Authorization
  // header, so a beacon release would need an unauthenticated endpoint that
  // frees seats — a worse problem than the one it solves. A closed tab is
  // handled by the five-minute TTL, which is exactly why lazy expiry exists:
  // the client is an optimisation, the server's clock is the truth.

  const goBack = useCallback(async () => {
    setBusy(true);
    try {
      await api.del(`/api/v1/shows/${id}/holds`);
    } catch {
      // Even if this fails the hold expires on its own; never block the exit.
    } finally {
      navigate(`/shows/${id}`);
    }
  }, [id, navigate]);

  async function confirm() {
    if (!user) {
      navigate('/login', { state: { from: { pathname: `/shows/${id}/checkout` } } });
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const { booking } = await api.post<{ booking: { id: string } }>('/api/v1/bookings', {
        showId: id,
        seatIds: mine.map((s) => s.id),
      });
      navigate(`/bookings/${booking.id}`);
    } catch (err) {
      setError(messageFor(err));
      seats.reload();
    } finally {
      setBusy(false);
    }
  }

  if (show.loading || seats.loading) return <Skeleton count={1} height={320} />;
  if (show.error) return <Alert>{show.error}</Alert>;
  if (!show.data) return null;

  const detail = show.data.show;

  return (
    <div className="checkout">
      <nav aria-label="Breadcrumb" className="detail__crumbs">
        <Link to={`/events/${detail.event.id}`}>{detail.event.title}</Link>
        <span aria-hidden="true">/</span>
        <Link to={`/shows/${id}`}>Seats</Link>
        <span aria-hidden="true">/</span>
        <span aria-current="page">Checkout</span>
      </nav>

      <ol className="steps" aria-label="Booking progress">
        <li>Choose seats</li>
        <li aria-current="step">Checkout</li>
        <li>Ticket</li>
      </ol>

      <Card className="checkout__card">
        <h1 className="checkout__title">{detail.event.title}</h1>
        <p className="checkout__meta">
          {formatShowDate(detail.startsAt)} at {formatShowTime(detail.startsAt)} ·{' '}
          {detail.event.venue.name}
        </p>

        {expiresAt && (
          <p className="checkout__timer">
            Your seats are held for{' '}
            <HoldCountdown expiresAt={expiresAt} onExpire={() => navigate(`/shows/${id}`)} />
          </p>
        )}

        {error && <Alert>{error}</Alert>}

        <ul className="checkout__seats">
          {mine.map((s) => (
            <li key={s.id}>
              <span>
                {s.row}
                {s.number} · {s.categoryName}
              </span>
              <span>{formatPrice(s.price)}</span>
            </li>
          ))}
        </ul>

        <p className="checkout__total">
          <span>Total</span>
          <strong>{formatPrice(total)}</strong>
        </p>

        <Button variant="cta" full loading={busy} onClick={confirm}>
          {user ? 'Confirm booking' : 'Log in to confirm'}
        </Button>
        <Button variant="quiet" full disabled={busy} onClick={goBack}>
          Back to seats
        </Button>
        <p className="checkout__note">
          Going back keeps your seats for a few more seconds in case you change your mind.
        </p>
      </Card>
    </div>
  );
}
```

- [ ] **Step 2: Add its styles**

Create `apps/web/src/pages/checkout.css`:

```css
.checkout {
  display: grid;
  gap: var(--space-4);
  max-width: 30rem;
  margin: 0 auto;
}

.checkout__card {
  display: grid;
  gap: var(--space-3);
  padding: var(--space-5);
}

.checkout__title {
  font-family: var(--font-brand);
  font-weight: 400;
  font-size: var(--text-xl);
}

.checkout__meta,
.checkout__note,
.checkout__timer {
  font-size: var(--text-sm);
  color: var(--text-muted);
}

.checkout__seats {
  display: grid;
  gap: var(--space-2);
  list-style: none;
  margin: 0;
  padding: var(--space-3) 0;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
}
.checkout__seats li {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
}

.checkout__total {
  display: flex;
  justify-content: space-between;
  font-variant-numeric: tabular-nums;
}

/* Progress. Numbers come from a counter so the markup stays a plain list and
   the current step is announced by aria-current rather than by colour. */
.steps {
  display: flex;
  gap: var(--space-4);
  list-style: none;
  margin: 0;
  padding: 0;
  counter-reset: step;
  font-size: var(--text-xs);
  color: var(--text-muted);
}
.steps li {
  counter-increment: step;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.steps li::before {
  content: counter(step);
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 1px solid var(--border-strong);
  font-variant-numeric: tabular-nums;
}
.steps li[aria-current='step'] {
  color: var(--text);
  font-weight: 600;
}
.steps li[aria-current='step']::before {
  background: var(--brand);
  border-color: var(--brand);
  color: var(--brand-ink);
}
```

- [ ] **Step 3: Make Continue navigate instead of holding in place**

In `apps/web/src/pages/ShowPage.tsx`, replace the body of `placeHold` so it navigates on success, and rename the button label. Replace the whole `placeHold` function with:

```tsx
  async function placeHold() {
    if (!user) {
      navigate('/login', { state: { from: { pathname: `/shows/${id}` } } });
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await api.post(`/api/v1/shows/${id}/holds`, { seatIds: [...selected] });
      // Page 2. The lock is acquired here and nowhere earlier — clicking a seat
      // is browsing, and locking on browse freezes a row for everybody else.
      navigate(`/shows/${id}/checkout`);
    } catch (err) {
      setError(messageFor(err));
      reloadSeats();
    } finally {
      setBusy(false);
    }
  }
```

Then change the CTA label from `'Hold these seats'` to `'Continue'`, and the logged-out label to `'Log in to continue'`.

- [ ] **Step 4: Register the route**

In `apps/web/src/main.tsx`, add the import beside the other page imports:

```tsx
import { CheckoutPage } from './pages/CheckoutPage.js';
```

and the route immediately after the `/shows/:id` route:

```tsx
            <Route
              path="/shows/:id/checkout"
              element={
                <RequireAuth>
                  <CheckoutPage />
                </RequireAuth>
              }
            />
```

- [ ] **Step 5: Typecheck and build**

Run: `npm run typecheck && npm run build -w apps/web`
Expected: 0 type errors; the build succeeds.

- [ ] **Step 6: Walk the flow by hand**

Run: `npm run dev`

Then, in the browser:
1. Open a show, select two seats. **Confirm in the network tab that no request fires** — selection must not lock.
2. Press Continue → one `POST /holds` → you land on `/shows/:id/checkout` with a countdown.
3. Press Back to seats → `DELETE /holds` → the seats show as still yours briefly, then free.
4. Select again, Continue, Confirm booking → the ticket page with a QR.

- [ ] **Step 7: Commit**

```bash
npm run format
git add apps/web/src
git commit -m "$(cat <<'EOF'
Split booking into three pages, locking only on Continue

Choosing seats, paying, and holding a ticket are three different activities and
now three different pages. The lock moves to Continue: clicking a seat is
browsing, and locking on browse means one undecided person freezes a row for
everybody else.

Leaving checkout does not abandon the seats outright — it shortens the hold to
the grace window, and the copy says so, because a customer who presses Back by
mistake should not lose their seats to somebody faster.

A closed tab is handled best-effort with sendBeacon on pagehide, and the
five-minute TTL is the backstop when that fails. That is precisely why lazy
expiry exists: the client is an optimisation, the server's clock is the truth.

Arriving at checkout with nothing held — a lapsed hold, or the URL opened
directly — redirects back to the seat map rather than rendering an empty basket.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Section pricing with seat counts

**Files:**
- Modify: `apps/api/src/modules/venues/service.ts`
- Modify: `apps/web/src/pages/OrganiserPage.tsx`
- Modify: `apps/web/src/pages/manage.css`

The route needs no change: `venueRoutes.get('/:id/sections')` already wraps
whatever `listSections()` returns in `{ sections }`.

**Interfaces:**
- Consumes: nothing new
- Produces: `GET /venues/:id/sections` returns `{ sections: { name: string; seatCount: number }[] }`

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/venues.capabilities.test.ts`, inside the `describe('seat generation follows the venue layout', ...)` block:

```ts
  test('sections are reported with their seat counts', async () => {
    const { venue } = await json(
      await call('POST', '/venues', { name: tag('Counted'), address: 'x' }, admin),
    );
    await call(
      'POST',
      `/venues/${venue.id}/seats`,
      { section: 'Front', rows: 2, seatsPerRow: 5 },
      admin,
    );
    await call(
      'POST',
      `/venues/${venue.id}/seats`,
      { section: 'Back', rows: 3, seatsPerRow: 4 },
      admin,
    );

    const { sections } = await json(await call('GET', `/venues/${venue.id}/sections`));
    const byName = Object.fromEntries(
      sections.map((s: { name: string; seatCount: number }) => [s.name, s.seatCount]),
    );
    assert.equal(byName['Front'], 10);
    assert.equal(byName['Back'], 12);
  });
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/venues.capabilities.test.ts`
Expected: FAIL — `sections` is an array of strings, so `s.name` is `undefined`.

- [ ] **Step 3: Return counts from the service**

In `apps/api/src/modules/venues/service.ts`, replace `listSections`:

```ts
/**
 * Sections in a venue with their seat counts — what a category may claim, and
 * how many seats a price will cover.
 *
 * The count matters: pricing a section blind is how an organiser discovers at
 * show-creation time that "Balcony" was four hundred seats.
 */
export async function listSections(venueId: string) {
  const grouped = await prisma.seat.groupBy({
    by: ['section'],
    where: { venueId },
    _count: { _all: true },
    orderBy: { section: 'asc' },
  });
  return grouped.map((g) => ({ name: g.section, seatCount: g._count._all }));
}
```

- [ ] **Step 4: Run the test**

Run: `cd apps/api && NODE_ENV=test node --import tsx --test tests/venues.capabilities.test.ts`
Expected: PASS, 9 tests.

- [ ] **Step 5: Show the counts in the organiser UI**

In `apps/web/src/pages/OrganiserPage.tsx`:

Change the `sections` fetch type in `EventEditor`:

```tsx
  const sections = useAsync(
    () =>
      api.get<{ sections: { name: string; seatCount: number }[] }>(
        `/api/v1/venues/${event.venue.id}/sections`,
      ),
    [event.venue.id],
  );

  const priced = new Set(event.categories.flatMap((c) => c.sections));
  const unpriced = (sections.data?.sections ?? []).filter((s) => !priced.has(s.name));
```

Update the warning to use names:

```tsx
          <p className="manage__warn">
            Still unpriced: <strong>{unpriced.map((s) => s.name).join(', ')}</strong>. A show
            cannot be created until every section has a price.
          </p>
```

Change `AddCategory`'s prop type and checkbox rendering:

```tsx
function AddCategory({
  eventId,
  available,
  onAdded,
}: {
  eventId: string;
  available: { name: string; seatCount: number }[];
  onAdded: () => void;
}) {
```

and the fieldset:

```tsx
      <fieldset className="checks">
        <legend className="field__label">Sections this covers</legend>
        {available.map((section) => (
          <label key={section.name} className="check">
            <input
              type="checkbox"
              checked={chosen.includes(section.name)}
              onChange={() => toggle(section.name)}
            />
            {section.name}
            {/* The seat count is the point: pricing a section blind is how an
                organiser finds out at show-creation time that it was 400 seats. */}
            <small>{section.seatCount} seats</small>
          </label>
        ))}
      </fieldset>
```

Add to `apps/web/src/pages/manage.css`:

```css
.check small {
  color: var(--text-muted);
  font-size: var(--text-xs);
}
```

- [ ] **Step 6: Typecheck and run everything**

Run: `npm run typecheck && cd apps/api && NODE_ENV=test npm test`
Expected: 0 type errors; 107 passing, 0 failing.

- [ ] **Step 7: Commit**

```bash
npm run format
git add apps/api apps/web
git commit -m "$(cat <<'EOF'
Show seat counts when pricing a section

An organiser priced sections blind, learning only at show-creation time that
"Balcony" was four hundred seats. GET /venues/:id/sections now returns each
section with its seat count, and the pricing checkboxes show it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Update the documentation

**Files:**
- Modify: `docs/API.md`
- Modify: `README.md`
- Modify: `docs/DECISIONS.md`
- Modify: `docs/CONTEXT.md`
- Modify: `docs/TODO.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Record the two new ADRs**

Append to `docs/DECISIONS.md`:

```markdown
---

## ADR-027 — Stage layout is stored venue geometry, not a render-time projection

**Accepted** · 2026-08-23

`Venue.stageLayout` decides how the venue builder generates coordinates. A
centre-stage venue's seats are written with radial `posX`/`posY` at build time.

_Alternative, and my own earlier draft:_ layout as a per-event projection,
computing radial positions at render time so one hall could be staged both ways.

_Why not:_ it solved a problem nobody has. A hall built in the round **is** in
the round. Storing the geometry means the seat map renderer needs no special
case at all — it already draws whatever coordinates it is given — and the two
layouts differ only in the stage marker.

_Consequence:_ a venue cannot be re-staged after its seats exist. Build a second
venue instead. That is the honest model: re-staging a real room means moving
real chairs.

---

## ADR-028 — Venue double-booking is prevented by a partial exclusion constraint

**Accepted** · 2026-08-23

Two layers. `assertVenueFree()` inside the show-creation transaction locks the
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

_Cost:_ Prisma cannot express it, so it is hand-written and invisible to
`schema.prisma`. Recorded in `docs/DEBUGGING.md` as drift a future `migrate dev`
may try to drop.

---

## ADR-029 — Holds expire on two clocks

**Accepted** · 2026-08-23

Abandonment gives the full `HOLD_TTL_SECONDS` (300). An explicit back or cancel
shortens the hold to `RELEASE_GRACE_SECONDS` (15) rather than deleting it.

_Why not delete:_ keeping the owner lets `extendHold()` restore the full TTL if
the customer returns, so a mis-clicked Back is recoverable rather than a lost
seat. Deleting makes that impossible.

_Why not zero:_ bouncing back and forward should not cost somebody their seats
to a faster customer.

_Why this needed no new mechanism:_ `effectiveStatus()` already treats a lapsed
lease as free, so the seat becomes bookable at exactly fifteen seconds without
the sweeper being involved at all. One number changed.
```

- [ ] **Step 2: Update the API reference**

In `docs/API.md`, in the Venues table, replace the `POST /venues` and `GET /venues/:id/sections` rows:

```markdown
| ✅  | `GET /venues/:id/sections`  | public | `{ name, seatCount }[]` — what a category may claim and how many seats a price covers. |
| ✅  | `POST /venues`              | ADMIN  | `{ name, address, stageLayout?, allowedEventTypes?, turnaroundMinutes? }`. `400 CENTRE_STAGE_CANNOT_SHOW_MOVIES` — nobody projects a film in the round. |
```

and in the Seats table add:

```markdown
| ✅  | `POST /shows/:id/holds/extend` | any auth | Restores a shortened hold to the full TTL. `409 NO_ACTIVE_HOLD` once it has lapsed. |
```

and amend the `DELETE /shows/:id/holds` row:

```markdown
| ✅  | `DELETE /shows/:id/holds` | any auth | **Shortens** the caller's holds to `RELEASE_GRACE_SECONDS` rather than deleting them, so a mis-clicked Back is recoverable via the extend endpoint. Returns `{ released, freeAt }`. |
```

In the Events table amend `POST /events/:id/shows`:

```markdown
| ✅  | `POST /events/:id/shows` | ORGANISER | `{ startsAt, durationMinutes }`. `409 VENUE_DOUBLE_BOOKED` if the venue is occupied, counting the turnaround. `400 SECTION_NOT_PRICED` rolls the whole thing back. |
```

and `POST /events`:

```markdown
| ✅  | `POST /events` | ORGANISER | `400 EVENT_TYPE_NOT_ALLOWED` if the venue does not permit that type. |
```

- [ ] **Step 3: Update the README**

In `README.md`, in the "Seat hold and TTL" section, after the paragraph beginning "**The sweeper is visibility.**", insert:

```markdown
### Two clocks

Abandoning checkout and pressing Back are different events:

| Situation | Seats free after |
| --- | --- |
| Tab closed, walked away | **5 minutes** |
| Explicit back or cancel | **15 seconds** |

Back does not delete the hold — it *shortens* it. The owner is kept, so a
customer who bounces back and forward can reclaim their seats instead of losing
them to somebody faster. No new mechanism: `effectiveStatus()` makes the seat
bookable at exactly fifteen seconds, with the sweeper uninvolved.
```

In the "What it does" table, change the Admin row to:

```markdown
| **Admin**     | Create venues, build their seat layouts, and set their capabilities — stage layout, which event types they permit, and how long the room needs between shows |
```

and the Organiser row to:

```markdown
| **Organiser** | Book a venue for a slot (no double-booking), price each section, schedule shows, and read revenue by category and by show |
```

- [ ] **Step 4: Update the phase markers**

In `docs/TODO.md`, tick every Milestone 0 and Milestone 1 checkbox.

In `docs/CONTEXT.md`, update the **Current state** table so **Phase** reads
`Milestone 1 complete — venue capabilities, scheduling, three-page flow` and
**Next action** reads `Milestone 2: show cancellation`.

In `CLAUDE.md`, update the **Current phase** line to name milestone 2 as next.

- [ ] **Step 5: Commit**

```bash
npm run format
git add docs CLAUDE.md README.md
git commit -m "$(cat <<'EOF'
Document venue capabilities, scheduling and the two-clock TTL

Three ADRs: stage layout as stored geometry rather than a render-time
projection, including why that reverses my earlier draft; venue double-booking
prevented by a partial GiST exclusion constraint, with the reasoning for
denormalising venueId and for making the constraint partial; and the two-clock
hold expiry, including why an explicit back shortens rather than deletes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review

**Spec coverage**

| Spec section | Task |
| --- | --- |
| Step zero — test database split | 1 |
| 1. Venue capabilities — schema, layout, validation, event-type gate | 3, 4, 5 |
| 2. Venue scheduling — occupied window, denormalised `venueId`, both layers | 6, 7 |
| 3. Section-wise pricing | 10 |
| 4. Three-page flow, two clocks, honest limitation | 8, 9 |
| Migrations | 3, 6, 7 |
| Tests | every task |
| Non-goals | respected — no per-event projection, no per-event turnaround, no recurring shows |

**Placeholders:** none remaining. The review caught one: the checkout page called a
`holds/beacon-release` endpoint no task defined — and `navigator.sendBeacon` cannot
send an `Authorization` header, so that endpoint would have had to free seats
unauthenticated. Cut; the five-minute TTL is the backstop, which is the spec's own
"honest limitation".

**Names verified against the codebase** rather than written from memory:
`seconds()` and `blankAsUnset()` in `env.ts`; `ApiError.badRequest`/`conflict`;
`broadcastStatus` and the `env` import in `seats/service.ts`; `compact()` from
`lib/http.js`; `Button`'s `variant`/`full`/`loading` props; `HoldCountdown`'s
`expiresAt`/`onExpire`; `useAsync`'s `{ data, error, loading, reload }`;
`api.get`/`post`/`del`; `SeatView`'s `heldByMe`, `holdExpiresAt`, `categoryName`,
`price`; `ShowDetail`'s `event.id` and `event.venue.name`.

**Type consistency:** `occupiedWindow` returns `{ endsAt, occupiesUntil }` in Task 6 and is consumed with those names in Task 7 and the seed. `listSections` returns `{ name, seatCount }[]` in Task 10 and is consumed with those names in the same task's UI change. `generateEndStageBlock` / `generateCentreStageBlock` are defined in Task 2 and consumed in Task 4 with matching parameter names. `releaseHolds` gains `freeAt` in Task 8 and nothing else reads it.

**Known cross-task hazard, flagged for the executor:** Task 6 deliberately leaves `npm run typecheck` failing, and Task 7 fixes it. Do not attempt to run a full green typecheck between those two commits.
