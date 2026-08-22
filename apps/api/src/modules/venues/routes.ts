import { Router } from 'express';
import type { RequestHandler } from 'express';
import { requireAuth, requireRole } from '../../middleware/auth.js';
import { addSeatBlockSchema, createVenueSchema, updateVenueSchema } from './schema.js';
import * as service from './service.js';

export const venueRoutes = Router();

const adminOnly: RequestHandler[] = [requireAuth, requireRole(['ADMIN'])];

// Reading a venue is public — the seat layout is on the ticket page anyway.
venueRoutes.get('/', async (_req, res) => {
  res.json({ venues: await service.listVenues() });
});

venueRoutes.get('/:id', async (req, res) => {
  res.json({ venue: await service.getVenue(req.params.id) });
});

venueRoutes.get('/:id/sections', async (req, res) => {
  res.json({ sections: await service.listSections(req.params.id) });
});

venueRoutes.post('/', adminOnly, async (req, res) => {
  res.status(201).json({ venue: await service.createVenue(createVenueSchema.parse(req.body)) });
});

venueRoutes.patch('/:id', adminOnly, async (req, res) => {
  res.json({ venue: await service.updateVenue(req.params.id, updateVenueSchema.parse(req.body)) });
});

venueRoutes.post('/:id/seats', adminOnly, async (req, res) => {
  const input = addSeatBlockSchema.parse(req.body);
  res.status(201).json(await service.addSeatBlock(req.params.id, input));
});
