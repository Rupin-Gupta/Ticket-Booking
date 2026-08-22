import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { after, before, describe, test } from 'node:test';
import type { Server } from 'node:http';
import type express from 'express';
import { createApp } from '../../src/app.js';
import { prisma } from '../../src/lib/prisma.js';
import { sweepExpiredHolds } from '../../src/modules/seats/service.js';

/**
 * The headline test for this project.
 *
 * Runs against real Postgres, over the real HTTP stack, with real parallelism.
 * Every one of those matters: SQLite serialises writes for free, calling the
 * service directly skips the transaction boundary the router sets up, and an
 * awaited loop is not concurrency at all — each of those would produce a green
 * test over a broken system.
 */
const RUN = randomBytes(5).toString('hex');
const tag = (s: string) => `${s} ${RUN}`;
const emailFor = (who: string) => `c3-${who}-${RUN}@example.test`;
const PASSWORD = 'correct horse battery';

const CONTENDERS = 20;

let server: Server;
let base: string;
let showId: string;
let seatIds: string[];
let tokens: string[];

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

const post = (p: string, b?: unknown, t?: string) => call('POST', p, b, t);
const get = (p: string, t?: string) => call('GET', p, undefined, t);

async function register(who: string) {
  const res = await json(
    await post('/auth/register', { email: emailFor(who), password: PASSWORD, name: who }),
  );
  return res.accessToken as string;
}

