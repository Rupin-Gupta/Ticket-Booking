import { Prisma } from '@prisma/client';
import type { Role } from '@ticket/shared';
import { prisma } from '../../lib/prisma.js';
import { ApiError } from '../../lib/errors.js';
import { enqueueEmail } from '../../jobs/email.queue.js';
import { advanceWaitlist, type PendingOffer } from '../waitlist/service.js';
import { bookingSelect, toBookingView, writeBooking, type BookingRow } from './write.js';

type Caller = { sub: string; role: Role };

/* ---------------------------------------------------------------- booking */

/**
 * Turns held seats into a confirmed booking.
 *
 * Same locking discipline as the hold path, for the same reason: lock, re-read
 * under the lock, verify, write. The extra condition here is ownership — the
 * seats must be held *by this caller* and still unexpired. Without that check
 * anyone could book seats somebody else is in the middle of paying for.
 */
export async function createBooking(showId: string, seatIds: string[], caller: Caller) {
  type LockedRow = {
    id: string;
    status: string;
    heldByUserId: string | null;
    holdExpiresAt: Date | null;
    categoryId: string;
    seatRow: string;
    seatNumber: number;
  };

  const booking = await prisma.$transaction(
    async (tx) => {
      const rows = await tx.$queryRaw<LockedRow[]>`
        SELECT ss.id,
               ss.status::text     AS status,
               ss."heldByUserId",
               ss."holdExpiresAt",
               ss."categoryId",
               s.row               AS "seatRow",
               s.number            AS "seatNumber"
        FROM "ShowSeat" ss
        JOIN "Seat" s ON s.id = ss."seatId"
        WHERE ss.id = ANY(${seatIds}::text[])
          AND ss."showId" = ${showId}
        ORDER BY ss.id
        FOR UPDATE OF ss`;

      if (rows.length !== seatIds.length) {
        throw ApiError.notFound(
          'SEAT_NOT_FOUND',
          'One or more of those seats are not in this show.',
        );
      }

      const now = new Date();
      const notMine = rows.filter(
        (r) =>
          r.status !== 'HELD' ||
          r.heldByUserId !== caller.sub ||
          r.holdExpiresAt === null ||
          r.holdExpiresAt.getTime() <= now.getTime(),
      );
      if (notMine.length > 0) {
        const names = notMine.map((r) => `${r.seatRow}${r.seatNumber}`).join(', ');
        throw ApiError.conflict(
          'HOLD_NOT_VALID',
          `Your hold on ${names} has expired or was never yours. Pick the seats again.`,
        );
      }

      return writeBooking(tx, {
        showId,
        customerId: caller.sub,
        seats: rows.map((r) => ({ id: r.id, categoryId: r.categoryId })),
      });
    },
    { maxWait: 15_000, timeout: 20_000 },
  );

  // Queued AFTER the transaction commits, and deliberately not awaited into
  // the response's success. The seat is confirmed in Postgres; the email is
  // allowed to be a second late, and a mail provider must never be able to
  // fail a booking the customer has already made.
  void enqueueEmail({ kind: 'booking-confirmed', bookingId: booking.id });

  return toBookingView(booking);
}

/* ---------------------------------------------------------------- reading */

export async function listMyBookings(caller: Caller) {
  const rows = await prisma.booking.findMany({
    where: { customerId: caller.sub },
    select: bookingSelect,
    orderBy: { createdAt: 'desc' },
  });
  return rows.map((r) => toBookingView(r));
}

export async function getBooking(id: string, caller: Caller) {
  const booking = await prisma.booking.findUnique({
    where: { id },
    select: { ...bookingSelect, customerId: true, qrToken: true },
  });
  if (!booking) throw ApiError.notFound('BOOKING_NOT_FOUND', 'No booking with that reference.');

  // Owner-checked, not merely authenticated. Booking ids are uuids, but "hard
  // to guess" is not an access control.
  if (caller.role !== 'ADMIN' && booking.customerId !== caller.sub) {
    throw ApiError.forbidden('That booking belongs to someone else.');
  }

  return toBookingView(booking, {
    ...(booking.status === 'CONFIRMED' ? { includeQr: booking.qrToken } : {}),
  });
}

