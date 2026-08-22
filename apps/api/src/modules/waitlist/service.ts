import { Prisma } from '@prisma/client';
import type { Role } from '@ticket/shared';
import { prisma } from '../../lib/prisma.js';
import { ApiError } from '../../lib/errors.js';
import { env } from '../../env.js';
import { randomToken } from '../../lib/qr.js';
import { writeBooking, toBookingView } from '../bookings/write.js';
import { broadcastSeats, broadcastStatus } from '../../realtime/emit.js';

type Caller = { sub: string; role: Role };

/** An offer that was just created and whose email still has to be sent. */
export type PendingOffer = { entryId: string; showSeatId: string };

/**
 * Offer the freed seat to the next person in line.
 *
 * **This is the only implementation of "a seat became free, find the next
 * customer".** Booking cancellation calls it, and so does offer expiry. Rule 3
 * exists because two copies drift: a fix to the ordering or the SKIP LOCKED
 * clause lands in one and not the other, and the bug then only shows up on
 * whichever path is rarer and less tested.
 *
 * Runs inside the caller's transaction. Returns the offer that needs an email,
 * or null if the queue was empty and the seat went back on general sale — the
 * caller sends after commit, never inside it.
 */
export async function advanceWaitlist(
  tx: Prisma.TransactionClient,
  showSeatId: string,
): Promise<PendingOffer | null> {
  // Lock the seat first. Without this, two cancellations freeing seats in the
  // same category could each read an empty-looking queue.
  const locked = await tx.$queryRaw<
    { id: string; showId: string; categoryId: string; status: string }[]
  >`
    SELECT id, "showId", "categoryId", status::text AS status
    FROM "ShowSeat"
    WHERE id = ${showSeatId}
    FOR UPDATE`;

  const seat = locked[0];
  if (!seat) return null;

  // Every legitimate caller passes a seat that is BOOKED (a cancellation) or
  // OFFERED (an offer that lapsed or was given up). A HELD seat means somebody
  // is mid-checkout, and a future caller passing one here would silently take a
  // live hold away from a paying customer. Refuse rather than trust the caller.
  if (seat.status === 'HELD' || seat.status === 'AVAILABLE') {
    throw new Error(
      `advanceWaitlist called on a ${seat.status} seat (${showSeatId}) — ` +
        'it must only be given a seat that has just been freed.',
    );
  }

  /*
   * FIFO by joinedAt — the queue's whole promise.
   *
   * SKIP LOCKED is what makes concurrent advances safe: if another transaction
   * is already offering this same person a different seat, we step over them
   * and take the next in line instead of blocking and then handing one customer
   * two offers. Plain FOR UPDATE would serialise here and, worse, could wake up
   * to find the row already OFFERED.
   */
  const next = await tx.$queryRaw<{ id: string; customerId: string }[]>`
    SELECT id, "customerId"
    FROM "WaitlistEntry"
    WHERE "showId" = ${seat.showId}
      AND "categoryId" = ${seat.categoryId}
      AND status = 'WAITING'
    ORDER BY "joinedAt" ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED`;

  const entry = next[0];

  if (!entry) {
    // Nobody waiting — back on general sale.
    await tx.showSeat.update({
      where: { id: showSeatId },
      data: { status: 'AVAILABLE', heldByUserId: null, holdExpiresAt: null, offerExpiresAt: null },
    });
    return null;
  }

  const expiresAt = new Date(Date.now() + env.OFFER_TTL_SECONDS * 1000);

  // OFFERED, not HELD. The two expire differently — an expired hold goes back
  // to AVAILABLE, an expired offer has to walk the queue — and collapsing them
  // would make the sweeper guess which kind of expiry it found (ADR-002).
  await tx.showSeat.update({
    where: { id: showSeatId },
    data: {
      status: 'OFFERED',
      heldByUserId: null,
      holdExpiresAt: null,
      offerExpiresAt: expiresAt,
    },
  });

  await tx.waitlistEntry.update({
    where: { id: entry.id },
    data: {
      status: 'OFFERED',
      offeredSeatId: showSeatId,
      // A bearer credential for a real seat: 32 CSPRNG bytes, single use,
      // time-limited, and checked against the logged-in customer on accept.
      offerToken: randomToken(),
      offerExpiresAt: expiresAt,
    },
  });

  return { entryId: entry.id, showSeatId };
}

