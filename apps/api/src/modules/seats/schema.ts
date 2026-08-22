import { z } from 'zod';
import { env } from '../../env.js';

export const holdSeatsSchema = z.object({
  seatIds: z
    .array(z.string().min(1))
    .min(1, 'Select at least one seat.')
    // Capped so one request cannot lock the whole venue in a single call.
    .max(env.MAX_SEATS_PER_HOLD, `You can hold at most ${env.MAX_SEATS_PER_HOLD} seats at a time.`)
    // Duplicates would inflate the count past the cap and make the lock set
    // lie about how many rows it is protecting.
    .refine((ids) => new Set(ids).size === ids.length, 'Duplicate seat in request.'),
});

export type HoldSeatsInput = z.infer<typeof holdSeatsSchema>;
