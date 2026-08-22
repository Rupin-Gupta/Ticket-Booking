import { Router } from 'express';
import { requireAuth, requireRole } from '../../middleware/auth.js';
import { param } from '../../lib/http.js';
import * as service from './service.js';

export const organiserRoutes = Router();

organiserRoutes.get(
  '/events/:id/summary',
  requireAuth,
  requireRole(['ORGANISER', 'ADMIN']),
  async (req, res) => {
    // Role gets you through the door; the service checks you own this event.
    res.json(await service.eventSummary(param(req, 'id'), req.user!));
  },
);
