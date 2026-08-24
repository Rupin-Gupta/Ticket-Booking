import { useEffect, useRef, useState } from 'react';
import { io, type Socket } from 'socket.io-client';
import type { SeatUpdate, SeatView } from '@ticket/shared';
import { SOCKET_EVENTS } from '@ticket/shared';

/** Empty in dev — vite proxies the upgrade to :4000. Set on Vercel. */
const BASE = import.meta.env.VITE_API_URL ?? '';

/** How often to re-fetch when the socket is down. Off while it is connected. */
const FALLBACK_POLL_MS = 8000;

type Options = {
  showId: string;
  /** The authoritative map from the REST call, including this viewer's own holds. */
  initial: SeatView[];
  /** Used when the socket is unavailable, and once on reconnect to resync. */
  refetch: () => void;
};

/**
 * Keeps the seat map live.
 *
 * Broadcasts carry only `{ id, status }` — a shared payload cannot answer "is
 * this MINE", which differs per viewer. So ownership is reconciled here: the
 * REST response is the source of truth for `heldByMe`, and an incoming update
 * preserves it while the seat is still HELD and drops it the moment the seat
 * moves to anything else.
 *
 * The socket is an optimisation, never the source of truth. If it never
 * connects, the poll keeps the map correct — just less immediately.
 */
export function useLiveSeats({ showId, initial, refetch }: Options) {
  const [seats, setSeats] = useState<SeatView[]>(initial);
  const [live, setLive] = useState(false);
  const [viewers, setViewers] = useState(0);

  // Held in a ref so the socket effect does not tear down and reconnect every
  // time the seat list changes.
  const refetchRef = useRef(refetch);
  refetchRef.current = refetch;

  // A fresh REST response always wins: it is the only thing that knows which
  // seats are this viewer's own.
  useEffect(() => setSeats(initial), [initial]);

  useEffect(() => {
    if (!showId) return;

    const socket: Socket = io(BASE || window.location.origin, {
      path: '/socket.io',
      transports: ['websocket', 'polling'],
    });

    const apply = (updates: SeatUpdate[]) => {
      setSeats((current) => {
        const byId = new Map(updates.map((u) => [u.id, u.status]));
        let changed = false;
        const next = current.map((seat) => {
          const status = byId.get(seat.id);
          if (!status || status === seat.status) return seat;
          changed = true;
          // Ownership survives only while the seat is still held. Once it is
          // booked, released or offered onward, it is no longer ours and the
          // countdown must go with it.
          const stillMine = seat.heldByMe && status === 'HELD';
          return {
            ...seat,
            status,
            heldByMe: stillMine,
            holdExpiresAt: stillMine ? seat.holdExpiresAt : null,
          };
        });
        // Returning the same array when nothing moved keeps React from
        // re-rendering a hundred seat buttons on every stray broadcast.
        return changed ? next : current;
      });
    };

    socket.on('connect', () => {
      setLive(true);
      socket.emit(SOCKET_EVENTS.showJoin, { showId });
    });

    socket.on('disconnect', () => setLive(false));

    // Reconnecting means updates were missed while offline; a snapshot alone
    // would not restore this viewer's own holds, so refetch instead.
    socket.io.on('reconnect', () => refetchRef.current());

    socket.on(SOCKET_EVENTS.seatUpdate, (payload: { showId: string; seats: SeatUpdate[] }) => {
      if (payload.showId === showId) apply(payload.seats);
    });

    socket.on(SOCKET_EVENTS.seatSync, (payload: { showId: string; seats: SeatView[] }) => {
      // The snapshot is anonymous, so take only status from it.
      if (payload.showId === showId) {
        apply(payload.seats.map((s) => ({ id: s.id, status: s.status })));
      }
    });

    socket.on(SOCKET_EVENTS.viewers, (payload: { showId: string; viewers: number }) => {
      if (payload.showId === showId) setViewers(payload.viewers);
    });

    return () => {
      socket.emit(SOCKET_EVENTS.showLeave, { showId });
      socket.close();
    };
  }, [showId]);

  // Polling exists for the case where the socket cannot connect at all —
  // a proxy that blocks upgrades, a corporate network, a dead adapter.
  useEffect(() => {
    if (live) return;
    const timer = setInterval(() => refetchRef.current(), FALLBACK_POLL_MS);
    return () => clearInterval(timer);
  }, [live]);

  // Zero while disconnected: an unreachable socket knows nothing about who else
  // is here, and a stale "12 watching" is worse than no number at all.
  return { seats, live, viewers: live ? viewers : 0 };
}
