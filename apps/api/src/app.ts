import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import { allowedOrigins, configured, env } from './env.js';
import { requestLogger } from './middleware/logger.js';
import { errorHandler, notFound } from './middleware/error.js';

/**
 * Round-trips one query to Postgres. Two jobs:
 *   1. tells a fresh clone whether its connection string actually works, rather
 *      than only whether it is present
 *   2. gives the daily keep-alive something to hit — Supabase pauses a free
 *      project after 7 days with no database activity, and unpausing is manual.
 *      A dashboard visit does not count; a query does.
 *
 * Reports rather than throws: an unreachable database is information, not a
 * reason for the health endpoint itself to fail.
 */
async function databaseStatus(): Promise<'up' | 'unreachable' | 'not-configured'> {
  if (!configured.database) return 'not-configured';
  try {
    // Imported here, not at module load: constructing PrismaClient reads
    // DATABASE_URL, and the API must still boot without one.
    const { prisma } = await import('./lib/prisma.js');
    await prisma.$queryRaw`SELECT 1`;
    return 'up';
  } catch (err) {
    console.error('[health] database unreachable', err);
    return 'unreachable';
  }
}

export function createApp() {
  const app = express();

  app.set('trust proxy', 1); // Render sits behind a proxy; needed for rate limiting by IP later
  app.use(helmet());
  app.use(cors({ origin: allowedOrigins, credentials: true }));
  app.use(express.json({ limit: '100kb' }));
  app.use(requestLogger);

  // Liveness plus a wiring checklist, so a fresh clone can see what is still
  // unconfigured without reading the code.
  app.get('/health', async (_req, res) => {
    res.json({
      ok: true,
      env: env.NODE_ENV,
      uptimeSeconds: Math.round(process.uptime()),
      configured,
      database: await databaseStatus(),
    });
  });

  const api = express.Router();
  // Phase 1+ mounts modules here: auth, venues, events, shows, holds,
  // bookings, waitlist, organiser.
  app.use('/api/v1', api);

  app.use(notFound);
  app.use(errorHandler);

  return app;
}
