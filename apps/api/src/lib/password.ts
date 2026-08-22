import argon2 from 'argon2';

/**
 * Argon2id — OWASP's first choice in the Password Storage Cheat Sheet, with
 * bcrypt listed as the legacy fallback.
 *
 * The type is pinned explicitly rather than relying on the library default, so
 * a dependency bump cannot silently move us onto argon2i or argon2d. The cost
 * parameters are the library defaults (m=64MiB, t=3, p=4), which are already
 * above the OWASP minimum of m=19MiB, t=2, p=1.
 */
const OPTIONS = { type: argon2.argon2id } as const;

export const hashPassword = (plain: string) => argon2.hash(plain, OPTIONS);

/**
 * Wrong-but-well-formed hash, used to burn the same CPU time on a login for an
 * email that does not exist as on one that does. Without it, "no such user"
 * returns in microseconds while a real user costs ~50ms, and that difference is
 * a working account-enumeration oracle.
 *
 * Generated once at startup rather than hard-coded, so it always matches the
 * cost parameters above.
 */
const decoyHash = hashPassword('a password nobody has: ' + Math.random().toString(36) + Date.now());

export async function verifyPassword(hash: string | null, plain: string): Promise<boolean> {
  if (hash === null) {
    await argon2.verify(await decoyHash, plain).catch(() => false);
    return false;
  }
  try {
    return await argon2.verify(hash, plain);
  } catch {
    // Malformed hash in the database — treat as a failed login, never a 500.
    return false;
  }
}
