import type { SeatStatus, SeatUpdate } from '@ticket/shared';
import { SOCKET_EVENTS, showRoom } from '@ticket/shared';

/**
 * The publish side of realtime, kept in its own module with no dependencies.
 *
 * Services import this; `realtime/index.ts` imports the services to build the
 * snapshot. Splitting them is what keeps the graph acyclic — and it means a
 * service can announce a change without knowing Socket.IO exists.
 */
type Emitter = { to: (room: string) => { emit: (event: string, payload: unknown) => void } };

let io: Emitter | null = null;

export function setEmitter(next: Emitter | null) {
  io = next;
}

/**
 * Announce seat changes to everyone watching a show.
 *
 * **Call this after the transaction commits, never inside it.** Emitting from
 * within means a rolled-back transaction has already told every browser the
 * seat is gone, and nothing ever corrects them.
 *
 * Silently does nothing when realtime is not running — under test, or before
 * the server has started. A missed broadcast is a stale screen for a few
 * seconds, not a wrong answer: the poll fallback and the next read both
 * recompute effective status from the database.
 */
export function broadcastSeats(showId: string, seats: SeatUpdate[]) {
  if (!io || seats.length === 0) return;
  io.to(showRoom(showId)).emit(SOCKET_EVENTS.seatUpdate, { showId, seats });
}

/** Convenience for the common case: several seats moving to one status. */
export const broadcastStatus = (showId: string, seatIds: string[], status: SeatStatus) =>
  broadcastSeats(
    showId,
    seatIds.map((id) => ({ id, status })),
  );
