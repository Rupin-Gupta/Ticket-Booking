import { z } from 'zod';

export const createEventSchema = z.object({
  venueId: z.string().uuid(),
  title: z.string().trim().min(1).max(160),
  type: z.enum(['MOVIE', 'CONCERT']),
  description: z.string().trim().max(2000).optional(),
});

// No venueId: moving an event to a different venue would orphan every
// ShowSeat already generated against the old venue's seats.
export const updateEventSchema = createEventSchema.omit({ venueId: true }).partial();

export const createCategorySchema = z.object({
  name: z.string().trim().min(1).max(40),
  // Money as a string, parsed to Decimal by Prisma. A float cannot hold 0.10.
  price: z
    .union([z.string(), z.number()])
    .refine((v) => !Number.isNaN(Number(v)) && Number(v) >= 0, 'Price must be a number ≥ 0')
    .transform((v) => String(v)),
  sections: z.array(z.string().trim().min(1)).min(1, 'Claim at least one section.'),
});

export const createShowSchema = z.object({
  startsAt: z.coerce
    .date()
    .refine((d) => d.getTime() > Date.now(), 'Show must start in the future.'),
});

export const listEventsQuerySchema = z.object({
  type: z.enum(['MOVIE', 'CONCERT']).optional(),
  venueId: z.string().uuid().optional(),
  q: z.string().trim().max(120).optional(),
  from: z.coerce.date().optional(),
  to: z.coerce.date().optional(),
  // Capped so one request cannot ask for the whole table.
  limit: z.coerce.number().int().min(1).max(50).default(20),
  offset: z.coerce.number().int().min(0).default(0),
});

export type CreateEventInput = z.infer<typeof createEventSchema>;
export type UpdateEventInput = z.infer<typeof updateEventSchema>;
export type CreateCategoryInput = z.infer<typeof createCategorySchema>;
export type CreateShowInput = z.infer<typeof createShowSchema>;
export type ListEventsQuery = z.infer<typeof listEventsQuerySchema>;
