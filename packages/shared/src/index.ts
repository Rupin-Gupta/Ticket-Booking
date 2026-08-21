/**
 * Types and constants shared by apps/api and apps/web.
 *
 * These mirror the Prisma enums in apps/api/prisma/schema.prisma. Prisma's
 * generated enums stay authoritative on the server; this file exists so the
 * browser (which never sees @prisma/client) can talk about the same values.
 *
 * ponytail: mirrored by hand rather than code-generated. apps/api asserts at
 * compile time that the two sides still match — see src/lib/enum-parity.ts.
 * Generate them if the enum count ever gets past a handful.
 */

export const ROLE = ['CUSTOMER', 'ORGANISER', 'ADMIN'] as const;
export type Role = (typeof ROLE)[number];

export const EVENT_TYPE = ['MOVIE', 'CONCERT'] as const;
export type EventType = (typeof EVENT_TYPE)[number];

/** AVAILABLE → HELD → BOOKED, plus OFFERED for a seat reserved to one waitlisted customer. */
export const SEAT_STATUS = ['AVAILABLE', 'HELD', 'OFFERED', 'BOOKED'] as const;
export type SeatStatus = (typeof SEAT_STATUS)[number];

export const BOOKING_STATUS = ['CONFIRMED', 'CANCELLED'] as const;
export type BookingStatus = (typeof BOOKING_STATUS)[number];

export const WAITLIST_STATUS = ['WAITING', 'OFFERED', 'EXPIRED', 'CONVERTED', 'CANCELLED'] as const;
export type WaitlistStatus = (typeof WAITLIST_STATUS)[number];

/**
 * One seat as the browser is allowed to see it.
 *
 * Deliberately has no `heldByUserId`: the public seat map shows *that* a seat
 * is held, never *who* holds it. `heldByMe` is the only ownership signal that
 * ever crosses the wire, and the server computes it per requester.
 */
export type SeatView = {
  id: string;
  section: string;
  row: string;
  number: number;
  posX: number;
  posY: number;
  categoryId: string;
  categoryName: string;
  price: string;
  status: SeatStatus;
  heldByMe: boolean;
  /** Present only when this requester is the one holding it. */
  holdExpiresAt: string | null;
};

/** Every API failure has this shape, from every route. */
export type ApiErrorBody = {
  error: {
    code: string;
    message: string;
    details?: unknown;
  };
};

/** Socket.IO event names. Rooms are `show:{showId}`. */
export const SOCKET_EVENTS = {
  /** client → server */
  showJoin: 'show:join',
  showLeave: 'show:leave',
  /** server → client: full snapshot, sent on join */
  seatSync: 'seat:sync',
  /** server → client: one seat, after every committed mutation */
  seatUpdate: 'seat:update',
} as const;

export const showRoom = (showId: string) => `show:${showId}`;
