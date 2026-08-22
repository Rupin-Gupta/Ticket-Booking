import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { createServer, type Server } from 'node:http';
import { after, before, describe, test } from 'node:test';
import { io as connect, type Socket } from 'socket.io-client';
import { SOCKET_EVENTS } from '@ticket/shared';
import { createApp } from '../src/app.js';
import { prisma } from '../src/lib/prisma.js';
import { closeRedis } from '../src/lib/redis.js';
import { startRealtime } from '../src/realtime/index.js';

/**
 * Proves a mutation on one connection reaches a different connection — the
 * whole point of Phase 6, and the thing a unit test of the emitter could not
 * tell you.
 */
const RUN = randomBytes(5).toString('hex');
const tag = (s: string) => `${s} ${RUN}`;
const emailFor = (who: string) => `rt-${who}-${RUN}@example.test`;
const PASSWORD = 'correct horse battery';

let server: Server;
let stopRealtime: () => Promise<void>;
let base: string;
let showId: string;
let seatIds: string[];
let holder: string;
let watcher: Socket;

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

/** Resolves with the next matching broadcast, or rejects on a deadline. */
function nextUpdate(socket: Socket, timeoutMs = 5000): Promise<{ id: string; status: string }[]> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`no seat:update within ${timeoutMs}ms`)),
      timeoutMs,
    );
    socket.once(
      SOCKET_EVENTS.seatUpdate,
      (payload: { seats: { id: string; status: string }[] }) => {
        clearTimeout(timer);
        resolve(payload.seats);
      },
    );
  });
}

before(async () => {
  // The realtime layer is skipped under NODE_ENV=test everywhere else, so this
  // file starts it explicitly on its own server.
  server = createServer(createApp());
  stopRealtime = startRealtime(server);
  await new Promise<void>((resolve) => server.listen(0, resolve));
  const addr = server.address();
  if (typeof addr === 'string' || addr === null) throw new Error('no port');
  base = `http://127.0.0.1:${addr.port}`;

  holder = (
    await json(
      await call('POST', '/auth/register', {
        email: emailFor('holder'),
        password: PASSWORD,
        name: 'Holder',
      }),
    )
  ).accessToken;

  const organiser = await prisma.user.create({
    data: { email: emailFor('org'), name: 'Org', role: 'ORGANISER', passwordHash: 'unused' },
  });
  const venue = await prisma.venue.create({ data: { name: tag('Live'), address: 'x' } });
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
    data: { organiserId: organiser.id, venueId: venue.id, title: tag('Live'), type: 'CONCERT' },
  });
  const category = await prisma.seatCategory.create({
    data: { eventId: event.id, name: 'Main', price: '100', sections: ['Main'] },
  });
  const show = await prisma.show.create({
    data: { eventId: event.id, startsAt: new Date(Date.now() + 86_400_000) },
  });
  showId = show.id;
  const seats = await prisma.seat.findMany({ where: { venueId: venue.id } });
  await prisma.showSeat.createMany({
    data: seats.map((s) => ({ showId: show.id, seatId: s.id, categoryId: category.id })),
  });
  seatIds = (await prisma.showSeat.findMany({ where: { showId }, select: { id: true } })).map(
    (r) => r.id,
  );

  // A second party entirely: this connection never makes the mutation, it only
  // watches. That is what makes the assertion meaningful.
  watcher = connect(base, { transports: ['websocket'] });
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('watcher never connected')), 5000);
    watcher.on('connect', () => {
      clearTimeout(timer);
      resolve();
    });
  });
});

