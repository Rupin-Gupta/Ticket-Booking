import { PrismaClient } from '@prisma/client';
import { isProd, requireEnv } from '../env.js';

/**
 * One client for the process.
 *
 * From Phase 1 on, every route needs the database, so this is imported at boot
 * rather than lazily. requireEnv() runs first so a missing connection string
 * fails immediately with a message that names the fix — better than starting
 * successfully and then 500-ing on every request with a Prisma stack trace.
 *
 * `globalThis` cache is for tsx watch: a reload without it leaks a connection
 * pool per restart until Postgres refuses new connections.
 */
requireEnv('DATABASE_URL');

const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

export const prisma =
  globalForPrisma.prisma ??
  new PrismaClient({
    log: ['warn', 'error'],
  });

if (!isProd) globalForPrisma.prisma = prisma;
