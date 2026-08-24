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
  /**
   * How often this seat is picked up and put back down, relative to its own
   * row. Null unless the organiser published signals for this event AND the
   * seat has enough outcomes to support a number.
   */
  hesitation: { ratio: number; rowMultiple: number; sample: number } | null;
  /** STANDARD | WHEELCHAIR_SPACE | COMPANION | STEP_FREE */
  accessType: SeatAccessType;
  /** The other half of a wheelchair pair. Selecting either selects both. */
  pairedWith: string | null;
};

export type SeatAccessType = 'STANDARD' | 'WHEELCHAIR_SPACE' | 'COMPANION' | 'STEP_FREE';

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
  /** server → client: how many people are watching this show right now */
  viewers: 'show:viewers',
} as const;

export const showRoom = (showId: string) => `show:${showId}`;

/**
 * One seat, as broadcast to every viewer of a show.
 *
 * Deliberately narrower than `SeatView`: a broadcast is one payload sent to
 * many people, and `heldByMe` / `holdExpiresAt` are answers to "is this MINE",
 * which differ per viewer. Putting them in a broadcast would either leak one
 * customer's countdown to everyone or force a separate emit per socket.
 *
 * Clients reconcile locally — each one knows which seats it holds from its own
 * API responses, and re-applies that to incoming updates.
 */
export type SeatUpdate = {
  id: string;
  status: SeatStatus;
};

/** Everything the server broadcasts, keyed by event name. */
export type ServerEvents = {
  [SOCKET_EVENTS.seatSync]: (payload: { showId: string; seats: SeatView[] }) => void;
  [SOCKET_EVENTS.seatUpdate]: (payload: { showId: string; seats: SeatUpdate[] }) => void;
  [SOCKET_EVENTS.viewers]: (payload: { showId: string; viewers: number }) => void;
};

export type ClientEvents = {
  [SOCKET_EVENTS.showJoin]: (payload: { showId: string }) => void;
  [SOCKET_EVENTS.showLeave]: (payload: { showId: string }) => void;
};
