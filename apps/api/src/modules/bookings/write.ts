import { Prisma } from '@prisma/client';
import { bookingReference, randomToken } from '../../lib/qr.js';

/**
 * Booking creation, extracted so the checkout path and the waitlist-offer path
 * share one implementation.
 *
 * It lives here rather than in `service.ts` to keep the import graph acyclic:
 * bookings/service imports waitlist/service for advanceWaitlist(), so
 * waitlist/service must not import bookings/service back. Both import this.
 *
 * Rule 2 in spirit — there is one "turn seats into a booking", never two that
 * can drift apart.
 */

export const bookingSelect = {
  id: true,
  reference: true,
  status: true,
  createdAt: true,
  cancelledAt: true,
  show: {
    select: {
      id: true,
      startsAt: true,
      event: {
        select: {
          id: true,
          title: true,
          type: true,
          venue: { select: { name: true, address: true } },
        },
      },
    },
  },
  seats: {
    select: {
      priceAtBooking: true,
      showSeat: {
        select: { id: true, seat: { select: { section: true, row: true, number: true } } },
      },
    },
  },
} satisfies Prisma.BookingSelect;

export type BookingRow = Prisma.BookingGetPayload<{ select: typeof bookingSelect }>;

/**
 * `qrToken` is deliberately absent unless explicitly asked for. It is a bearer
 * credential for entry, so it travels in the emailed QR and on the single
 * booking a customer opens — never in a list.
 */
export function toBookingView(booking: BookingRow, opts: { includeQr?: string } = {}) {
  const total = booking.seats.reduce((sum, s) => sum.add(s.priceAtBooking), new Prisma.Decimal(0));
  return {
    id: booking.id,
    reference: booking.reference,
    status: booking.status,
    createdAt: booking.createdAt.toISOString(),
    cancelledAt: booking.cancelledAt?.toISOString() ?? null,
    show: {
      id: booking.show.id,
      startsAt: booking.show.startsAt.toISOString(),
      eventId: booking.show.event.id,
      title: booking.show.event.title,
      type: booking.show.event.type,
      venue: booking.show.event.venue.name,
      address: booking.show.event.venue.address,
    },
    seats: booking.seats.map((s) => ({
      showSeatId: s.showSeat.id,
      label: `${s.showSeat.seat.row}${s.showSeat.seat.number}`,
      section: s.showSeat.seat.section,
      price: s.priceAtBooking.toString(),
    })),
    total: total.toString(),
    ...(opts.includeQr ? { qrToken: opts.includeQr } : {}),
  };
}

/**
 * Writes the booking and flips its seats to BOOKED. The caller must already
 * hold row locks on those seats and have verified they are claimable — this
 * function does no checking of its own, on purpose, because the two callers
 * verify different things (a live hold vs. a valid offer).
 */
export async function writeBooking(
  tx: Prisma.TransactionClient,
  input: { showId: string; customerId: string; seats: { id: string; categoryId: string }[] },
): Promise<BookingRow> {
  // Price is read now and frozen onto each row. An organiser re-pricing a
  // category next week must not rewrite what this booking was worth.
  const categories = await tx.seatCategory.findMany({
    where: { id: { in: [...new Set(input.seats.map((s) => s.categoryId))] } },
    select: { id: true, price: true },
  });
  const priceOf = new Map(categories.map((c) => [c.id, c.price]));

  const booking = await tx.booking.create({
    data: {
      reference: bookingReference(),
      qrToken: randomToken(),
      customerId: input.customerId,
      showId: input.showId,
      seats: {
        create: input.seats.map((s) => ({
          showSeatId: s.id,
          priceAtBooking: priceOf.get(s.categoryId)!,
        })),
      },
    },
    select: bookingSelect,
  });

  await tx.showSeat.updateMany({
    where: { id: { in: input.seats.map((s) => s.id) } },
    data: { status: 'BOOKED', heldByUserId: null, holdExpiresAt: null, offerExpiresAt: null },
  });

  return booking;
}
