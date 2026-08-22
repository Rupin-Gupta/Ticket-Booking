import { Prisma } from '@prisma/client';
import { prisma } from '../../lib/prisma.js';
import { ApiError } from '../../lib/errors.js';
import { compact } from '../../lib/http.js';
import type { AddSeatBlockInput, CreateVenueInput, UpdateVenueInput } from './schema.js';

export const listVenues = () =>
  prisma.venue.findMany({
    select: { id: true, name: true, address: true, _count: { select: { seats: true } } },
    orderBy: { name: 'asc' },
  });

export async function getVenue(id: string) {
  const venue = await prisma.venue.findUnique({
    where: { id },
    select: {
      id: true,
      name: true,
      address: true,
      seats: {
        select: { id: true, section: true, row: true, number: true, posX: true, posY: true },
        orderBy: [{ posY: 'asc' }, { posX: 'asc' }],
      },
    },
  });
  if (!venue) throw ApiError.notFound('VENUE_NOT_FOUND', 'No venue with that id.');
  return venue;
}

export const createVenue = (input: CreateVenueInput) =>
  prisma.venue.create({ data: input, select: { id: true, name: true, address: true } });

export async function updateVenue(id: string, input: UpdateVenueInput) {
  await getVenue(id); // 404 before 500
  return prisma.venue.update({
    where: { id },
    data: compact(input),
    select: { id: true, name: true, address: true },
  });
}

const ROW_LABELS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

/**
 * Generates a rectangular block of seats and appends it below whatever the
 * venue already has.
 *
 * posX / posY are grid coordinates, not pixels — the frontend decides how big
 * a seat is. New blocks start two rows below the lowest existing seat so
 * sections stack visually instead of overlapping, and the caller never has to
 * work out an offset.
 */
export async function addSeatBlock(venueId: string, input: AddSeatBlockInput) {
  await getVenue(venueId);

  const lowest = await prisma.seat.aggregate({
    where: { venueId },
    _max: { posY: true },
  });
  const startY = lowest._max.posY === null ? 0 : lowest._max.posY + 2;

  const seats: Prisma.SeatCreateManyInput[] = [];
  for (let r = 0; r < input.rows; r++) {
    for (let n = 1; n <= input.seatsPerRow; n++) {
      seats.push({
        venueId,
        section: input.section,
        row: ROW_LABELS[r]!,
        number: n,
        // Centre each row on x = 0 so rows of different widths stay aligned.
        posX: n - (input.seatsPerRow + 1) / 2,
        posY: startY + r,
      });
    }
  }

  try {
    const { count } = await prisma.seat.createMany({ data: seats });
    return { created: count, section: input.section, startY };
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

/** Distinct section names in a venue — what a category is allowed to claim. */
export async function listSections(venueId: string): Promise<string[]> {
  const rows = await prisma.seat.findMany({
    where: { venueId },
    select: { section: true },
    distinct: ['section'],
    orderBy: { section: 'asc' },
  });
  return rows.map((r) => r.section);
}
