import { z } from 'zod';

/**
 * Note what is absent: there is no `role` field, anywhere.
 *
 * Zod strips unknown keys by default, so a request body carrying
 * `"role": "ADMIN"` parses to an object without it and the service hard-codes
 * CUSTOMER regardless. Accepting a client-supplied role is a one-line
 * privilege-escalation hole, and the way to not have it is to never parse the
 * field in the first place.
 */
export const registerSchema = z.object({
  email: z.string().trim().toLowerCase().email().max(254),
  // Upper bound is a denial-of-service guard: Argon2 on a megabyte of input
  // costs real CPU, and an attacker will happily send a megabyte.
  password: z.string().min(8, 'Password must be at least 8 characters.').max(128),
  name: z.string().trim().min(1).max(80),
});

export const loginSchema = z.object({
  email: z.string().trim().toLowerCase().email().max(254),
  password: z.string().min(1).max(128),
});

export type RegisterInput = z.infer<typeof registerSchema>;
export type LoginInput = z.infer<typeof loginSchema>;
