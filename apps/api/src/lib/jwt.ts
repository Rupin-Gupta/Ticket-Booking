import jwt from 'jsonwebtoken';
import type { Role } from '@ticket/shared';
import { env, requireEnv } from '../env.js';

export type TokenPayload = {
  sub: string;
  role: Role;
};

/**
 * HS256 is pinned explicitly on BOTH sign and verify.
 *
 * Never let the library infer the algorithm from the token header: an attacker
 * controls that header, and a verifier that trusts it accepts `alg: none` or a
 * token signed with a different scheme entirely. Pinning on verify is the half
 * that actually matters.
 */
const ALGORITHM = 'HS256' as const;

export function signAccessToken(payload: TokenPayload): string {
  return jwt.sign(payload, requireEnv('JWT_SECRET'), {
    algorithm: ALGORITHM,
    expiresIn: env.JWT_EXPIRES_IN,
  } as jwt.SignOptions);
}

/** Throws whatever jsonwebtoken throws; the caller turns that into a 401. */
export function verifyAccessToken(token: string): TokenPayload {
  const decoded = jwt.verify(token, requireEnv('JWT_SECRET'), {
    algorithms: [ALGORITHM],
  });
  if (typeof decoded === 'string') throw new Error('Unexpected string JWT payload');
  return { sub: String(decoded.sub), role: decoded['role'] as Role };
}