/* ------------------------------------------------------------- cancelling */

export async function cancelBooking(id: string, caller: Caller) {
  const freed = await prisma.$transaction(
    async (tx) => {
      const booking = await tx.booking.findUnique({
        where: { id },
        select: {
          id: true,
          status: true,
          customerId: true,
          show: { select: { startsAt: true } },
          seats: { select: { showSeatId: true } },
        },
      });
      if (!booking) throw ApiError.notFound('BOOKING_NOT_FOUND', 'No booking with that reference.');
      if (caller.role !== 'ADMIN' && booking.customerId !== caller.sub) {
        throw ApiError.forbidden('That booking belongs to someone else.');
      }
      if (booking.status === 'CANCELLED') {
        throw ApiError.conflict('ALREADY_CANCELLED', 'That booking is already cancelled.');
      }
      // Releasing a seat after the doors open helps nobody and would put a
      // seat back on sale for a show already under way.
      if (booking.show.startsAt.getTime() <= Date.now()) {
        throw ApiError.conflict('SHOW_ALREADY_STARTED', 'This show has already started.');
      }

      const now = new Date();
      await tx.booking.update({
        where: { id },
        data: { status: 'CANCELLED', cancelledAt: now },
      });

      // Release the claim without deleting the row: the price paid is revenue
      // history and the cancellation email still needs the seat labels. The
      // partial unique index only counts rows where releasedAt IS NULL, so
      // clearing it here is what lets the seat be sold again.
      await tx.bookingSeat.updateMany({ where: { bookingId: id }, data: { releasedAt: now } });

      const showSeatIds = booking.seats.map((s) => s.showSeatId);

      // Each freed seat goes to the next person in line, not straight back on
      // sale. Same function the offer sweeper calls — rule 3.
      const offers: PendingOffer[] = [];
      for (const showSeatId of showSeatIds) {
        const pending = await advanceWaitlist(tx, showSeatId);
        if (pending) offers.push(pending);
      }

      return { showSeatIds, offers };
    },
    { maxWait: 15_000, timeout: 20_000 },
  );

  void enqueueEmail({ kind: 'booking-cancelled', bookingId: id });
  // Offer emails go out after the transaction commits. Sending inside it would
  // tell somebody about a seat a rollback then takes back.
  for (const offer of freed.offers) {
    void enqueueEmail({ kind: 'waitlist-offer', entryId: offer.entryId });
  }

  return {
    cancelled: true,
    seatsReleased: freed.showSeatIds.length,
    offeredToWaitlist: freed.offers.length,
  };
}

/* ----------------------------------------------------------- verification */

/**
 * What a scanned QR resolves to. Public by necessity — the person on the door
 * is not logged in — so it returns only what a door needs: is this ticket
 * real, for which show, and which seats.
 *
 * Never the customer's email or name. A QR code is a thing people photograph
 * and forward.
 */
export async function verifyTicket(qrToken: string) {
  const booking = await prisma.booking.findUnique({
    where: { qrToken },
    select: {
      reference: true,
      status: true,
      show: {
        select: {
          startsAt: true,
          event: { select: { title: true, venue: { select: { name: true } } } },
        },
      },
      seats: {
        select: { showSeat: { select: { seat: { select: { row: true, number: true } } } } },
      },
    },
  });

  if (!booking) {
    // A wrong token and a cancelled booking are different facts, and the door
    // staff need to tell them apart.
    throw ApiError.notFound('TICKET_NOT_FOUND', 'This ticket is not recognised.');
  }

  return {
    valid: booking.status === 'CONFIRMED',
    status: booking.status,
    reference: booking.reference,
    eventTitle: booking.show.event.title,
    venue: booking.show.event.venue.name,
    startsAt: booking.show.startsAt.toISOString(),
    seats: booking.seats.map((s) => `${s.showSeat.seat.row}${s.showSeat.seat.number}`),
  };
}
