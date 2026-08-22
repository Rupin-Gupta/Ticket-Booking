import { prisma } from '../src/lib/prisma.js';
import { hashPassword } from '../src/lib/password.js';

/**
 * Demo accounts. Organiser and admin exist only here — nothing in the API
 * lets a client choose its own role, which is the point.
 *
 * Idempotent: re-running updates the existing rows rather than failing on the
 * unique email. Passwords are deliberately obvious; this seeds a demo, and
 * the README says so.
 */
const ACCOUNTS = [
  { email: 'admin@ticket.dev', name: 'Ada Admin', role: 'ADMIN' },
  { email: 'organiser@ticket.dev', name: 'Omar Organiser', role: 'ORGANISER' },
  { email: 'customer@ticket.dev', name: 'Cara Customer', role: 'CUSTOMER' },
  { email: 'customer2@ticket.dev', name: 'Cyrus Customer', role: 'CUSTOMER' },
] as const;

const PASSWORD = 'password123';

async function main() {
  // Hashed once rather than per account: Argon2 is deliberately slow, and
  // four identical passwords do not need four hashes.
  const passwordHash = await hashPassword(PASSWORD);

  for (const account of ACCOUNTS) {
    const user = await prisma.user.upsert({
      where: { email: account.email },
      update: { name: account.name, role: account.role, passwordHash },
      create: { ...account, passwordHash },
      select: { email: true, role: true },
    });
    console.log(`  ${user.role.padEnd(9)} ${user.email}`);
  }

  console.log(`\nAll four use the password: ${PASSWORD}`);
}

main()
  .catch((err) => {
    console.error(err);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
