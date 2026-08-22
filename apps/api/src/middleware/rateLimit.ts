import rateLimit, { type Options } from 'express-rate-limit';
import { env } from '../env.js';

/**
 * Rate limits exist for the attacks a correctness guard cannot see. A row lock
 * stops two people racing for one seat; it does nothing about one script
 * trying ten thousand passwords, or holding every seat in the venue on purpose.
 *
 * Disabled under NODE_ENV=test — the concurrency suite deliberately fires 20
 * simultaneous requests at one endpoint, which is exactly what this blocks.
 */
const limiter = (windowMs: number, limit: number, code: string, message: string) =>
  rateLimit({
    windowMs,
    limit,
    standardHeaders: 'draft-7',
    legacyHeaders: false,
    skip: () => env.NODE_ENV === 'test',
    handler: (_req, res) => res.status(429).json({ error: { code, message } }),
  } satisfies Partial<Options>);

/** Password guessing is the whole threat here, so this one is tight. */
export const loginLimiter = limiter(
  15 * 60_000,
  10,
  'TOO_MANY_LOGIN_ATTEMPTS',
  'Too many login attempts. Try again in a few minutes.',
);

/** Stops bulk account creation without getting in a real person's way. */
export const registerLimiter = limiter(
  60 * 60_000,
  5,
  'TOO_MANY_REGISTRATIONS',
  'Too many accounts created from this address. Try again later.',
);

/**
 * Holds are the contended endpoint — this is the one a script would hammer to
 * lock a venue. Generous enough that a real person picking seats, changing
 * their mind and picking again never sees it.
 */
export const holdLimiter = limiter(
  60_000,
  20,
  'TOO_MANY_HOLD_ATTEMPTS',
  'Too many seat requests. Wait a moment and try again.',
);
