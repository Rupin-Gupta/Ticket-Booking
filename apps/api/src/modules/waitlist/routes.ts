import { Router } from 'express';
import { z } from 'zod';
import { requireAuth } from '../../middleware/auth.js';
import { param } from '../../lib/http.js';
import { enqueueEmail } from '../../jobs/email.queue.js';
import * as service from './service.js';

/** Mounted alongside the other show routes at /shows. */
export const waitlistShowRoutes = Router();

/** Mounted at /waitlist. */
export const waitlistRoutes = Router();

const joinSchema = z.object({ categoryId: z.string().min(1) });

waitlistShowRoutes.post('/:id/waitlist', requireAuth, async (req, res) => {
  const { categoryId } = joinSchema.parse(req.body);
  res.status(201).json(await service.join(param(req, 'id'), categoryId, req.user!));
});

waitlistRoutes.get('/me', requireAuth, async (req, res) => {
  res.json({ entries: await service.listMine(req.user!) });
});

waitlistRoutes.delete('/:id', requireAuth, async (req, res) => {
  const result = await service.leave(param(req, 'id'), req.user!);
  // Giving up an offer hands the seat straight on; the next person is told
  // after the transaction that created their offer has committed.
  if (result.pending)
    void enqueueEmail({ kind: 'waitlist-offer', entryId: result.pending.entryId });
  res.json({ left: result.left, passedOn: result.passedOn });
});

// Public: the customer follows this link from an email, possibly on a phone
// that is not signed in yet. Reading the offer is safe; accepting is not.
waitlistRoutes.get('/offers/:token', async (req, res) => {
  res.json({ offer: await service.getOffer(param(req, 'token')) });
});

waitlistRoutes.post('/offers/:token/accept', requireAuth, async (req, res) => {
  const booking = await service.acceptOffer(param(req, 'token'), req.user!);
  res.status(201).json({ booking });
});
