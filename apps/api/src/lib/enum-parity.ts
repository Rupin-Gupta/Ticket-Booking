import type { $Enums } from '@prisma/client';
import type { BookingStatus, EventType, Role, SeatStatus, WaitlistStatus } from '@ticket/shared';

/**
 * packages/shared mirrors the Prisma enums by hand so the browser can talk
 * about the same values without importing @prisma/client. This file is the
 * guard: `npm run typecheck` fails if either side drifts. It is never executed.
 */
type Exact<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;

const _role: Exact<Role, $Enums.Role> = true;
const _eventType: Exact<EventType, $Enums.EventType> = true;
const _seatStatus: Exact<SeatStatus, $Enums.SeatStatus> = true;
const _bookingStatus: Exact<BookingStatus, $Enums.BookingStatus> = true;
const _waitlistStatus: Exact<WaitlistStatus, $Enums.WaitlistStatus> = true;

export const enumParityChecked = [
  _role,
  _eventType,
  _seatStatus,
  _bookingStatus,
  _waitlistStatus,
] as const;
