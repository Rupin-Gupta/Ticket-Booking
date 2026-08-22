import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { after, before, describe, test } from 'node:test';
import type { Server } from 'node:http';
import type express from 'express';
import { createApp } from '../src/app.js';
import { prisma } from '../src/lib/prisma.js';

/**
 * Runs against the real database. Everything created here is tagged with RUN
 * and torn down in `after`, so a failed run cannot poison the next one.
 */
const RUN = randomBytes(5).toString('hex');
const tag = (s: string) => `${s} ${RUN}`;
const emailFor = (who: string) => `t2-${who}-${RUN}@example.test`;
const PASSWORD = 'correct horse battery';

let server: Server;
let base: string;

let adminToken: string;
let organiserToken: string;
let otherOrganiserToken: string;
let customerToken: string;
let venueId: string;
let eventId: string;

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

/** Registers a customer, then promotes it directly in the database — the API
 *  deliberately offers no way to do that, which is the behaviour under test. */
async function makeUser(who: string, role: 'ADMIN' | 'ORGANISER' | 'CUSTOMER') {
  const email = emailFor(who);
  const res = await json(await post('/auth/register', { email, password: PASSWORD, name: who }));
  if (role !== 'CUSTOMER') {
    await prisma.user.update({ where: { email }, data: { role } });
    // Re-login so the token carries the new role claim.
    return (await json(await post('/auth/login', { email, password: PASSWORD }))).accessToken;
  }
  return res.accessToken;
}

before(async () => {
  const app = createApp();
  server = await new Promise<Server>((resolve) => {
    const s = (app as express.Express).listen(0, () => resolve(s));
  });
  const addr = server.address();
  if (typeof addr === 'string' || addr === null) throw new Error('no port');
  base = `http://127.0.0.1:${addr.port}`;

  adminToken = await makeUser('admin', 'ADMIN');
  organiserToken = await makeUser('org', 'ORGANISER');
  otherOrganiserToken = await makeUser('org2', 'ORGANISER');
  customerToken = await makeUser('cust', 'CUSTOMER');
});

after(async () => {
  // Deepest first — foreign keys.
  const shows = await prisma.show.findMany({ where: { event: { title: { contains: RUN } } } });
  const showIds = shows.map((s) => s.id);
  await prisma.showSeat.deleteMany({ where: { showId: { in: showIds } } });
  await prisma.show.deleteMany({ where: { id: { in: showIds } } });
  await prisma.seatCategory.deleteMany({ where: { event: { title: { contains: RUN } } } });
  await prisma.event.deleteMany({ where: { title: { contains: RUN } } });
  await prisma.seat.deleteMany({ where: { venue: { name: { contains: RUN } } } });
  await prisma.venue.deleteMany({ where: { name: { contains: RUN } } });
  await prisma.user.deleteMany({ where: { email: { endsWith: `-${RUN}@example.test` } } });
  await prisma.$disconnect();
  server.close();
});

describe('venues', () => {
  test('only an admin can create one', async () => {
    assert.equal(
      (await post('/venues', { name: tag('V'), address: 'x' }, customerToken)).status,
      403,
    );
    assert.equal(
      (await post('/venues', { name: tag('V'), address: 'x' }, organiserToken)).status,
      403,
    );
    assert.equal((await post('/venues', { name: tag('V'), address: 'x' })).status, 401);

    const res = await post('/venues', { name: tag('Venue'), address: '1 Test Road' }, adminToken);
    assert.equal(res.status, 201);
    venueId = (await json(res)).venue.id;
  });

  test('a seat block generates rows x seatsPerRow seats', async () => {
    const res = await post(
      '/venues/:id/seats'.replace(':id', venueId),
      {
        section: 'Premium',
        rows: 3,
        seatsPerRow: 10,
      },
      adminToken,
    );
    assert.equal(res.status, 201);
    assert.equal((await json(res)).created, 30);

    const second = await post(
      `/venues/${venueId}/seats`,
      {
        section: 'Standard',
        rows: 4,
        seatsPerRow: 12,
      },
      adminToken,
    );
    assert.equal((await json(second)).created, 48);

    assert.equal(await prisma.seat.count({ where: { venueId } }), 78);
  });

  test('sections stack instead of overlapping', async () => {
    const premium = await prisma.seat.aggregate({
      where: { venueId, section: 'Premium' },
      _max: { posY: true },
    });
    const standard = await prisma.seat.aggregate({
      where: { venueId, section: 'Standard' },
      _min: { posY: true },
    });
    assert.ok(
      standard._min.posY! > premium._max.posY!,
      'Standard should start below the last Premium row',
    );
  });

  test('re-adding the same block is refused', async () => {
    const res = await post(
      `/venues/${venueId}/seats`,
      { section: 'Premium', rows: 3, seatsPerRow: 10 },
      adminToken,
    );
    assert.equal(res.status, 409);
    assert.equal((await json(res)).error.code, 'SEATS_ALREADY_EXIST');
  });
});

