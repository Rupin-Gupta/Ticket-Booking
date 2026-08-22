import { Prisma } from '@prisma/client';
import type { Role } from '@ticket/shared';
import { prisma } from '../../lib/prisma.js';
import { ApiError } from '../../lib/errors.js';
import { hashPassword, verifyPassword } from '../../lib/password.js';
import { signAccessToken } from '../../lib/jwt.js';
import type { LoginInput, RegisterInput } from './schema.js';

export type PublicUser = {
  id: string;
  email: string;
  name: string;
  role: Role;
};

/** Explicit select. `passwordHash` must never reach a response body. */
const publicFields = { id: true, email: true, name: true, role: true } as const;

export async function register(input: RegisterInput) {
  const passwordHash = await hashPassword(input.password);

  try {
    const user = await prisma.user.create({
      data: {
        email: input.email,
        name: input.name,
        passwordHash,
        // Hard-coded, not taken from input. Organiser and admin accounts come
        // from the seed script or an admin-only promote endpoint.
        role: 'CUSTOMER',
      },
      select: publicFields,
    });
    return { user, accessToken: issueToken(user) };
  } catch (err) {
    // Let the unique index decide, rather than checking for an existing email
    // first — a check-then-insert races two simultaneous signups.
    if (err instanceof Prisma.PrismaClientKnownRequestError && err.code === 'P2002') {
      throw ApiError.conflict('EMAIL_TAKEN', 'An account with that email already exists.');
    }
    throw err;
  }
}

export async function login(input: LoginInput) {
  const user = await prisma.user.findUnique({
    where: { email: input.email },
    select: { ...publicFields, passwordHash: true },
  });

  // One message and one code for both "no such email" and "wrong password".
  // Distinguishing them tells an attacker which addresses have accounts.
  // verifyPassword() burns the same CPU either way so the timing does not
  // give away what the message withholds.
  const ok = await verifyPassword(user?.passwordHash ?? null, input.password);
  if (!user || !ok) {
    throw ApiError.unauthorized('Incorrect email or password.');
  }

  const { passwordHash: _discard, ...publicUser } = user;
  return { user: publicUser, accessToken: issueToken(publicUser) };
}

export function getById(id: string) {
  return prisma.user.findUnique({ where: { id }, select: publicFields });
}

const issueToken = (user: PublicUser) => signAccessToken({ sub: user.id, role: user.role });
