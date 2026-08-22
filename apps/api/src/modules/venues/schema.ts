import { z } from 'zod';

export const createVenueSchema = z.object({
  name: z.string().trim().min(1).max(120),
  address: z.string().trim().min(1).max(240),
});

export const updateVenueSchema = createVenueSchema.partial();

/**
 * Bulk seat creation: a rectangular block of seats in one named section.
 *
 * Rows are labelled A, B, C… so 26 is the ceiling — past that the labels would
 * need a second letter and nothing in this project needs a 27-row section.
 * ponytail: if a venue ever does, switch to AA/AB here and nowhere else.
 */
export const addSeatBlockSchema = z.object({
  section: z.string().trim().min(1).max(40),
  rows: z.number().int().min(1).max(26),
  seatsPerRow: z.number().int().min(1).max(60),
});

export type CreateVenueInput = z.infer<typeof createVenueSchema>;
export type UpdateVenueInput = z.infer<typeof updateVenueSchema>;
export type AddSeatBlockInput = z.infer<typeof addSeatBlockSchema>;
