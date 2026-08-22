import { PrismaClient } from '@prisma/client';
import { isProd } from '../env.js';

/**
 * One client for the process. Constructing it reads DATABASE_URL, so nothing
 * imports this module until a route actually needs the database — that keeps
 * `npm run dev` working on a clone that has not set up Supabase yet.
 *
 * `globalThis` cache is for tsx watch: a reload without it leaks a connection
 * pool per restart until Postgres refuses new connections.
 */
const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

export const prisma =
  globalForPrisma.prisma ??
  new PrismaClient({
    log: isProd ? ['warn', 'error'] : ['warn', 'error'],
  });

if (!isProd) globalForPrisma.prisma = prisma;
