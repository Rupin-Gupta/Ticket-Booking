import { Prisma } from '@prisma/client';
import type { Role } from '@ticket/shared';
import { prisma } from '../../lib/prisma.js';
import { ApiError } from '../../lib/errors.js';

type Caller = { sub: string; role: Role };

const ZERO = new Prisma.Decimal(0);

/**
 * Revenue and sales for one event.
 *
 * Money is summed from `BookingSeat.priceAtBooking`, never from the category's
 * current price. Those are different numbers the moment an organiser re-prices
 * anything, and the one the customer actually paid is the one on the row.
 *
 * Cancelled bookings are excluded by filtering on the booking's status rather
 * than on `releasedAt`: status is the authoritative record of whether money was
 * kept, and `releasedAt` exists to free the seat, which is a related but
 * separate fact.
 */
export async function eventSummary(eventId: string, caller: Caller) {
  const event = await prisma.event.findUnique({
    where: { id: eventId },
    select: {
      id: true,
      title: true,
      type: true,
      organiserId: true,
      venue: { select: { name: true } },
      categories: { select: { id: true, name: true, price: true }, orderBy: { price: 'desc' } },
      shows: { select: { id: true, startsAt: true }, orderBy: { startsAt: 'asc' } },
    },
  });
  if (!event) throw ApiError.notFound('EVENT_NOT_FOUND', 'No event with that id.');

  // Role says "some organiser"; this says "the organiser who owns this event".
  // Without it any organiser could read any other organiser's revenue.
  if (caller.role !== 'ADMIN' && event.organiserId !== caller.sub) {
    throw ApiError.forbidden('This event belongs to another organiser.');
  }

  const showIds = event.shows.map((s) => s.id);

  const [sold, capacity, bookings, waiting] = await Promise.all([
    // ponytail: aggregated in JS rather than SQL. Prisma cannot group by a
    // relation's column, and an event has hundreds of seats, not millions.
    // Move to a raw GROUP BY if a venue ever gets big enough to notice.
    prisma.bookingSeat.findMany({
      where: { booking: { showId: { in: showIds }, status: 'CONFIRMED' } },
      select: {
        priceAtBooking: true,
        showSeat: { select: { showId: true, categoryId: true } },
      },
    }),
    prisma.showSeat.groupBy({
      by: ['showId', 'categoryId'],
      where: { showId: { in: showIds } },
      _count: { _all: true },
    }),
    prisma.booking.groupBy({
      by: ['showId', 'status'],
      where: { showId: { in: showIds } },
      _count: { _all: true },
    }),
    prisma.waitlistEntry.groupBy({
      by: ['categoryId'],
      where: { showId: { in: showIds }, status: 'WAITING' },
      _count: { _all: true },
    }),
  ]);

  const key = (showId: string, categoryId: string) => `${showId}::${categoryId}`;

  const soldByCell = new Map<string, { seats: number; revenue: Prisma.Decimal }>();
  for (const row of sold) {
    const k = key(row.showSeat.showId, row.showSeat.categoryId);
    const cell = soldByCell.get(k) ?? { seats: 0, revenue: ZERO };
    soldByCell.set(k, {
      seats: cell.seats + 1,
      // Decimal arithmetic throughout. A float cannot hold 0.10, and revenue
      // that is off by a cent is revenue somebody will ask about.
      revenue: cell.revenue.add(row.priceAtBooking),
    });
  }

  const capacityByCell = new Map(capacity.map((c) => [key(c.showId, c.categoryId), c._count._all]));

  const perCategory = event.categories.map((category) => {
    let seats = 0;
    let seatsSold = 0;
    let revenue = ZERO;
    for (const show of event.shows) {
      const k = key(show.id, category.id);
      seats += capacityByCell.get(k) ?? 0;
      const cell = soldByCell.get(k);
      if (cell) {
        seatsSold += cell.seats;
        revenue = revenue.add(cell.revenue);
      }
    }
    return {
      id: category.id,
      name: category.name,
      currentPrice: category.price.toString(),
      capacity: seats,
      seatsSold,
      revenue: revenue.toString(),
      waiting: waiting.find((w) => w.categoryId === category.id)?._count._all ?? 0,
    };
  });

  const perShow = event.shows.map((show) => {
    let seats = 0;
    let seatsSold = 0;
    let revenue = ZERO;
    for (const category of event.categories) {
      const k = key(show.id, category.id);
      seats += capacityByCell.get(k) ?? 0;
      const cell = soldByCell.get(k);
      if (cell) {
        seatsSold += cell.seats;
        revenue = revenue.add(cell.revenue);
      }
    }
    const rows = bookings.filter((b) => b.showId === show.id);
    return {
      id: show.id,
      startsAt: show.startsAt.toISOString(),
      capacity: seats,
      seatsSold,
      revenue: revenue.toString(),
      bookings: rows.find((r) => r.status === 'CONFIRMED')?._count._all ?? 0,
      cancelled: rows.find((r) => r.status === 'CANCELLED')?._count._all ?? 0,
    };
  });

  const totalRevenue = perShow.reduce((sum, s) => sum.add(s.revenue), ZERO);
  const totalCapacity = perShow.reduce((n, s) => n + s.capacity, 0);
  const totalSold = perShow.reduce((n, s) => n + s.seatsSold, 0);

  return {
    event: {
      id: event.id,
      title: event.title,
      type: event.type,
      venue: event.venue.name,
    },
    totals: {
      revenue: totalRevenue.toString(),
      capacity: totalCapacity,
      seatsSold: totalSold,
      // Guarded: an event with no shows yet has no capacity, and x/0 is not a
      // number a dashboard should ever render.
      percentSold: totalCapacity === 0 ? 0 : Math.round((totalSold / totalCapacity) * 100),
      bookings: perShow.reduce((n, s) => n + s.bookings, 0),
      cancelled: perShow.reduce((n, s) => n + s.cancelled, 0),
      waiting: perCategory.reduce((n, c) => n + c.waiting, 0),
    },
    categories: perCategory,
    shows: perShow,
  };
}
