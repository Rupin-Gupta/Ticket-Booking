import { z } from 'zod';

// Node loads .env natively (>= 20.12). No dotenv dependency.
// ponytail: try/catch because in production the platform injects real env vars
// and there is no .env file on disk.
try {
  process.loadEnvFile();
} catch {
  /* no .env file — expected on Render/Vercel */
}

const seconds = (fallback: number) => z.coerce.number().int().positive().default(fallback);

const schema = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  PORT: z.coerce.number().int().positive().default(4000),

  /** Comma-separated origins allowed to call the API. */
  WEB_URL: z.string().default('http://localhost:5173'),

  // --- Infrastructure. Optional so the app boots before the accounts exist;
  // requireEnv() below is what fails loudly when a subsystem actually needs one.
  DATABASE_URL: z.string().optional(),
  DIRECT_URL: z.string().optional(),
  REDIS_URL: z.string().optional(),

  // --- Auth
  JWT_SECRET: z.string().min(32).optional(),
  JWT_EXPIRES_IN: z.string().default('15m'),

  // --- Email
  RESEND_API_KEY: z.string().optional(),
  MAIL_FROM: z.string().default('Ticket Booking <onboarding@resend.dev>'),

  // --- Seat hold / waitlist tuning. The brief calls the hold TTL
  // "configurable"; tests also need to set it to 2s without waiting 10 minutes.
  HOLD_TTL_SECONDS: seconds(600),
  OFFER_TTL_SECONDS: seconds(600),
  SWEEPER_INTERVAL_MS: z.coerce.number().int().positive().default(10_000),
  MAX_SEATS_PER_HOLD: z.coerce.number().int().positive().default(6),
  MAX_ACTIVE_HOLDS_PER_USER: z.coerce.number().int().positive().default(2),
});

const parsed = schema.safeParse(process.env);

if (!parsed.success) {
  const issues = parsed.error.issues.map((i) => `  ${i.path.join('.')}: ${i.message}`).join('\n');
  throw new Error(`Invalid environment configuration:\n${issues}`);
}

export const env = parsed.data;

export const isProd = env.NODE_ENV === 'production';

/** Origins allowed by CORS. */
export const allowedOrigins = env.WEB_URL.split(',').map((o) => o.trim());

// `-?` strips the optional marker; without it every indexed access carries a
// stray `undefined` and the union collapses.
type OptionalKey = {
  [K in keyof typeof env]-?: undefined extends (typeof env)[K] ? K : never;
}[keyof typeof env];

/**
 * Read an env var that is optional at boot but required by whichever subsystem
 * is asking. Fails with the fix, not just the symptom.
 */
export function requireEnv(key: OptionalKey): string {
  const value = env[key];
  if (!value) {
    throw new Error(`Missing required environment variable ${key}. See apps/api/.env.example.`);
  }
  return value;
}

/** What /health reports, so a fresh clone can see what is still unwired. */
export const configured = {
  database: Boolean(env.DATABASE_URL),
  redis: Boolean(env.REDIS_URL),
  auth: Boolean(env.JWT_SECRET),
  email: Boolean(env.RESEND_API_KEY),
};