after(async () => {
  watcher.close();
  await stopRealtime();
  // Bookings first: BookingSeat rows reference ShowSeat, so deleting seats
  // while a booking still points at them violates the foreign key.
  const bookings = await prisma.booking.findMany({
    where: { show: { event: { title: { contains: RUN } } } },
    select: { id: true },
  });
  await prisma.bookingSeat.deleteMany({ where: { bookingId: { in: bookings.map((b) => b.id) } } });
  await prisma.booking.deleteMany({ where: { id: { in: bookings.map((b) => b.id) } } });
  await prisma.showSeat.deleteMany({ where: { show: { event: { title: { contains: RUN } } } } });
  await prisma.show.deleteMany({ where: { event: { title: { contains: RUN } } } });
  await prisma.seatCategory.deleteMany({ where: { event: { title: { contains: RUN } } } });
  await prisma.event.deleteMany({ where: { title: { contains: RUN } } });
  await prisma.seat.deleteMany({ where: { venue: { name: { contains: RUN } } } });
  await prisma.venue.deleteMany({ where: { name: { contains: RUN } } });
  await prisma.user.deleteMany({ where: { email: { endsWith: `-${RUN}@example.test` } } });
  await prisma.$disconnect();
  await closeRedis();
  server.close();
});

describe('realtime seat updates', () => {
  test('joining a room returns a full snapshot', async () => {
    const sync = new Promise<{ showId: string; seats: unknown[] }>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('no seat:sync')), 5000);
      watcher.once(SOCKET_EVENTS.seatSync, (payload) => {
        clearTimeout(timer);
        resolve(payload);
      });
    });

    watcher.emit(SOCKET_EVENTS.showJoin, { showId });
    const payload = await sync;

    assert.equal(payload.showId, showId);
    assert.equal(payload.seats.length, 3, 'a late joiner must get the whole map, not a diff');
  });

  test('a hold placed over HTTP reaches a different connection', async () => {
    const seat = seatIds[0]!;
    const update = nextUpdate(watcher);

    const res = await call('POST', `/shows/${showId}/holds`, { seatIds: [seat] }, holder);
    assert.equal(res.status, 201);

    const seats = await update;
    assert.deepEqual(seats, [{ id: seat, status: 'HELD' }]);
  });

  test('the broadcast carries no owner and no countdown', async () => {
    const seat = seatIds[1]!;
    const update = nextUpdate(watcher);
    await call('POST', `/shows/${showId}/holds`, { seatIds: [seat] }, holder);
    const seats = await update;

    const raw = JSON.stringify(seats);
    assert.ok(!raw.includes('heldByUserId'), 'the broadcast leaked who holds the seat');
    assert.ok(!raw.includes('heldByMe'), 'heldByMe is per-viewer and cannot be broadcast');
    assert.ok(!raw.includes('holdExpiresAt'), "another customer's countdown is not public");
    assert.deepEqual(Object.keys(seats[0]!).sort(), ['id', 'status']);
  });

  test('releasing broadcasts the seat as available again', async () => {
    const update = nextUpdate(watcher);
    const res = await call('DELETE', `/shows/${showId}/holds`, undefined, holder);
    assert.equal(res.status, 200);

    const seats = await update;
    assert.ok(seats.length > 0);
    assert.ok(seats.every((s) => s.status === 'AVAILABLE'));
  });

  test('booking broadcasts BOOKED', async () => {
    const seat = seatIds[2]!;
    await call('POST', `/shows/${showId}/holds`, { seatIds: [seat] }, holder);

    const update = nextUpdate(watcher);
    const res = await call('POST', '/bookings', { showId, seatIds: [seat] }, holder);
    assert.equal(res.status, 201);

    const seats = await update;
    assert.deepEqual(seats, [{ id: seat, status: 'BOOKED' }]);
  });

  test('a room only hears about its own show', async () => {
    const other = connect(base, { transports: ['websocket'] });
    await new Promise<void>((resolve) => other.on('connect', () => resolve()));
    other.emit(SOCKET_EVENTS.showJoin, { showId: 'some-other-show' });

    let heard = false;
    other.on(SOCKET_EVENTS.seatUpdate, () => {
      heard = true;
    });

    await call('POST', `/shows/${showId}/holds`, { seatIds: [seatIds[0]!] }, holder);
    await new Promise((r) => setTimeout(r, 800));

    assert.equal(heard, false, 'a broadcast crossed into an unrelated room');
    other.close();
  });
});
