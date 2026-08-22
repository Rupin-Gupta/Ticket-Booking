import { Router } from 'express';
import { requireAuth, requireRole } from '../../middleware/auth.js';
import { addSeatBlockSchema, createVenueSchema, updateVenueSchema } from './schema.js';
import { param } from '../../lib/http.js';
import * as service from './service.js';

export const venueRoutes = Router();

const adminOnly = [requireAuth, requireRole(['ADMIN'])] as const;

// Reading a venue is public — the seat layout is on the ticket page anyway.
venueRoutes.get('/', async (_req, res) => {
  res.json({ venues: await service.listVenues() });
});

venueRoutes.get('/:id', async (req, res) => {
  res.json({ venue: await service.getVenue(param(req, 'id')) });
});

venueRoutes.get('/:id/sections', async (req, res) => {
  res.json({ sections: await service.listSections(param(req, 'id')) });
});

venueRoutes.post('/', ...adminOnly, async (req, res) => {
  res.status(201).json({ venue: await service.createVenue(createVenueSchema.parse(req.body)) });
});

venueRoutes.patch('/:id', ...adminOnly, async (req, res) => {
  res.json({
    venue: await service.updateVenue(param(req, 'id'), updateVenueSchema.parse(req.body)),
  });
});

venueRoutes.post('/:id/seats', ...adminOnly, async (req, res) => {
  const input = addSeatBlockSchema.parse(req.body);
  res.status(201).json(await service.addSeatBlock(param(req, 'id'), input));
});
