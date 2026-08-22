import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { after, before, beforeEach, describe, test } from 'node:test';
import type { Server } from 'node:http';
import type express from 'express';
import { createApp } from '../../src/app.js';
import { prisma } from '../../src/lib/prisma.js';
import { closeRedis } from '../../src/lib/redis.js';
import { advanceWaitlist, sweepExpiredOffers } from '../../src/modules/waitlist/service.js';

/**
 * The waitlist counterpart to the concurrency test: the offer must go to the
 * earliest joiner and to nobody else, and an ignored offer must walk down the
 * queue on its own.
 */
const RUN = randomBytes(5).toString('hex');
const tag = (s: string) => `${s} ${RUN}`;
const emailFor = (who: string) => `w5-${who}-${RUN}@example.test`;
const PASSWORD = 'correct horse battery';

let server: Server;
let base: string;
let showId: string;
let categoryId: string;
let seatIds: string[];
/** alice joined first, then bob, then cara — the order the queue must honour. */
let alice: string;
let bob: string;
let cara: string;
let aliceId: string;
let bobId: string;
let caraId: string;

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
  const body = await json(
    await post('/auth/register', { email: emailFor(who), password: PASSWORD, name: who }),
  );
  return { token: body.accessToken as string, id: body.user.id as string };
}

/** Books every seat in the show so the category is genuinely sold out. */
async function sellOut(token: string) {
  await post(`/shows/${showId}/holds`, { seatIds }, token);
  const res = await post('/bookings', { showId, seatIds }, token);
  assert.equal(res.status, 201, 'precondition: the buyer should get every seat');
  return (await json(res)).booking.id as string;
}

/** Joins in a fixed order, with a gap so joinedAt cannot tie. */
async function queueUp() {
  const ids: string[] = [];
  for (const token of [alice, bob, cara]) {
    const res = await post(`/shows/${showId}/waitlist`, { categoryId }, token);
    assert.equal(res.status, 201, 'precondition: joining a sold-out category should work');
    ids.push((await json(res)).id);
    // joinedAt has millisecond resolution; three inserts in the same tick would
    // make the FIFO assertion meaningless.
    await new Promise((r) => setTimeout(r, 25));
  }
  return ids;
}

before(async () => {
  const app = createApp();
  server = await new Promise<Server>((resolve) => {
    const s = (app as express.Express).listen(0, () => resolve(s));
  });
  const addr = server.address();
  if (typeof addr === 'string' || addr === null) throw new Error('no port');
  base = `http://127.0.0.1:${addr.port}`;

  ({ token: alice, id: aliceId } = await register('alice'));
  ({ token: bob, id: bobId } = await register('bob'));
  ({ token: cara, id: caraId } = await register('cara'));

  const organiser = await prisma.user.create({
    data: { email: emailFor('org'), name: 'Org', role: 'ORGANISER', passwordHash: 'unused' },
  });
  const venue = await prisma.venue.create({ data: { name: tag('Pit'), address: 'x' } });
  // Two seats only — sold out has to be reachable.
  await prisma.seat.createMany({
    data: [1, 2].map((n) => ({
      venueId: venue.id,
      section: 'Pit',
      row: 'A',
      number: n,
      posX: n,
      posY: 0,
    })),
  });
  const event = await prisma.event.create({
    data: { organiserId: organiser.id, venueId: venue.id, title: tag('Waitlist'), type: 'CONCERT' },
  });
  const category = await prisma.seatCategory.create({
    data: { eventId: event.id, name: 'Pit', price: '100', sections: ['Pit'] },
  });
  categoryId = category.id;
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
    await prisma.showSeat.findMany({ where: { showId: show.id }, select: { id: true } })
  ).map((r) => r.id);
});

/** Every test starts from a clean, sold-out show with an empty queue. */
beforeEach(async () => {
  await prisma.waitlistEntry.deleteMany({ where: { showId } });
  const bookings = await prisma.booking.findMany({ where: { showId }, select: { id: true } });
  await prisma.bookingSeat.deleteMany({ where: { bookingId: { in: bookings.map((b) => b.id) } } });
  await prisma.booking.deleteMany({ where: { id: { in: bookings.map((b) => b.id) } } });
  await prisma.showSeat.updateMany({
    where: { showId },
    data: { status: 'AVAILABLE', heldByUserId: null, holdExpiresAt: null, offerExpiresAt: null },
  });
});

