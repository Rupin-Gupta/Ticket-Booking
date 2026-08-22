import type { RequestHandler } from 'express';
import type { Role } from '@ticket/shared';
import { ApiError } from '../lib/errors.js';
import { verifyAccessToken, type TokenPayload } from '../lib/jwt.js';

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express {
    interface Request {
      user?: TokenPayload;
    }
  }
}

/** Rejects anything without a valid, unexpired bearer token. */
export const requireAuth: RequestHandler = (req, _res, next) => {
  const header = req.get('authorization');
  if (!header?.startsWith('Bearer ')) {
    return next(ApiError.unauthorized('Missing bearer token.'));
  }
  try {
    req.user = verifyAccessToken(header.slice('Bearer '.length).trim());
    next();
  } catch {
    // Expired, wrong signature, wrong algorithm — all the same to the client.
    // Saying which would tell an attacker whether they had the right secret.
    next(ApiError.unauthorized('Invalid or expired token.'));
  }
};

/**
 * Coarse role gate. Layer it on top of requireAuth.
 *
 * This is only half of authorisation — it says "some organiser", never "the
 * organiser who owns this event". Resource-ownership checks belong in the
 * service, and without them any organiser can read any other organiser's
 * revenue.
 */
export const requireRole =
  (roles: readonly Role[]): RequestHandler =>
  (req, _res, next) => {
    if (!req.user) return next(ApiError.unauthorized());
    if (!roles.includes(req.user.role)) {
      return next(ApiError.forbidden(`Requires role: ${roles.join(' or ')}.`));
    }
    next();
  };
