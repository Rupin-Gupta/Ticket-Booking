import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { after, before, describe, test } from 'node:test';
import type { Server } from 'node:http';
import type express from 'express';
import { createApp } from '../src/app.js';
import { prisma } from '../src/lib/prisma.js';
import { closeRedis } from '../src/lib/redis.js';

const RUN = randomBytes(5).toString('hex');
const tag = (s: string) => `${s} ${RUN}`;
const emailFor = (who: string) => `o7-${who}-${RUN}@example.test`;
const PASSWORD = 'correct horse battery';

/** Deliberately awkward prices: 199.99 x 3 is where a float drifts. */
const PREMIUM = '199.99';
const STANDARD = '49.50';

let server: Server;
let base: string;
let eventId: string;
let showId: string;
let premiumSeats: string[];
let standardSeats: string[];
let organiser: string;
let otherOrganiser: string;
let customer: string;

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

async function register(who: string, role?: 'ORGANISER') {
  const email = emailFor(who);
  const body = await json(await post('/auth/register', { email, password: PASSWORD, name: who }));
  if (!role) return body.accessToken as string;
  await prisma.user.update({ where: { email }, data: { role } });
  return (await json(await post('/auth/login', { email, password: PASSWORD })))
    .accessToken as string;
}

async function buy(seatIds: string[], token: string) {
  await post(`/shows/${showId}/holds`, { seatIds }, token);
  const res = await post('/bookings', { showId, seatIds }, token);
  assert.equal(res.status, 201, 'precondition: the purchase should succeed');
  return (await json(res)).booking;
}

before(async () => {
  const app = createApp();
  server = await new Promise<Server>((resolve) => {
    const s = (app as express.Express).listen(0, () => resolve(s));
  });
  const addr = server.address();
  if (typeof addr === 'string' || addr === null) throw new Error('no port');
  base = `http://127.0.0.1:${addr.port}`;

  organiser = await register('org', 'ORGANISER');
  otherOrganiser = await register('org2', 'ORGANISER');
  customer = await register('cust');

  const owner = await prisma.user.findUniqueOrThrow({ where: { email: emailFor('org') } });
  const venue = await prisma.venue.create({ data: { name: tag('Dome'), address: 'x' } });
  await prisma.seat.createMany({
    data: [
      ...[1, 2, 3].map((n) => ({
        venueId: venue.id,
        section: 'Premium',
        row: 'A',
        number: n,
        posX: n,
        posY: 0,
      })),
      ...[1, 2, 3, 4].map((n) => ({
        venueId: venue.id,
        section: 'Standard',
        row: 'B',
        number: n,
        posX: n,
        posY: 1,
      })),
    ],
  });

  const event = await prisma.event.create({
    data: { organiserId: owner.id, venueId: venue.id, title: tag('Revenue'), type: 'CONCERT' },
  });
  eventId = event.id;
  const premium = await prisma.seatCategory.create({
    data: { eventId, name: 'Premium', price: PREMIUM, sections: ['Premium'] },
  });
  const standard = await prisma.seatCategory.create({
    data: { eventId, name: 'Standard', price: STANDARD, sections: ['Standard'] },
  });
  const show = await prisma.show.create({
    data: { eventId, startsAt: new Date(Date.now() + 86_400_000) },
  });
  showId = show.id;

  const seats = await prisma.seat.findMany({ where: { venueId: venue.id } });
  await prisma.showSeat.createMany({
    data: seats.map((s) => ({
      showId: show.id,
      seatId: s.id,
      categoryId: s.section === 'Premium' ? premium.id : standard.id,
    })),
  });

  const showSeats = await prisma.showSeat.findMany({
    where: { showId },
    select: { id: true, categoryId: true },
  });
  premiumSeats = showSeats.filter((s) => s.categoryId === premium.id).map((s) => s.id);
  standardSeats = showSeats.filter((s) => s.categoryId === standard.id).map((s) => s.id);
});

