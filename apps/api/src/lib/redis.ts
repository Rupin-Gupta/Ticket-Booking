import IORedis from 'ioredis';
import { env } from '../env.js';

/**
 * One shared connection for BullMQ.
 *
 * `maxRetriesPerRequest: null` is not optional — BullMQ blocks on commands
 * that can wait a full minute, and ioredis's default retry ceiling aborts
 * them. BullMQ throws at startup if this is set to anything else.
 *
 * Upstash is TLS-only, which the `rediss://` scheme already implies; a
 * `redis://` URL against it does not error, it simply hangs.
 */
let client: IORedis | null = null;

export function getRedis(): IORedis | null {
  if (!env.REDIS_URL) return null;
  if (client) return client;

  client = new IORedis(env.REDIS_URL, {
    maxRetriesPerRequest: null,
    enableReadyCheck: false,
    // Upstash's free tier meters commands, so a reconnect storm is expensive
    // as well as noisy.
    retryStrategy: (attempt) => Math.min(attempt * 500, 5_000),
  });

  client.on('error', (err) => console.error('[redis]', err.message));
  return client;
}

export async function closeRedis() {
  await client?.quit().catch(() => {});
  client = null;
}