/* ------------------------------------------------------------------ joining */

/** A category is sold out when it has no seat a customer could take right now. */
async function availableInCategory(showId: string, categoryId: string) {
  const now = new Date();
  return prisma.showSeat.count({
    where: {
      showId,
      categoryId,
      OR: [
        { status: 'AVAILABLE' },
        // An expired lease is free even if nothing has swept it yet, so it must
        // count here too — otherwise a stale row makes a category look sold out
        // and pushes someone into a queue they do not belong in.
        { status: 'HELD', holdExpiresAt: { lt: now } },
        { status: 'OFFERED', offerExpiresAt: { lt: now } },
      ],
    },
  });
}

export async function join(showId: string, categoryId: string, caller: Caller) {
  const category = await prisma.seatCategory.findFirst({
    where: { id: categoryId, showSeats: { some: { showId } } },
    select: { id: true, name: true },
  });
  if (!category) {
    throw ApiError.badRequest('CATEGORY_NOT_IN_SHOW', 'That category is not part of this show.');
  }

  if ((await availableInCategory(showId, categoryId)) > 0) {
    throw ApiError.conflict(
      'SEATS_STILL_AVAILABLE',
      `${category.name} still has seats. Book one instead of waiting.`,
    );
  }

  // Refreshing the page must not buy a third place in line. Only live states
  // block — a previous entry that expired or was cancelled should not lock
  // somebody out forever.
  const existing = await prisma.waitlistEntry.findFirst({
    where: {
      showId,
      categoryId,
      customerId: caller.sub,
      status: { in: ['WAITING', 'OFFERED'] },
    },
    select: { id: true, status: true },
  });
  if (existing) {
    throw ApiError.conflict(
      'ALREADY_WAITING',
      existing.status === 'OFFERED'
        ? 'You already have a seat offered to you for this category.'
        : 'You are already on the waitlist for this category.',
    );
  }

  const entry = await prisma.waitlistEntry.create({
    data: { showId, categoryId, customerId: caller.sub },
    select: { id: true, joinedAt: true },
  });

  return { id: entry.id, position: await positionOf(showId, categoryId, entry.joinedAt) };
}

/** How many people are ahead. Derived from joinedAt, never stored — a stored
 *  position would need rewriting for everyone behind on every departure. */
const positionOf = (showId: string, categoryId: string, joinedAt: Date) =>
  prisma.waitlistEntry
    .count({
      where: { showId, categoryId, status: 'WAITING', joinedAt: { lt: joinedAt } },
    })
    .then((ahead) => ahead + 1);

export async function listMine(caller: Caller) {
  const rows = await prisma.waitlistEntry.findMany({
    where: { customerId: caller.sub, status: { in: ['WAITING', 'OFFERED'] } },
    select: {
      id: true,
      status: true,
      joinedAt: true,
      showId: true,
      categoryId: true,
      offerToken: true,
      offerExpiresAt: true,
      category: { select: { name: true, price: true } },
      show: {
        select: { startsAt: true, event: { select: { id: true, title: true } } },
      },
    },
    orderBy: { joinedAt: 'desc' },
  });

  return Promise.all(
    rows.map(async (r) => ({
      id: r.id,
      status: r.status,
      joinedAt: r.joinedAt.toISOString(),
      showId: r.showId,
      eventId: r.show.event.id,
      eventTitle: r.show.event.title,
      startsAt: r.show.startsAt.toISOString(),
      category: r.category.name,
      price: r.category.price.toString(),
      // Only ever this customer's own token — it is on their own entry.
      offerToken: r.status === 'OFFERED' ? r.offerToken : null,
      offerExpiresAt: r.offerExpiresAt?.toISOString() ?? null,
      position:
        r.status === 'WAITING' ? await positionOf(r.showId, r.categoryId, r.joinedAt) : null,
    })),
  );
}