after(async () => {
  const bookings = await prisma.booking.findMany({
    where: { show: { event: { title: { contains: RUN } } } },
    select: { id: true },
  });
  await prisma.bookingSeat.deleteMany({ where: { bookingId: { in: bookings.map((b) => b.id) } } });
  await prisma.booking.deleteMany({ where: { id: { in: bookings.map((b) => b.id) } } });
  await prisma.waitlistEntry.deleteMany({
    where: { show: { event: { title: { contains: RUN } } } },
  });
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

describe('access', () => {
  test('a customer cannot read a revenue summary', async () => {
    assert.equal((await get(`/organiser/events/${eventId}/summary`, customer)).status, 403);
    assert.equal((await get(`/organiser/events/${eventId}/summary`)).status, 401);
  });

  test("another organiser cannot read someone else's revenue", async () => {
    const res = await get(`/organiser/events/${eventId}/summary`, otherOrganiser);
    assert.equal(res.status, 403, 'role alone must not grant access to another organiser event');
  });
});

describe('revenue', () => {
  test('an unsold event reports zeroes without dividing by zero', async () => {
    const { totals, categories } = await json(
      await get(`/organiser/events/${eventId}/summary`, organiser),
    );
    assert.equal(totals.revenue, '0');
    assert.equal(totals.seatsSold, 0);
    assert.equal(totals.capacity, 7);
    assert.equal(totals.percentSold, 0);
    assert.equal(categories.length, 2);
  });

  test('revenue reconciles exactly against priceAtBooking', async () => {
    await buy([premiumSeats[0]!, premiumSeats[1]!], customer); // 199.99 x 2
    await buy([standardSeats[0]!], customer); //                   49.50 x 1

    const body = await json(await get(`/organiser/events/${eventId}/summary`, organiser));

    // 199.99 * 2 + 49.50 = 449.48. A float would give 449.47999999999996.
    assert.equal(body.totals.revenue, '449.48');
    assert.equal(body.totals.seatsSold, 3);
    assert.equal(body.totals.capacity, 7);
    assert.equal(body.totals.percentSold, 43);

    const premium = body.categories.find((c: { name: string }) => c.name === 'Premium');
    const standard = body.categories.find((c: { name: string }) => c.name === 'Standard');
    assert.equal(premium.revenue, '399.98');
    assert.equal(premium.seatsSold, 2);
    assert.equal(standard.revenue, '49.5');
    assert.equal(standard.seatsSold, 1);

    // The independent check: sum the rows the database actually holds.
    const rows = await prisma.bookingSeat.findMany({
      where: { booking: { showId, status: 'CONFIRMED' } },
      select: { priceAtBooking: true },
    });
    const fromRows = rows.reduce((sum, r) => sum + Number(r.priceAtBooking), 0);
    assert.equal(
      Number(body.totals.revenue).toFixed(2),
      fromRows.toFixed(2),
      'the summary disagrees with the booking rows it claims to be summarising',
    );
  });

  test('cancelled bookings are excluded from revenue and seats sold', async () => {
    const booking = await buy([standardSeats[1]!], customer);

    const before = await json(await get(`/organiser/events/${eventId}/summary`, organiser));
    assert.equal(before.totals.revenue, '498.98');
    assert.equal(before.totals.seatsSold, 4);

    assert.equal((await post(`/bookings/${booking.id}/cancel`, undefined, customer)).status, 200);

    const after = await json(await get(`/organiser/events/${eventId}/summary`, organiser));
    assert.equal(after.totals.revenue, '449.48', 'a cancelled booking still counted as revenue');
    assert.equal(after.totals.seatsSold, 3, 'a released seat still counted as sold');
    assert.equal(after.totals.cancelled, 1, 'the cancellation should still be visible as a count');

    // The BookingSeat row survives on purpose — it is the record of what was
    // paid — so this proves the filter is on booking status, not row existence.
    const rows = await prisma.bookingSeat.count({ where: { booking: { showId } } });
    assert.equal(rows, 4, 'the cancelled row should still exist for history');
  });

  test('re-pricing a category does not rewrite past revenue', async () => {
    const category = await prisma.seatCategory.findFirstOrThrow({
      where: { eventId, name: 'Premium' },
    });
    await prisma.seatCategory.update({ where: { id: category.id }, data: { price: '999.00' } });

    const body = await json(await get(`/organiser/events/${eventId}/summary`, organiser));
    assert.equal(
      body.totals.revenue,
      '449.48',
      'revenue moved when the price changed — it must come from priceAtBooking',
    );

    const premium = body.categories.find((c: { name: string }) => c.name === 'Premium');
    assert.equal(premium.currentPrice, '999');
    assert.equal(premium.revenue, '399.98', 'past sales must keep the price they were sold at');

    await prisma.seatCategory.update({ where: { id: category.id }, data: { price: PREMIUM } });
  });

  test('the per-show breakdown sums to the totals', async () => {
    const body = await json(await get(`/organiser/events/${eventId}/summary`, organiser));
    const showSum = body.shows.reduce(
      (n: number, s: { revenue: string }) => n + Number(s.revenue),
      0,
    );
    const catSum = body.categories.reduce(
      (n: number, c: { revenue: string }) => n + Number(c.revenue),
      0,
    );
    assert.equal(showSum.toFixed(2), Number(body.totals.revenue).toFixed(2));
    assert.equal(catSum.toFixed(2), Number(body.totals.revenue).toFixed(2));
  });
});
