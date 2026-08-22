import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { after, before, describe, test } from 'node:test';
import type { Server } from 'node:http';
import type express from 'express';
import { createApp } from '../src/app.js';
import { prisma } from '../src/lib/prisma.js';
import { bookingReference, randomToken, verifyUrl } from '../src/lib/qr.js';
import { closeRedis } from '../src/lib/redis.js';

const RUN = randomBytes(5).toString('hex');
const tag = (s: string) => `${s} ${RUN}`;
const emailFor = (who: string) => `b4-${who}-${RUN}@example.test`;
const PASSWORD = 'correct horse battery';

let server: Server;
let base: string;
let showId: string;
let seatIds: string[];
let customer: string;
let other: string;

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

const register = async (who: string) =>
  (
    await json(
      await post('/auth/register', { email: emailFor(who), password: PASSWORD, name: who }),
    )
  ).accessToken as string;

/** Holds seats then books them — the normal path a customer walks. */
async function holdAndBook(ids: string[], token: string) {
  const held = await post(`/shows/${showId}/holds`, { seatIds: ids }, token);
  assert.equal(held.status, 201, 'precondition: the hold should succeed');
  return post('/bookings', { showId, seatIds: ids }, token);
}

before(async () => {
  const app = createApp();
  server = await new Promise<Server>((resolve) => {
    const s = (app as express.Express).listen(0, () => resolve(s));
  });
  const addr = server.address();
  if (typeof addr === 'string' || addr === null) throw new Error('no port');
  base = `http://127.0.0.1:${addr.port}`;

  customer = await register('cust');
  other = await register('other');

  const organiser = await prisma.user.create({
    data: { email: emailFor('org'), name: 'Org', role: 'ORGANISER', passwordHash: 'unused' },
  });
  const venue = await prisma.venue.create({ data: { name: tag('Hall'), address: 'x' } });
  await prisma.seat.createMany({
    data: Array.from({ length: 10 }, (_, i) => ({
      venueId: venue.id,
      section: 'Stalls',
      row: 'A',
      number: i + 1,
      posX: i,
      posY: 0,
    })),
  });
  const event = await prisma.event.create({
    data: { organiserId: organiser.id, venueId: venue.id, title: tag('Booking'), type: 'CONCERT' },
  });
  const category = await prisma.seatCategory.create({
    data: { eventId: event.id, name: 'Stalls', price: '250.50', sections: ['Stalls'] },
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
  server.close();
  // Booking enqueues an email, which opens an ioredis connection. Left open it
  // keeps the event loop alive and the test process never exits.
  await closeRedis();
});

describe('tokens and references', () => {
  test('qrToken is 32 random bytes, never repeated', () => {
    const tokens = new Set(Array.from({ length: 500 }, randomToken));
    assert.equal(tokens.size, 500, 'randomToken produced a collision in 500 draws');
    const one = randomToken();
    assert.equal(one.length, 64, '32 bytes hex-encoded is 64 characters');
    assert.match(one, /^[0-9a-f]+$/);
  });

  test('the human reference avoids characters that misread', () => {
    const refs = Array.from({ length: 300 }, bookingReference);
    for (const ref of refs) {
      assert.match(ref, /^BK-[A-HJ-NP-Z2-9]{5}$/, `${ref} contains an ambiguous character`);
    }
    // I, O, 0 and 1 are excluded on purpose — a reference gets read aloud.
    assert.ok(!refs.join('').includes('I'));
    assert.ok(!refs.join('').includes('O'));
  });

  test('the QR encodes a verification URL, not booking data', () => {
    const token = randomToken();
    const url = verifyUrl(token);
    assert.ok(url.includes(`/verify/${token}`));
    assert.ok(!url.includes('BK-'), 'the QR must not carry the human reference');
  });
});

describe('creating a booking', () => {
  test('turns held seats into a confirmed booking and frees nothing', async () => {
    const ids = [seatIds[0]!, seatIds[1]!];
    const res = await holdAndBook(ids, customer);
    assert.equal(res.status, 201);

    const { booking } = await json(res);
    assert.equal(booking.status, 'CONFIRMED');
    assert.match(booking.reference, /^BK-/);
    assert.equal(booking.seats.length, 2);
    // 250.50 × 2 — the case a float gets wrong.
    assert.equal(booking.total, '501');
    assert.equal(booking.seats[0].price, '250.5');
    assert.equal(booking.qrToken, undefined, 'the list shape must not carry the QR token');

    const rows = await prisma.showSeat.findMany({
      where: { id: { in: ids } },
      select: { status: true, heldByUserId: true, holdExpiresAt: true },
    });
    assert.ok(rows.every((r) => r.status === 'BOOKED'));
    assert.ok(rows.every((r) => r.heldByUserId === null && r.holdExpiresAt === null));
  });

  test('refuses seats held by somebody else', async () => {
    const seat = seatIds[2]!;
    assert.equal((await post(`/shows/${showId}/holds`, { seatIds: [seat] }, other)).status, 201);

    const res = await post('/bookings', { showId, seatIds: [seat] }, customer);
    assert.equal(res.status, 409);
    assert.equal((await json(res)).error.code, 'HOLD_NOT_VALID');

    const row = await prisma.showSeat.findUnique({ where: { id: seat }, select: { status: true } });
    assert.equal(row?.status, 'HELD', 'the other customer must still hold it');
  });

  test('refuses a seat that was never held', async () => {
    const res = await post('/bookings', { showId, seatIds: [seatIds[3]!] }, customer);
    assert.equal(res.status, 409);
    assert.equal((await json(res)).error.code, 'HOLD_NOT_VALID');
  });

  test('refuses an expired hold', async () => {
    const seat = seatIds[4]!;
    await post(`/shows/${showId}/holds`, { seatIds: [seat] }, customer);
    await prisma.showSeat.update({
      where: { id: seat },
      data: { holdExpiresAt: new Date(Date.now() - 1000) },
    });

    const res = await post('/bookings', { showId, seatIds: [seat] }, customer);
    assert.equal(res.status, 409, 'an expired hold must not be bookable');
  });

  test('two simultaneous bookings of one held seat produce one booking', async () => {
    const seat = seatIds[5]!;
    await post(`/shows/${showId}/holds`, { seatIds: [seat] }, customer);

    // The same customer double-clicking checkout. BookingSeat.showSeatId is
    // @unique, so even if both transactions got through, Postgres refuses the
    // second — the seatbelt behind the application check.
    const [a, b] = await Promise.all([
      post('/bookings', { showId, seatIds: [seat] }, customer),
      post('/bookings', { showId, seatIds: [seat] }, customer),
    ]);

    const created = [a.status, b.status].filter((s) => s === 201).length;
    assert.equal(created, 1, `expected one booking, got ${created} (${a.status}, ${b.status})`);

    const count = await prisma.bookingSeat.count({ where: { showSeatId: seat } });
    assert.equal(count, 1, 'one show-seat ended up in two bookings');
  });

  test('booking requires authentication', async () => {
    assert.equal((await post('/bookings', { showId, seatIds: [seatIds[6]!] })).status, 401);
  });
});

describe('reading bookings', () => {
  test('history lists only your own', async () => {
    const mine = await json(await get('/bookings', customer));
    const theirs = await json(await get('/bookings', other));
    assert.ok(mine.bookings.length > 0);
    assert.equal(theirs.bookings.length, 0, "another customer's history must be empty");
  });

  test('fetching one exposes the QR token to its owner and nobody else', async () => {
    const { bookings } = await json(await get('/bookings', customer));
    const id = bookings[0].id;

    const own = await json(await get(`/bookings/${id}`, customer));
    assert.ok(own.booking.qrToken, 'the owner needs the token to render their ticket');

    const foreign = await get(`/bookings/${id}`, other);
    assert.equal(foreign.status, 403);
    assert.equal((await json(foreign)).error.code, 'FORBIDDEN');
  });
});

describe('verification', () => {
  test('a real token verifies without revealing the customer', async () => {
    const { bookings } = await json(await get('/bookings', customer));
    const { booking } = await json(await get(`/bookings/${bookings[0].id}`, customer));

    // Public route, no token — the door staff are not logged in.
    const res = await get(`/verify/${booking.qrToken}`);
    assert.equal(res.status, 200);

    const body = await res.text();
    assert.ok(!body.includes('@example.test'), 'the ticket leaked the customer email');
    const { ticket } = JSON.parse(body);
    assert.equal(ticket.valid, true);
    assert.equal(ticket.reference, booking.reference);
    assert.ok(Array.isArray(ticket.seats) && ticket.seats.length > 0);
  });

  test('an unknown token is refused', async () => {
    assert.equal((await get(`/verify/${randomToken()}`)).status, 404);
  });
});

describe('cancelling', () => {
  test('another customer cannot cancel your booking', async () => {
    const { bookings } = await json(await get('/bookings', customer));
    const res = await post(`/bookings/${bookings[0].id}/cancel`, undefined, other);
    assert.equal(res.status, 403);

    const still = await prisma.booking.findUnique({
      where: { id: bookings[0].id },
      select: { status: true },
    });
    assert.equal(still?.status, 'CONFIRMED', 'the booking was cancelled by a stranger');
  });

  test('cancelling frees the seats and invalidates the QR', async () => {
    const ids = [seatIds[7]!, seatIds[8]!];
    const { booking } = await json(await holdAndBook(ids, customer));

    const full = await json(await get(`/bookings/${booking.id}`, customer));
    const token = full.booking.qrToken;
    assert.equal((await json(await get(`/verify/${token}`))).ticket.valid, true);

    const res = await post(`/bookings/${booking.id}/cancel`, undefined, customer);
    assert.equal(res.status, 200);
    assert.equal((await json(res)).seatsReleased, 2);

    const seats = await prisma.showSeat.findMany({
      where: { id: { in: ids } },
      select: { status: true },
    });
    assert.ok(
      seats.every((s) => s.status === 'AVAILABLE'),
      'seats were not released',
    );

    // The token still resolves — the door needs to distinguish "not a ticket"
    // from "a cancelled ticket" — but it is no longer valid.
    const after = await json(await get(`/verify/${token}`));
    assert.equal(after.ticket.valid, false);
    assert.equal(after.ticket.status, 'CANCELLED');
  });

  test('cancelling twice is refused', async () => {
    const { bookings } = await json(await get('/bookings', customer));
    const cancelled = bookings.find((b: { status: string }) => b.status === 'CANCELLED');
    assert.ok(cancelled, 'precondition: a cancelled booking exists');

    const res = await post(`/bookings/${cancelled.id}/cancel`, undefined, customer);
    assert.equal(res.status, 409);
    assert.equal((await json(res)).error.code, 'ALREADY_CANCELLED');
  });

  test('a released seat can be booked again by someone else', async () => {
    const seat = seatIds[7]!;
    const res = await holdAndBook([seat], other);
    assert.equal(res.status, 201, 'a cancelled seat must go back on sale');
  });
});
