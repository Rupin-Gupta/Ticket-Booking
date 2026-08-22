import { Router } from 'express';
import { ApiError } from '../../lib/errors.js';
import { requireAuth } from '../../middleware/auth.js';
import { loginLimiter, registerLimiter } from '../../middleware/rateLimit.js';
import { loginSchema, registerSchema } from './schema.js';
import * as service from './service.js';

export const authRoutes = Router();

// Express 5 forwards a rejected promise to the error handler on its own, so
// these need no try/catch. A thrown ZodError becomes a 400 there too.

authRoutes.post('/register', registerLimiter, async (req, res) => {
  const input = registerSchema.parse(req.body);
  const result = await service.register(input);
  res.status(201).json(result);
});

authRoutes.post('/login', loginLimiter, async (req, res) => {
  const input = loginSchema.parse(req.body);
  res.json(await service.login(input));
});

authRoutes.get('/me', requireAuth, async (req, res) => {
  // The token is valid, but the account behind it may have been deleted since
  // it was issued — a JWT cannot be revoked before it expires.
  const user = await service.getById(req.user!.sub);
  if (!user) throw ApiError.unauthorized('Account no longer exists.');
  res.json({ user });
});