after(async () => {
  await prisma.waitlistEntry.deleteMany({
    where: { show: { event: { title: { contains: RUN } } } },
  });
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
  await closeRedis();
});

describe('joining', () => {
  test('is refused while the category still has seats', async () => {
    const res = await post(`/shows/${showId}/waitlist`, { categoryId }, alice);
    assert.equal(res.status, 409);
    assert.equal((await json(res)).error.code, 'SEATS_STILL_AVAILABLE');
  });

  test('is allowed once sold out, and reports a queue position', async () => {
    await sellOut(alice);

    const first = await post(`/shows/${showId}/waitlist`, { categoryId }, bob);
    assert.equal(first.status, 201);
    assert.equal((await json(first)).position, 1);

    const second = await post(`/shows/${showId}/waitlist`, { categoryId }, cara);
    assert.equal((await json(second)).position, 2);
  });

  test('the same customer cannot join twice', async () => {
    await sellOut(alice);
    assert.equal((await post(`/shows/${showId}/waitlist`, { categoryId }, bob)).status, 201);

    const again = await post(`/shows/${showId}/waitlist`, { categoryId }, bob);
    assert.equal(again.status, 409);
    assert.equal((await json(again)).error.code, 'ALREADY_WAITING');

    const count = await prisma.waitlistEntry.count({ where: { showId, customerId: bobId } });
    assert.equal(count, 1, 'a refresh bought a second place in line');
  });
});

describe('cancellation offers the seat to the earliest joiner, and only them', () => {
  test('three waiting, one cancelled booking → alice alone is offered', async () => {
    // cara buys everything so alice, bob and cara-order is not confused by the
    // buyer also being in the queue.
    const bookingId = await sellOut(cara);
    // alice first, then bob — cara is last and should never be reached here.
    const [aliceEntry, bobEntry, caraEntry] = await queueUp();

    const res = await post(`/bookings/${bookingId}/cancel`, undefined, cara);
    assert.equal(res.status, 200);
    const body = await json(res);
    assert.equal(body.seatsReleased, 2);

    const entries = await prisma.waitlistEntry.findMany({
      where: { showId },
      select: { id: true, customerId: true, status: true, offerToken: true, offeredSeatId: true },
      orderBy: { joinedAt: 'asc' },
    });

    const byId = new Map(entries.map((e) => [e.id, e]));
    // Two seats were freed, so the two earliest joiners get one each — and the
    // third stays waiting. The ordering is the whole assertion.
    assert.equal(byId.get(aliceEntry!)!.status, 'OFFERED', 'the earliest joiner was skipped');
    assert.equal(byId.get(bobEntry!)!.status, 'OFFERED');
    assert.equal(byId.get(caraEntry!)!.status, 'WAITING', 'the last in line was offered too early');

    assert.ok(byId.get(aliceEntry!)!.offerToken, 'no offer token was issued');
    assert.equal(byId.get(caraEntry!)!.offerToken, null, 'a waiting entry must carry no token');

    // The seats are OFFERED, not AVAILABLE — nobody else can take them.
    const seats = await prisma.showSeat.findMany({
      where: { showId },
      select: { status: true, offerExpiresAt: true },
    });
    assert.ok(
      seats.every((s) => s.status === 'OFFERED'),
      'a freed seat went back on general sale',
    );
    assert.ok(
      seats.every((s) => s.offerExpiresAt !== null),
      'an offer has no expiry',
    );
  });

  test('with an empty queue the seat returns to general sale', async () => {
    const bookingId = await sellOut(alice);
    await post(`/bookings/${bookingId}/cancel`, undefined, alice);

    const seats = await prisma.showSeat.findMany({ where: { showId }, select: { status: true } });
    assert.ok(seats.every((s) => s.status === 'AVAILABLE'));
  });
});

