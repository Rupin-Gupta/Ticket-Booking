import type { Server as HttpServer } from 'node:http';
import { Server } from 'socket.io';
import { createAdapter } from '@socket.io/redis-adapter';
import { SOCKET_EVENTS, showRoom } from '@ticket/shared';
import { allowedOrigins, env } from '../env.js';
import { getRedis } from '../lib/redis.js';
import { getSeatMap } from '../modules/seats/service.js';
import { setEmitter } from './emit.js';

/** One socket has no business watching hundreds of shows at once. */
const MAX_ROOMS_PER_SOCKET = 10;

/**
 * Realtime seat updates.
 *
 * **No authentication, on purpose.** Everything this layer emits is already
 * available from `GET /shows/:id/seats` without a token, and the broadcast
 * shape carries no `heldByMe` and no `holdExpiresAt` — those are per-viewer
 * answers a shared payload cannot give. Adding a handshake we never read from
 * would be security theatre; the protection that matters is that there is
 * nothing private in the payload to begin with.
 */
export function startRealtime(httpServer: HttpServer) {
  const io = new Server(httpServer, {
    cors: { origin: allowedOrigins, credentials: true },
    // Trim the default 45s: a customer holding seats on a dead connection
    // should be noticed before their hold expires, not after.
    pingTimeout: 20_000,
  });

  /*
   * Without this adapter each process broadcasts only to its own connected
   * sockets, so with two instances roughly half the viewers silently miss every
   * update — and it never reproduces locally on one process (rule 15).
   *
   * Two connections because a Redis client in subscriber mode cannot issue
   * ordinary commands.
   */
  const redis = getRedis();
  const adapterConnections: { quit: () => Promise<unknown> }[] = [];
  if (redis) {
    // Two connections because a client in subscriber mode cannot issue ordinary
    // commands. Both are tracked so shutdown can close them — an open
    // subscriber keeps the event loop alive and the process never exits.
    const pub = redis.duplicate();
    const sub = redis.duplicate();
    adapterConnections.push(pub, sub);
    io.adapter(createAdapter(pub, sub));
    console.log('realtime: redis adapter wired (multi-instance safe)');
  } else {
    console.warn('realtime: no REDIS_URL — broadcasts will not cross process boundaries');
  }

  io.on('connection', (socket) => {
    socket.on(SOCKET_EVENTS.showJoin, async (payload: { showId?: string }) => {
      const showId = payload?.showId;
      if (typeof showId !== 'string' || showId.length === 0) return;
      if (socket.rooms.size > MAX_ROOMS_PER_SOCKET) return;

      await socket.join(showRoom(showId));

      // A full snapshot on join, so a late arrival is never rendering a map
      // assembled from updates it missed. The viewer is anonymous here, hence
      // `null` — the browser already has its own map from the REST call and
      // reconciles ownership locally.
      try {
        const seats = await getSeatMap(showId, null);
        socket.emit(SOCKET_EVENTS.seatSync, { showId, seats });
      } catch {
        // An unknown show id is a client bug, not something to crash on.
      }
    });

    socket.on(SOCKET_EVENTS.showLeave, (payload: { showId?: string }) => {
      if (typeof payload?.showId === 'string') void socket.leave(showRoom(payload.showId));
    });
  });

  setEmitter(io);
  console.log(`realtime listening (rooms keyed show:{id})`);

  return async () => {
    setEmitter(null);
    await io.close();
    await Promise.allSettled(adapterConnections.map((c) => c.quit()));
  };
}

export const realtimeEnabled = () => env.NODE_ENV !== 'test';
