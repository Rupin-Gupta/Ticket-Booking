import { prisma } from '../src/lib/prisma.js';
import { hashPassword } from '../src/lib/password.js';
import { instantiateShowSeats } from '../src/modules/events/service.js';

/**
 * Demo data. Organiser and admin accounts exist only here — nothing in the API
 * lets a client choose its own role, which is the point.
 *
 * Idempotent: re-running updates rather than failing on unique constraints, so
 * it is safe to run against a database that already has data.
 */

const PASSWORD = 'password123';

const ACCOUNTS = [
  { email: 'admin@ticket.dev', name: 'Ada Admin', role: 'ADMIN' },
  { email: 'organiser@ticket.dev', name: 'Omar Organiser', role: 'ORGANISER' },
  { email: 'customer@ticket.dev', name: 'Cara Customer', role: 'CUSTOMER' },
  { email: 'customer2@ticket.dev', name: 'Cyrus Customer', role: 'CUSTOMER' },
] as const;

const ROW_LABELS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

/** Same grid maths as the venues service, so seeded venues look like built ones. */
function seatBlock(venueId: string, section: string, rows: number, perRow: number, startY: number) {
  const seats = [];
  for (let r = 0; r < rows; r++) {
    for (let n = 1; n <= perRow; n++) {
      seats.push({
        venueId,
        section,
        row: ROW_LABELS[r]!,
        number: n,
        posX: n - (perRow + 1) / 2,
        posY: startY + r,
      });
    }
  }
  return seats;
}

const daysFromNow = (days: number, hour: number) => {
  const d = new Date();
  d.setDate(d.getDate() + days);
  d.setHours(hour, 0, 0, 0);
  return d;
};

async function main() {
  // Hashed once: Argon2 is deliberately slow and four identical passwords do
  // not need four hashes.
  const passwordHash = await hashPassword(PASSWORD);

  for (const account of ACCOUNTS) {
    await prisma.user.upsert({
      where: { email: account.email },
      update: { name: account.name, role: account.role, passwordHash },
      create: { ...account, passwordHash },
    });
    console.log(`  ${account.role.padEnd(9)} ${account.email}`);
  }

  const organiser = await prisma.user.findUniqueOrThrow({
    where: { email: 'organiser@ticket.dev' },
  });

  // --- venue ---------------------------------------------------------------
  const existingVenue = await prisma.venue.findFirst({ where: { name: 'The Regal' } });
  const venue =
    existingVenue ??
    (await prisma.venue.create({
      data: { name: 'The Regal', address: '12 Marine Drive, Mumbai' },
    }));

  if ((await prisma.seat.count({ where: { venueId: venue.id } })) === 0) {
    // Two sections, deliberately different widths — the seat map has to handle
    // rows that are not all the same length.
    await prisma.seat.createMany({
      data: [
        ...seatBlock(venue.id, 'Premium', 3, 10, 0),
        ...seatBlock(venue.id, 'Standard', 5, 14, 5),
      ],
    });
  }
  const seatCount = await prisma.seat.count({ where: { venueId: venue.id } });
  console.log(`\n  Venue    ${venue.name} — ${seatCount} seats across Premium and Standard`);

  // --- event + pricing -----------------------------------------------------
  const existingEvent = await prisma.event.findFirst({
    where: { title: 'Interstellar (re-release)', organiserId: organiser.id },
  });
  const event =
    existingEvent ??
    (await prisma.event.create({
      data: {
        organiserId: organiser.id,
        venueId: venue.id,
        title: 'Interstellar (re-release)',
        type: 'MOVIE',
        description: 'Back on the big screen, in 70mm.',
      },
    }));

  for (const category of [
    { name: 'Premium', price: '450', sections: ['Premium'] },
    { name: 'Standard', price: '250', sections: ['Standard'] },
  ]) {
    await prisma.seatCategory.upsert({
      where: { eventId_name: { eventId: event.id, name: category.name } },
      update: { price: category.price, sections: category.sections },
      create: { eventId: event.id, ...category },
    });
  }
  console.log(`  Event    ${event.title} — Premium 450, Standard 250`);

  // --- shows, each with a full seat map ------------------------------------
  for (const startsAt of [daysFromNow(3, 19), daysFromNow(5, 21)]) {
    const already = await prisma.show.findFirst({ where: { eventId: event.id, startsAt } });
    if (already) {
      console.log(`  Show     ${startsAt.toISOString()} (already seeded)`);
      continue;
    }
    // Same transaction shape the API uses, so the seed exercises the real path
    // rather than a parallel one that could drift from it.
    const created = await prisma.$transaction(async (tx) => {
      const show = await tx.show.create({ data: { eventId: event.id, startsAt } });
      const count = await instantiateShowSeats(tx, {
        showId: show.id,
        eventId: event.id,
        venueId: venue.id,
      });
      return { show, count };
    });
    console.log(`  Show     ${startsAt.toISOString()} — ${created.count} seats generated`);
  }

  console.log(`\nAll four accounts use the password: ${PASSWORD}`);
}

main()
  .catch((err) => {
    console.error(err);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