describe('accepting an offer', () => {
  async function offerToAlice() {
    const bookingId = await sellOut(cara);
    await post(`/shows/${showId}/waitlist`, { categoryId }, alice);
    await post(`/bookings/${bookingId}/cancel`, undefined, cara);
    const entry = await prisma.waitlistEntry.findFirstOrThrow({
      where: { showId, customerId: aliceId },
      select: { offerToken: true, offeredSeatId: true },
    });
    return entry;
  }

  test('the customer it was offered to can claim it', async () => {
    const { offerToken } = await offerToAlice();

    const res = await post(`/waitlist/offers/${offerToken}/accept`, undefined, alice);
    assert.equal(res.status, 201);
    const { booking } = await json(res);
    assert.equal(booking.status, 'CONFIRMED');
    assert.equal(booking.seats.length, 1);

    const entry = await prisma.waitlistEntry.findFirstOrThrow({
      where: { showId, customerId: aliceId },
      select: { status: true, offerToken: true },
    });
    assert.equal(entry.status, 'CONVERTED');
    assert.equal(entry.offerToken, null, 'the token must be single use');
  });

  test('somebody else holding the link cannot claim it', async () => {
    const { offerToken, offeredSeatId } = await offerToAlice();

    // The token arrives by email. Email gets forwarded.
    const res = await post(`/waitlist/offers/${offerToken}/accept`, undefined, bob);
    assert.equal(res.status, 403);

    const seat = await prisma.showSeat.findUniqueOrThrow({
      where: { id: offeredSeatId! },
      select: { status: true },
    });
    assert.equal(seat.status, 'OFFERED', "a stranger's attempt changed the seat");
  });

  test('an expired offer cannot be claimed even one second late', async () => {
    const { offerToken } = await offerToAlice();
    await prisma.waitlistEntry.updateMany({
      where: { offerToken },
      data: { offerExpiresAt: new Date(Date.now() - 1000) },
    });

    const res = await post(`/waitlist/offers/${offerToken}/accept`, undefined, alice);
    assert.equal(res.status, 410);
    assert.equal((await json(res)).error.code, 'OFFER_EXPIRED');
  });

  test('the same token cannot be used twice', async () => {
    const { offerToken } = await offerToAlice();
    assert.equal(
      (await post(`/waitlist/offers/${offerToken}/accept`, undefined, alice)).status,
      201,
    );

    const again = await post(`/waitlist/offers/${offerToken}/accept`, undefined, alice);
    assert.equal(again.status, 404, 'the token should no longer resolve at all');
  });

  test('reading an offer is public but expiry is honoured', async () => {
    const { offerToken } = await offerToAlice();

    // No token — the link is opened on a phone that is not signed in yet.
    const open = await get(`/waitlist/offers/${offerToken}`);
    assert.equal(open.status, 200);
    assert.ok((await json(open)).offer.expiresAt);

    await prisma.waitlistEntry.updateMany({
      where: { offerToken },
      data: { offerExpiresAt: new Date(Date.now() - 1000) },
    });
    assert.equal((await get(`/waitlist/offers/${offerToken}`)).status, 410);
  });
});

