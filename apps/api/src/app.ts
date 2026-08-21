import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import { allowedOrigins, configured, env } from './env.js';
import { requestLogger } from './middleware/logger.js';
import { errorHandler, notFound } from './middleware/error.js';

export function createApp() {
  const app = express();

  app.set('trust proxy', 1); // Render sits behind a proxy; needed for rate limiting by IP later
  app.use(helmet());
  app.use(cors({ origin: allowedOrigins, credentials: true }));
  app.use(express.json({ limit: '100kb' }));
  app.use(requestLogger);

  // Liveness plus a wiring checklist, so a fresh clone can see what is still
  // unconfigured without reading the code.
  app.get('/health', (_req, res) => {
    res.json({
      ok: true,
      env: env.NODE_ENV,
      uptimeSeconds: Math.round(process.uptime()),
      configured,
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
