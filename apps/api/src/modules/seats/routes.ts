import { Router, type RequestHandler } from 'express';
import { requireAuth, requireRole } from '../../middleware/auth.js';
import { holdLimiter } from '../../middleware/rateLimit.js';
import { param } from '../../lib/http.js';
import { verifyAccessToken } from '../../lib/jwt.js';
import { holdSeatsSchema } from './schema.js';
import * as service from './service.js';

/** Mounted alongside the existing show routes at /shows. */
export const seatShowRoutes = Router();

/** Mounted at /holds. */
export const holdRoutes = Router();

/**
 * The seat map is public, but a logged-in viewer should see which seats are
 * their own. Read the token if one is present and ignore it if it is not —
 * this must never 401, or the map breaks for anyone browsing signed out.
 */
const optionalAuth: RequestHandler = (req, _res, next) => {
  const header = req.get('authorization');
  if (header?.startsWith('Bearer ')) {
    try {
      req.user = verifyAccessToken(header.slice('Bearer '.length).trim());
    } catch {
      /* an expired token just means "not signed in" here */
    }
  }
  next();
};

seatShowRoutes.get('/:id/seats', optionalAuth, async (req, res) => {
  res.json({ seats: await service.getSeatMap(param(req, 'id'), req.user?.sub ?? null) });
});

seatShowRoutes.post(
  '/:id/holds',
  holdLimiter,
  requireAuth,
  requireRole(['CUSTOMER', 'ORGANISER', 'ADMIN']),
  async (req, res) => {
    const input = holdSeatsSchema.parse(req.body);
    const result = await service.holdSeats(param(req, 'id'), input, req.user!.sub);
    res.status(201).json(result);
  },
);

seatShowRoutes.delete('/:id/holds', requireAuth, async (req, res) => {
  res.json(await service.releaseHolds(param(req, 'id'), req.user!.sub));
});

holdRoutes.get('/me', requireAuth, async (req, res) => {
  res.json({ holds: await service.listMyHolds(req.user!.sub) });
});
