import { Router } from 'express';
import { requireAuth, requireRole } from '../../middleware/auth.js';
import {
  createCategorySchema,
  createEventSchema,
  createShowSchema,
  listEventsQuerySchema,
  updateEventSchema,
} from './schema.js';
import { param } from '../../lib/http.js';
import * as service from './service.js';

export const eventRoutes = Router();
export const showRoutes = Router();

const organiserOnly = [requireAuth, requireRole(['ORGANISER', 'ADMIN'])] as const;

/* Public browsing. No auth — an event listing nobody can see sells nothing. */

eventRoutes.get('/', async (req, res) => {
  res.json(await service.listEvents(listEventsQuerySchema.parse(req.query)));
});

/* Must be declared before '/:id', or Express matches "mine" as an id. */
eventRoutes.get('/mine', ...organiserOnly, async (req, res) => {
  res.json({ events: await service.listOwnEvents(req.user!) });
});

eventRoutes.get('/:id', async (req, res) => {
  res.json({ event: await service.getEvent(param(req, 'id')) });
});

/* Organiser-owned writes. Every one is ownership-checked in the service. */

eventRoutes.post('/', ...organiserOnly, async (req, res) => {
  const input = createEventSchema.parse(req.body);
  res.status(201).json({ event: await service.createEvent(input, req.user!) });
});

eventRoutes.patch('/:id', ...organiserOnly, async (req, res) => {
  const input = updateEventSchema.parse(req.body);
  res.json({ event: await service.updateEvent(param(req, 'id'), input, req.user!) });
});

eventRoutes.post('/:id/categories', ...organiserOnly, async (req, res) => {
  const input = createCategorySchema.parse(req.body);
  res
    .status(201)
    .json({ category: await service.createCategory(param(req, 'id'), input, req.user!) });
});

eventRoutes.post('/:id/shows', ...organiserOnly, async (req, res) => {
  const input = createShowSchema.parse(req.body);
  res.status(201).json({ show: await service.createShow(param(req, 'id'), input, req.user!) });
});

/* Shows are addressed on their own path — the seat map hangs off this in Phase 3. */

showRoutes.get('/:id', async (req, res) => {
  res.json({ show: await service.getShow(param(req, 'id')) });
});