export async function leave(id: string, caller: Caller) {
  const entry = await prisma.waitlistEntry.findUnique({
    where: { id },
    select: { id: true, customerId: true, status: true, offeredSeatId: true },
  });
  if (!entry)
    throw ApiError.notFound('WAITLIST_ENTRY_NOT_FOUND', 'No waitlist entry with that id.');
  if (entry.customerId !== caller.sub && caller.role !== 'ADMIN') {
    throw ApiError.forbidden('That waitlist entry belongs to someone else.');
  }

  // Leaving while holding an offer must hand the seat on rather than stranding
  // it in OFFERED until the sweeper notices.
  if (entry.status === 'OFFERED' && entry.offeredSeatId) {
    const seatId = entry.offeredSeatId;
    const pending = await prisma.$transaction(
      async (tx) => {
        await tx.waitlistEntry.update({
          where: { id },
          data: {
            status: 'CANCELLED',
            offerToken: null,
            offerExpiresAt: null,
            offeredSeatId: null,
          },
        });
        return advanceWaitlist(tx, seatId);
      },
      { maxWait: 15_000, timeout: 20_000 },
    );
    return { left: true, passedOn: pending !== null, pending };
  }

  await prisma.waitlistEntry.update({ where: { id }, data: { status: 'CANCELLED' } });
  return { left: true, passedOn: false, pending: null };
}

/* ------------------------------------------------------------------ offers */

export async function getOffer(token: string) {
  const entry = await prisma.waitlistEntry.findUnique({
    where: { offerToken: token },
    select: {
      id: true,
      status: true,
      offerExpiresAt: true,
      category: { select: { name: true, price: true } },
      show: {
        select: {
          id: true,
          startsAt: true,
          event: { select: { id: true, title: true, venue: { select: { name: true } } } },
        },
      },
    },
  });
  if (!entry) throw ApiError.notFound('OFFER_NOT_FOUND', 'This offer link is not recognised.');

  const expired = !entry.offerExpiresAt || entry.offerExpiresAt.getTime() <= Date.now();
  if (entry.status !== 'OFFERED' || expired) {
    // 410, not 404: the link was real, it has simply run out. The customer
    // deserves to know the difference.
    throw ApiError.gone(
      'OFFER_EXPIRED',
      'This offer has expired and the seat went to someone else.',
    );
  }

  return {
    showId: entry.show.id,
    eventId: entry.show.event.id,
    eventTitle: entry.show.event.title,
    venue: entry.show.event.venue.name,
    startsAt: entry.show.startsAt.toISOString(),
    category: entry.category.name,
    price: entry.category.price.toString(),
    expiresAt: entry.offerExpiresAt!.toISOString(),
  };
}

/**
 * Accept an offer and turn it into a booking.
 *
 * Five checks, all of them load-bearing:
 *   1. the token resolves to an entry
 *   2. that entry is still OFFERED
 *   3. the offer has not expired
 *   4. the seat is still OFFERED — not swept, not taken
 *   5. the caller is the customer the offer was made to
 *
 * Five matters because the token is a bearer credential that arrives by email:
 * without (5) anyone who sees the link can take the seat, and without (4) a
 * race with the sweeper could sell a seat already offered onward.
 */
