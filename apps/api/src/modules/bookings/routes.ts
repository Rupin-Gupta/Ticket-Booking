import { Router } from 'express';
import { z } from 'zod';
import { requireAuth } from '../../middleware/auth.js';
import { param } from '../../lib/http.js';
import { env } from '../../env.js';
import * as service from './service.js';

export const bookingRoutes = Router();
/** Mounted at the API root: the QR encodes {WEB_URL}/verify/{token}. */
export const verifyRoutes = Router();

const createSchema = z.object({
  showId: z.string().min(1),
  seatIds: z
    .array(z.string().min(1))
    .min(1)
    .max(env.MAX_SEATS_PER_HOLD)
    .refine((ids) => new Set(ids).size === ids.length, 'Duplicate seat in request.'),
});

bookingRoutes.post('/', requireAuth, async (req, res) => {
  const input = createSchema.parse(req.body);
  const booking = await service.createBooking(input.showId, input.seatIds, req.user!);
  res.status(201).json({ booking });
});

bookingRoutes.get('/', requireAuth, async (req, res) => {
  res.json({ bookings: await service.listMyBookings(req.user!) });
});

bookingRoutes.get('/:id', requireAuth, async (req, res) => {
  res.json({ booking: await service.getBooking(param(req, 'id'), req.user!) });
});

bookingRoutes.post('/:id/cancel', requireAuth, async (req, res) => {
  res.json(await service.cancelBooking(param(req, 'id'), req.user!));
});

// Public: the person scanning at the door is not logged in.
verifyRoutes.get('/:qrToken', async (req, res) => {
  res.json({ ticket: await service.verifyTicket(param(req, 'qrToken')) });
});
