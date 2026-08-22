import { Prisma } from '@prisma/client';
import type { Role } from '@ticket/shared';
import { prisma } from '../../lib/prisma.js';
import { ApiError } from '../../lib/errors.js';
import { compact } from '../../lib/http.js';
import type {
  CreateCategoryInput,
  CreateEventInput,
  CreateShowInput,
  ListEventsQuery,
  UpdateEventInput,
} from './schema.js';

type Caller = { sub: string; role: Role };

/**
 * Role alone is not authorisation. requireRole(['ORGANISER']) says "some
 * organiser"; this says "the organiser who owns this event". Without it any
 * organiser can edit any other organiser's event and read their revenue.
 *
 * ADMIN passes deliberately — an admin exists to fix things.
 */
async function assertOwns(eventId: string, caller: Caller) {
  const event = await prisma.event.findUnique({
    where: { id: eventId },
    select: { id: true, organiserId: true, venueId: true },
  });
  if (!event) throw ApiError.notFound('EVENT_NOT_FOUND', 'No event with that id.');
  if (caller.role !== 'ADMIN' && event.organiserId !== caller.sub) {
    throw ApiError.forbidden('This event belongs to another organiser.');
  }
  return event;
}

/* ------------------------------------------------------------------ events */

export async function listEvents(query: ListEventsQuery) {
  const showWindow =
    query.from || query.to
      ? {
          shows: {
            some: {
              startsAt: {
                ...(query.from ? { gte: query.from } : {}),
                ...(query.to ? { lte: query.to } : {}),
              },
            },
          },
        }
      : {};

  const where: Prisma.EventWhereInput = {
    ...(query.type ? { type: query.type } : {}),
    ...(query.venueId ? { venueId: query.venueId } : {}),
    ...(query.q ? { title: { contains: query.q, mode: 'insensitive' } } : {}),
    ...showWindow,
  };

  const [events, total] = await Promise.all([
    prisma.event.findMany({
      where,
      take: query.limit,
      skip: query.offset,
      orderBy: { title: 'asc' },
      select: {
        id: true,
        title: true,
        type: true,
        description: true,
        venue: { select: { id: true, name: true, address: true } },
        organiser: { select: { id: true, name: true } },
        categories: { select: { id: true, name: true, price: true }, orderBy: { price: 'desc' } },
        // Only upcoming shows: a listing full of last month's dates is noise.
        shows: {
          where: { startsAt: { gte: new Date() } },
          select: { id: true, startsAt: true },
          orderBy: { startsAt: 'asc' },
          take: 5,
        },
      },
    }),
    prisma.event.count({ where }),
  ]);

  return { events, total, limit: query.limit, offset: query.offset };
}

export async function getEvent(id: string) {
  const event = await prisma.event.findUnique({
    where: { id },
    select: {
      id: true,
      title: true,
      type: true,
      description: true,
      venue: { select: { id: true, name: true, address: true } },
      organiser: { select: { id: true, name: true } },
      categories: {
        select: { id: true, name: true, price: true, sections: true },
        orderBy: { price: 'desc' },
      },
      shows: {
        where: { startsAt: { gte: new Date() } },
        select: { id: true, startsAt: true, _count: { select: { showSeats: true } } },
        orderBy: { startsAt: 'asc' },
      },
    },
  });
  if (!event) throw ApiError.notFound('EVENT_NOT_FOUND', 'No event with that id.');
  return event;
}

export async function createEvent(input: CreateEventInput, caller: Caller) {
  const venue = await prisma.venue.findUnique({
    where: { id: input.venueId },
    select: { id: true },
  });
  if (!venue) throw ApiError.badRequest('VENUE_NOT_FOUND', 'No venue with that id.');

  return prisma.event.create({
    data: { ...compact(input), organiserId: caller.sub },
    select: { id: true, title: true, type: true, description: true, venueId: true },
  });
}

export async function updateEvent(id: string, input: UpdateEventInput, caller: Caller) {
  await assertOwns(id, caller);
  return prisma.event.update({
    where: { id },
    data: compact(input),
    select: { id: true, title: true, type: true, description: true, venueId: true },
  });
}

export async function listOwnEvents(caller: Caller) {
  return prisma.event.findMany({
    where: caller.role === 'ADMIN' ? {} : { organiserId: caller.sub },
    orderBy: { title: 'asc' },
    select: {
      id: true,
      title: true,
      type: true,
      venue: { select: { id: true, name: true } },
      categories: { select: { id: true, name: true, price: true, sections: true } },
      _count: { select: { shows: true } },
    },
  });
}

/* -------------------------------------------------------------- categories */