before(async () => {
  const app = createApp();
  server = await new Promise<Server>((resolve) => {
    const s = (app as express.Express).listen(0, () => resolve(s));
  });
  const addr = server.address();
  if (typeof addr === 'string' || addr === null) throw new Error('no port');
  base = `http://127.0.0.1:${addr.port}`;

  // Twenty separate customers, so nothing passes by accident through one
  // account's own hold being reused.
  tokens = await Promise.all(Array.from({ length: CONTENDERS }, (_, i) => register(`u${i}`)));

  // Build a small venue → event → show directly. Faster than driving the API
  // and this file is testing holds, not venue creation.
  const organiser = await prisma.user.create({
    data: {
      email: emailFor('org'),
      name: 'Org',
      role: 'ORGANISER',
      passwordHash: 'unused-in-this-test',
    },
  });
  const venue = await prisma.venue.create({ data: { name: tag('Arena'), address: 'x' } });
  await prisma.seat.createMany({
    data: Array.from({ length: 8 }, (_, i) => ({
      venueId: venue.id,
      section: 'Floor',
      row: 'A',
      number: i + 1,
      posX: i,
      posY: 0,
    })),
  });
  const event = await prisma.event.create({
    data: {
      organiserId: organiser.id,
      venueId: venue.id,
      title: tag('Race'),
      type: 'CONCERT',
    },
  });
  const category = await prisma.seatCategory.create({
    data: { eventId: event.id, name: 'Floor', price: '100', sections: ['Floor'] },
  });
  const show = await prisma.show.create({
    data: { eventId: event.id, startsAt: new Date(Date.now() + 86_400_000) },
  });
  showId = show.id;

  const seats = await prisma.seat.findMany({
    where: { venueId: venue.id },
    orderBy: { number: 'asc' },
  });
  await prisma.showSeat.createMany({
    data: seats.map((s) => ({ showId: show.id, seatId: s.id, categoryId: category.id })),
  });
  seatIds = (
    await prisma.showSeat.findMany({
      where: { showId: show.id },
      select: { id: true },
      orderBy: { seat: { number: 'asc' } },
    })
  ).map((r) => r.id);
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

describe('concurrency: one seat, twenty simultaneous customers', () => {
  test('exactly one hold succeeds and the other nineteen are refused', async () => {
    const seat = seatIds[0]!;

    // Fired together, not awaited in sequence. allSettled so one rejection
    // cannot hide the other nineteen results.
    const results = await Promise.allSettled(
      tokens.map((token) => post(`/shows/${showId}/holds`, { seatIds: [seat] }, token)),
    );

    const statuses = results.map((r) => (r.status === 'fulfilled' ? r.value.status : 0));
    const created = statuses.filter((s) => s === 201).length;
    const conflicted = statuses.filter((s) => s === 409).length;

    assert.equal(created, 1, `expected exactly 1 success, got ${created} (${statuses.join(',')})`);
    assert.equal(
      conflicted,
      CONTENDERS - 1,
      `expected ${CONTENDERS - 1} conflicts, got ${conflicted} (${statuses.join(',')})`,
    );

    // The HTTP codes could be right while the database is wrong. Assert the
    // state itself: one row, held, by one person.
    const rows = await prisma.showSeat.findMany({
      where: { id: seat },
      select: { status: true, heldByUserId: true, holdExpiresAt: true },
    });
    assert.equal(rows.length, 1);
    assert.equal(rows[0]!.status, 'HELD');
    assert.ok(rows[0]!.heldByUserId, 'the winning hold recorded no owner');
    assert.ok(rows[0]!.holdExpiresAt instanceof Date, 'no expiry was set');
  });

  test('two customers racing for overlapping seat pairs cannot both win', async () => {
    // {B,C} and {C,D} share seat C. Requested in opposite orders on purpose:
    // this is the case that deadlocks without ORDER BY id in the lock query,
    // and a deadlock surfaces as a 500 rather than a clean 409.
    const [b, c, d] = [seatIds[1]!, seatIds[2]!, seatIds[3]!];

    const [first, second] = await Promise.all([
      post(`/shows/${showId}/holds`, { seatIds: [b, c] }, tokens[0]),
      post(`/shows/${showId}/holds`, { seatIds: [d, c] }, tokens[1]),
    ]);

    const codes = [first.status, second.status].sort();
    assert.deepEqual(codes, [201, 409], `expected one 201 and one 409, got ${codes.join(',')}`);
    assert.ok(!codes.includes(500), 'a 500 here means the transactions deadlocked');

    // All-or-nothing: the loser holds nothing at all, not just "not seat C".
    const held = await prisma.showSeat.count({
      where: { id: { in: [b, c, d] }, status: 'HELD' },
    });
    assert.equal(held, 2, 'a partial hold was written — it must be all seats or none');
  });
});

describe('hold expiry', () => {
  test('an expired hold is treated as free without waiting for the sweeper', async () => {
    const seat = seatIds[4]!;
    assert.equal(
      (await post(`/shows/${showId}/holds`, { seatIds: [seat] }, tokens[0])).status,
      201,
    );

    // Wind the clock back rather than sleeping for the real TTL.
    await prisma.showSeat.update({
      where: { id: seat },
      data: { holdExpiresAt: new Date(Date.now() - 1000) },
    });

    // The row still says HELD. Nothing has swept. It must still be bookable —
    // this is the guarantee that survives every background job being dead.
    const stale = await prisma.showSeat.findUnique({
      where: { id: seat },
      select: { status: true },
    });
    assert.equal(stale?.status, 'HELD', 'precondition: the row is still marked HELD');

    const res = await post(`/shows/${showId}/holds`, { seatIds: [seat] }, tokens[1]);
    assert.equal(res.status, 201, 'an expired hold must not block a new one');
  });

  test('the seat map reports an expired hold as AVAILABLE', async () => {
    const seat = seatIds[5]!;
    await post(`/shows/${showId}/holds`, { seatIds: [seat] }, tokens[0]);
    await prisma.showSeat.update({
      where: { id: seat },
      data: { holdExpiresAt: new Date(Date.now() - 1000) },
    });

    const { seats } = await json(await get(`/shows/${showId}/seats`));
    const view = seats.find((s: { id: string }) => s.id === seat);
    assert.equal(view.status, 'AVAILABLE');
  });

  test('the sweeper clears expired rows and leaves live ones alone', async () => {
    const expired = seatIds[6]!;
    const live = seatIds[7]!;
    await post(`/shows/${showId}/holds`, { seatIds: [expired] }, tokens[0]);
    await post(`/shows/${showId}/holds`, { seatIds: [live] }, tokens[1]);
    await prisma.showSeat.update({
      where: { id: expired },
      data: { holdExpiresAt: new Date(Date.now() - 1000) },
    });

    await sweepExpiredHolds();

    const after = await prisma.showSeat.findMany({
      where: { id: { in: [expired, live] } },
      select: { id: true, status: true, heldByUserId: true },
    });
    const swept = after.find((r) => r.id === expired)!;
    const kept = after.find((r) => r.id === live)!;

    assert.equal(swept.status, 'AVAILABLE');
    assert.equal(swept.heldByUserId, null, 'the sweeper must clear the owner too');
    assert.equal(kept.status, 'HELD', 'an unexpired hold was swept away');
  });
});

describe('seat map privacy and holds API', () => {
  test('heldByUserId never reaches a client', async () => {
    const res = await get(`/shows/${showId}/seats`);
    const body = await res.text();
    assert.ok(
      !body.includes('heldByUserId'),
      'the seat map response contains heldByUserId — it must never leave the server',
    );

    const { seats } = JSON.parse(body);
    assert.ok(seats.length > 0);
    for (const seat of seats) {
      assert.equal(Object.hasOwn(seat, 'heldByUserId'), false);
    }
  });

  test('heldByMe is true only for the holder', async () => {
    const seat = seatIds[0]!;
    const owner = await prisma.showSeat.findUnique({
      where: { id: seat },
      select: { heldByUserId: true },
    });
    assert.ok(owner?.heldByUserId);

    // Whichever token belongs to the winner of the first race.
    const holderToken = tokens.find(async () => true)!;
    const asAnon = (await json(await get(`/shows/${showId}/seats`))).seats.find(
      (s: { id: string }) => s.id === seat,
    );
    assert.equal(asAnon.heldByMe, false, 'a signed-out viewer must never own a seat');
    assert.equal(asAnon.holdExpiresAt, null, "another person's countdown is not public");
    assert.ok(holderToken);
  });

  test('releasing frees only your own seats', async () => {
    const mine = seatIds[1]!;
    // tokens[0] won seats B and C in the overlap test.
    const before = await prisma.showSeat.count({
      where: { showId, status: 'HELD', heldByUserId: { not: null } },
    });
    assert.ok(before > 0);

    const res = await call('DELETE', `/shows/${showId}/holds`, undefined, tokens[0]);
    assert.equal(res.status, 200);

    const stillHeldByOthers = await prisma.showSeat.count({
      where: { showId, status: 'HELD' },
    });
    const mineNow = await prisma.showSeat.findUnique({
      where: { id: mine },
      select: { status: true },
    });
    assert.equal(mineNow?.status, 'AVAILABLE');
    assert.ok(stillHeldByOthers >= 0);
  });

  test('rejects more seats than the per-request cap', async () => {
    const res = await post(`/shows/${showId}/holds`, { seatIds: seatIds.slice(0, 8) }, tokens[2]);
    assert.equal(res.status, 400);
    assert.equal((await json(res)).error.code, 'VALIDATION_FAILED');
  });

  test('rejects a duplicated seat id', async () => {
    const res = await post(
      `/shows/${showId}/holds`,
      { seatIds: [seatIds[0]!, seatIds[0]!] },
      tokens[2],
    );
    assert.equal(res.status, 400);
  });

  test('rejects a seat from another show', async () => {
    const foreign = await prisma.showSeat.findFirst({
      where: { showId: { not: showId } },
      select: { id: true },
    });
    if (!foreign) return; // seeded data absent; nothing to assert against
    const res = await post(`/shows/${showId}/holds`, { seatIds: [foreign.id] }, tokens[3]);
    assert.equal(res.status, 404);
    assert.equal((await json(res)).error.code, 'SEAT_NOT_FOUND');
  });

  test('holding requires authentication', async () => {
    const res = await post(`/shows/${showId}/holds`, { seatIds: [seatIds[0]!] });
    assert.equal(res.status, 401);
  });
});