describe('events and pricing', () => {
  test('an organiser creates an event and owns it', async () => {
    const res = await post(
      '/events',
      {
        venueId,
        title: tag('Test Event'),
        type: 'MOVIE',
      },
      organiserToken,
    );
    assert.equal(res.status, 201);
    eventId = (await json(res)).event.id;
  });

  test('a different organiser cannot edit it — role alone is not enough', async () => {
    const res = await call(
      'PATCH',
      `/events/${eventId}`,
      { title: tag('Hijacked') },
      otherOrganiserToken,
    );
    assert.equal(res.status, 403);

    // And the row is untouched.
    const stored = await prisma.event.findUnique({
      where: { id: eventId },
      select: { title: true },
    });
    assert.equal(stored?.title, tag('Test Event'));
  });

  test('a category cannot claim a section the venue does not have', async () => {
    const res = await post(
      `/events/${eventId}/categories`,
      {
        name: 'Balcony',
        price: '900',
        sections: ['Rooftop'],
      },
      organiserToken,
    );
    assert.equal(res.status, 400);
    assert.equal((await json(res)).error.code, 'UNKNOWN_SECTION');
  });

  test('two categories cannot price the same section', async () => {
    assert.equal(
      (
        await post(
          `/events/${eventId}/categories`,
          { name: 'Front', price: '500', sections: ['Premium'] },
          organiserToken,
        )
      ).status,
      201,
    );
    const clash = await post(
      `/events/${eventId}/categories`,
      {
        name: 'AlsoFront',
        price: '600',
        sections: ['Premium'],
      },
      organiserToken,
    );
    assert.equal(clash.status, 409);
    assert.equal((await json(clash)).error.code, 'SECTION_ALREADY_PRICED');
  });
});

describe('shows and seat instantiation', () => {
  const startsAt = new Date(Date.now() + 7 * 86_400_000).toISOString();

  test('refuses to create a show while a section has no price', async () => {
    // "Standard" is still unpriced at this point.
    const res = await post(`/events/${eventId}/shows`, { startsAt }, organiserToken);
    assert.equal(res.status, 400);
    assert.equal((await json(res)).error.code, 'SECTION_NOT_PRICED');

    // The whole thing rolled back — no orphan show with an empty seat map.
    assert.equal(await prisma.show.count({ where: { eventId } }), 0);
  });

  test('generates exactly one ShowSeat per venue seat, priced by section', async () => {
    await post(
      `/events/${eventId}/categories`,
      { name: 'Rear', price: '200', sections: ['Standard'] },
      organiserToken,
    );

    const res = await post(`/events/${eventId}/shows`, { startsAt }, organiserToken);
    assert.equal(res.status, 201);

    const { show } = await json(res);
    const venueSeats = await prisma.seat.count({ where: { venueId } });
    assert.equal(show.seatCount, venueSeats, 'one ShowSeat per venue seat');
    assert.equal(await prisma.showSeat.count({ where: { showId: show.id } }), venueSeats);

    // Every seat is AVAILABLE, and priced by the category covering its section.
    const rows = await prisma.showSeat.findMany({
      where: { showId: show.id },
      select: {
        status: true,
        seat: { select: { section: true } },
        category: { select: { name: true } },
      },
    });
    assert.ok(rows.every((r) => r.status === 'AVAILABLE'));
    assert.ok(
      rows.every((r) =>
        r.seat.section === 'Premium' ? r.category.name === 'Front' : r.category.name === 'Rear',
      ),
      'each seat carries the category that claims its section',
    );
  });

  test('a past start date is rejected', async () => {
    const res = await post(
      `/events/${eventId}/shows`,
      {
        startsAt: new Date(Date.now() - 86_400_000).toISOString(),
      },
      organiserToken,
    );
    assert.equal(res.status, 400);
  });
});

describe('public browsing', () => {
  test('events are listable and filterable without a token', async () => {
    const all = await get(`/events?q=${encodeURIComponent(RUN)}`);
    assert.equal(all.status, 200);
    const body = await json(all);
    assert.equal(body.total, 1);
    assert.equal(body.events[0].title, tag('Test Event'));

    // Filter that should exclude it.
    const concerts = await json(await get(`/events?q=${encodeURIComponent(RUN)}&type=CONCERT`));
    assert.equal(concerts.total, 0);
  });

  test('event detail exposes categories and upcoming shows', async () => {
    const body = await json(await get(`/events/${eventId}`));
    assert.equal(body.event.categories.length, 2);
    assert.equal(body.event.shows.length, 1);
    assert.ok(body.event.venue.name.includes(RUN));
  });
});