export async function createCategory(eventId: string, input: CreateCategoryInput, caller: Caller) {
  const event = await assertOwns(eventId, caller);

  const [venueSections, existing] = await Promise.all([
    prisma.seat
      .findMany({
        where: { venueId: event.venueId },
        select: { section: true },
        distinct: ['section'],
      })
      .then((rows) => rows.map((r) => r.section)),
    prisma.seatCategory.findMany({ where: { eventId }, select: { name: true, sections: true } }),
  ]);

  if (venueSections.length === 0) {
    throw ApiError.badRequest(
      'VENUE_HAS_NO_SEATS',
      'Add seats to the venue before pricing its sections.',
    );
  }

  // Catching this here means instantiateShowSeats() never meets a seat it
  // cannot price, which is a far worse place to discover the problem.
  const unknown = input.sections.filter((s) => !venueSections.includes(s));
  if (unknown.length > 0) {
    throw ApiError.badRequest(
      'UNKNOWN_SECTION',
      `This venue has no section named ${unknown.join(', ')}. It has: ${venueSections.join(', ')}.`,
    );
  }

  // Two categories claiming one section would make a seat's price ambiguous.
  const claimed = new Map<string, string>();
  for (const category of existing) {
    for (const section of category.sections) claimed.set(section, category.name);
  }
  const taken = input.sections.filter((s) => claimed.has(s));
  if (taken.length > 0) {
    throw ApiError.conflict(
      'SECTION_ALREADY_PRICED',
      `${taken.map((s) => `"${s}" is already priced by ${claimed.get(s)}`).join('; ')}.`,
    );
  }

  try {
    return await prisma.seatCategory.create({
      data: { eventId, name: input.name, price: input.price, sections: input.sections },
      select: { id: true, name: true, price: true, sections: true },
    });
  } catch (err) {
    if (err instanceof Prisma.PrismaClientKnownRequestError && err.code === 'P2002') {
      throw ApiError.conflict(
        'CATEGORY_EXISTS',
        `This event already has a "${input.name}" category.`,
      );
    }
    throw err;
  }
}

/* ------------------------------------------------------------------- shows */

export async function createShow(eventId: string, input: CreateShowInput, caller: Caller) {
  const event = await assertOwns(eventId, caller);

  // One transaction: a show whose seats failed to generate is worse than no
  // show at all — it renders as a bookable date with an empty seat map.
  return prisma.$transaction(async (tx) => {
    const show = await tx.show.create({
      data: { eventId, startsAt: input.startsAt },
      select: { id: true, startsAt: true },
    });

    const seatCount = await instantiateShowSeats(tx, {
      showId: show.id,
      eventId,
      venueId: event.venueId,
    });

    return { ...show, seatCount };
  });
}

/**
 * Materialises one ShowSeat per venue seat, priced by whichever category
 * claims that seat's section.
 *
 * A physical Seat carries no status — a chair does not know whether it is
 * sold. These rows are what every hold, booking and waitlist offer locks, and
 * they exist from the moment the show does so the seat map is never partial.
 *
 * Runs inside the caller's transaction; @@unique([showId, seatId]) makes a
 * double-instantiation impossible rather than merely unlikely.
 */
export async function instantiateShowSeats(
  tx: Prisma.TransactionClient,
  ids: { showId: string; eventId: string; venueId: string },
) {
  const [seats, categories] = await Promise.all([
    tx.seat.findMany({ where: { venueId: ids.venueId }, select: { id: true, section: true } }),
    tx.seatCategory.findMany({
      where: { eventId: ids.eventId },
      select: { id: true, sections: true },
    }),
  ]);

  if (seats.length === 0) {
    throw ApiError.badRequest('VENUE_HAS_NO_SEATS', 'This venue has no seats yet.');
  }

  const categoryForSection = new Map<string, string>();
  for (const category of categories) {
    for (const section of category.sections) categoryForSection.set(section, category.id);
  }

  const unpriced = new Set<string>();
  const rows: Prisma.ShowSeatCreateManyInput[] = [];
  for (const seat of seats) {
    const categoryId = categoryForSection.get(seat.section);
    if (!categoryId) {
      unpriced.add(seat.section);
      continue;
    }
    rows.push({ showId: ids.showId, seatId: seat.id, categoryId });
  }

  // Refuse rather than generate a half-priced seat map. Every seat must have
  // a price before anyone can be sold one.
  if (unpriced.size > 0) {
    throw ApiError.badRequest(
      'SECTION_NOT_PRICED',
      `No category covers ${[...unpriced].join(', ')}. Add one before creating a show.`,
    );
  }

  await tx.showSeat.createMany({ data: rows });
  return rows.length;
}

export async function getShow(id: string) {
  const show = await prisma.show.findUnique({
    where: { id },
    select: {
      id: true,
      startsAt: true,
      event: {
        select: {
          id: true,
          title: true,
          type: true,
          venue: { select: { id: true, name: true, address: true } },
          categories: { select: { id: true, name: true, price: true }, orderBy: { price: 'desc' } },
        },
      },
      _count: { select: { showSeats: true } },
    },
  });
  if (!show) throw ApiError.notFound('SHOW_NOT_FOUND', 'No show with that id.');
  return show;
}