describe('an ignored offer walks down the queue', () => {
  test('expiry passes the seat to the next in line, then to general sale', async () => {
    const bookingId = await sellOut(cara);
    const [aliceEntry, bobEntry] = await queueUp();

    // One seat only, so the queue is unambiguous: free exactly one.
    const booking = await prisma.booking.findUniqueOrThrow({
      where: { id: bookingId },
      select: { seats: { select: { showSeatId: true } } },
    });
    const seatId = booking.seats[0]!.showSeatId;

    await prisma.$transaction(async (tx) => {
      await tx.bookingSeat.updateMany({ where: { bookingId }, data: { releasedAt: new Date() } });
      await tx.booking.update({ where: { id: bookingId }, data: { status: 'CANCELLED' } });
      await advanceWaitlist(tx, seatId);
    });

    let entries = await prisma.waitlistEntry.findMany({
      where: { showId },
      select: { id: true, status: true },
    });
    assert.equal(entries.find((e) => e.id === aliceEntry)!.status, 'OFFERED');
    assert.equal(entries.find((e) => e.id === bobEntry)!.status, 'WAITING');

    // Alice does nothing. Her clock runs out.
    await prisma.waitlistEntry.updateMany({
      where: { id: aliceEntry! },
      data: { offerExpiresAt: new Date(Date.now() - 1000) },
    });

    const first = await sweepExpiredOffers();
    assert.equal(first.expired, 1);
    assert.equal(first.offers.length, 1, 'the seat should have been re-offered, not released');

    entries = await prisma.waitlistEntry.findMany({
      where: { showId },
      select: { id: true, status: true },
    });
    assert.equal(entries.find((e) => e.id === aliceEntry)!.status, 'EXPIRED');
    assert.equal(
      entries.find((e) => e.id === bobEntry)!.status,
      'OFFERED',
      'the next in line was not offered the seat',
    );

    // Bob ignores it too, and cara is behind him.
    await prisma.waitlistEntry.updateMany({
      where: { id: bobEntry! },
      data: { offerExpiresAt: new Date(Date.now() - 1000) },
    });
    const second = await sweepExpiredOffers();
    assert.equal(second.offers.length, 1, 'the third joiner should now hold the offer');

    // Cara ignores it as well — the queue is now empty behind her.
    const caraEntry = await prisma.waitlistEntry.findFirstOrThrow({
      where: { showId, customerId: caraId, status: 'OFFERED' },
      select: { id: true },
    });
    await prisma.waitlistEntry.updateMany({
      where: { id: caraEntry.id },
      data: { offerExpiresAt: new Date(Date.now() - 1000) },
    });
    const third = await sweepExpiredOffers();
    assert.equal(third.offers.length, 0, 'nobody is left, so no further offer should be made');

    const seat = await prisma.showSeat.findUniqueOrThrow({
      where: { id: seatId },
      select: { status: true, offerExpiresAt: true },
    });
    assert.equal(
      seat.status,
      'AVAILABLE',
      'with the queue exhausted the seat must go back on sale',
    );
    assert.equal(seat.offerExpiresAt, null);
  });

  test('leaving while holding an offer hands the seat straight on', async () => {
    const bookingId = await sellOut(cara);
    const [aliceEntry, bobEntry] = await queueUp();

    const booking = await prisma.booking.findUniqueOrThrow({
      where: { id: bookingId },
      select: { seats: { select: { showSeatId: true } } },
    });
    await prisma.$transaction(async (tx) => {
      await tx.bookingSeat.updateMany({ where: { bookingId }, data: { releasedAt: new Date() } });
      await tx.booking.update({ where: { id: bookingId }, data: { status: 'CANCELLED' } });
      await advanceWaitlist(tx, booking.seats[0]!.showSeatId);
    });

    const res = await call('DELETE', `/waitlist/${aliceEntry}`, undefined, alice);
    assert.equal(res.status, 200);
    assert.equal((await json(res)).passedOn, true, 'the seat should not be stranded in OFFERED');

    const bobNow = await prisma.waitlistEntry.findUniqueOrThrow({
      where: { id: bobEntry! },
      select: { status: true },
    });
    assert.equal(bobNow.status, 'OFFERED');
  });
});

describe('leaving the queue', () => {
  test('another customer cannot remove your entry', async () => {
    await sellOut(cara);
    const [aliceEntry] = await queueUp();
    assert.ok(aliceEntry);

    const res = await call('DELETE', `/waitlist/${aliceEntry}`, undefined, bob);
    assert.equal(res.status, 403);

    const still = await prisma.waitlistEntry.findUniqueOrThrow({
      where: { id: aliceEntry! },
      select: { status: true },
    });
    assert.equal(still.status, 'WAITING');
  });

  test('leaving frees the position for those behind', async () => {
    await sellOut(cara);
    const [aliceEntry] = await queueUp();
    assert.ok(aliceEntry);

    await call('DELETE', `/waitlist/${aliceEntry}`, undefined, alice);

    const mine = await json(await get('/waitlist/me', bob));
    assert.equal(mine.entries[0].position, 1, 'bob should have moved up');
  });
});