export async function acceptOffer(token: string, caller: Caller) {
  const booking = await prisma.$transaction(
    async (tx) => {
      const rows = await tx.$queryRaw<
        {
          id: string;
          customerId: string;
          status: string;
          offerExpiresAt: Date | null;
          offeredSeatId: string | null;
          showId: string;
        }[]
      >`
        SELECT id, "customerId", status::text AS status, "offerExpiresAt", "offeredSeatId", "showId"
        FROM "WaitlistEntry"
        WHERE "offerToken" = ${token}
        FOR UPDATE`;

      const entry = rows[0];
      if (!entry) throw ApiError.notFound('OFFER_NOT_FOUND', 'This offer link is not recognised.');

      if (entry.customerId !== caller.sub) {
        // Deliberately the same message as an expired offer. Telling a stranger
        // "this is somebody else's valid offer" confirms the link is live.
        throw ApiError.forbidden('This offer is not yours.');
      }
      if (entry.status !== 'OFFERED' || !entry.offeredSeatId) {
        throw ApiError.gone('OFFER_EXPIRED', 'This offer is no longer open.');
      }
      if (!entry.offerExpiresAt || entry.offerExpiresAt.getTime() <= Date.now()) {
        throw ApiError.gone('OFFER_EXPIRED', 'This offer has expired.');
      }

      const seatRows = await tx.$queryRaw<{ id: string; status: string; categoryId: string }[]>`
        SELECT id, status::text AS status, "categoryId"
        FROM "ShowSeat"
        WHERE id = ${entry.offeredSeatId}
        FOR UPDATE`;

      const seat = seatRows[0];
      if (!seat || seat.status !== 'OFFERED') {
        throw ApiError.gone('OFFER_EXPIRED', 'That seat is no longer available.');
      }

      const created = await writeBooking(tx, {
        showId: entry.showId,
        customerId: caller.sub,
        seats: [{ id: seat.id, categoryId: seat.categoryId }],
      });

      await tx.waitlistEntry.update({
        where: { id: entry.id },
        // Token cleared: single use. A link that still works after the seat is
        // booked is a link somebody will try again.
        data: { status: 'CONVERTED', offerToken: null, offerExpiresAt: null },
      });

      return { booking: created, showId: entry.showId, seatId: seat.id };
    },
    { maxWait: 15_000, timeout: 20_000 },
  );

  broadcastStatus(booking.showId, [booking.seatId], 'BOOKED');
  return toBookingView(booking.booking);
}

/* ----------------------------------------------------------------- sweeping */

/**
 * Expire offers whose clock has run out and pass each seat down the queue.
 *
 * Note what this does NOT do: set the seat to AVAILABLE. An expired offer means
 * "this person did not take it", not "nobody wants it" — advanceWaitlist()
 * decides, and only returns the seat to general sale when the queue is
 * genuinely empty. That is the loop that makes an ignored offer walk the line
 * on its own.
 */
export async function sweepExpiredOffers(): Promise<{ expired: number; offers: PendingOffer[] }> {
  const due = await prisma.waitlistEntry.findMany({
    where: { status: 'OFFERED', offerExpiresAt: { lte: new Date() } },
    select: { id: true, offeredSeatId: true },
    // Bounded so one slow tick cannot try to process thousands at once.
    take: 50,
  });

  const offers: PendingOffer[] = [];
  const touched: { showId: string; showSeatId: string; status: 'OFFERED' | 'AVAILABLE' }[] = [];

  for (const entry of due) {
    // One transaction per seat. A single transaction over all of them would
    // hold every lock until the slowest finished, and one failure would roll
    // back expiries that had nothing to do with it.
    const pending = await prisma.$transaction(
      async (tx) => {
        const still = await tx.waitlistEntry.findUnique({
          where: { id: entry.id },
          select: { status: true },
        });
        // Another sweeper, or the customer accepting at the last second, may
        // have moved it since the list above was read.
        if (still?.status !== 'OFFERED') return null;

        await tx.waitlistEntry.update({
          where: { id: entry.id },
          data: { status: 'EXPIRED', offerToken: null, offerExpiresAt: null },
        });

        return entry.offeredSeatId ? advanceWaitlist(tx, entry.offeredSeatId) : null;
      },
      { maxWait: 15_000, timeout: 20_000 },
    );

    if (pending) offers.push(pending);

    if (entry.offeredSeatId) {
      const seat = await prisma.showSeat.findUnique({
        where: { id: entry.offeredSeatId },
        select: { showId: true, status: true },
      });
      if (seat && (seat.status === 'OFFERED' || seat.status === 'AVAILABLE')) {
        touched.push({
          showId: seat.showId,
          showSeatId: entry.offeredSeatId,
          status: seat.status,
        });
      }
    }
  }

  // Offers that moved on: the new holder's seat stays OFFERED, and a seat with
  // nobody left behind it goes back on sale. Both need announcing, because to
  // every other viewer these changed without anyone clicking anything.
  for (const seat of touched) {
    broadcastSeats(seat.showId, [{ id: seat.showSeatId, status: seat.status }]);
  }

  return { expired: due.length, offers };
}
