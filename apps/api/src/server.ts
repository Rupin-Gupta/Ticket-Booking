import { createApp } from './app.js';
import { env, configured } from './env.js';
import { startSweeper } from './jobs/sweeper.js';
import { startEmailWorker } from './jobs/email.queue.js';
import { closeRedis } from './lib/redis.js';

const stopSweeper = startSweeper();
const stopEmailWorker = startEmailWorker();

const server = createApp().listen(env.PORT, () => {
  console.log(`api listening on http://localhost:${env.PORT}  [${env.NODE_ENV}]`);

  const missing = Object.entries(configured)
    .filter(([, ok]) => !ok)
    .map(([name]) => name);
  if (missing.length) {
    console.warn(`not configured yet: ${missing.join(', ')} — see apps/api/.env.example`);
  }
});

// Drain in-flight requests instead of cutting them off mid-transaction.
// Phase 9 extends this to close BullMQ workers and disconnect Prisma.
for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.on(signal, () => {
    console.log(`\n${signal} — shutting down`);
    stopSweeper();
    // Let an in-flight send finish rather than losing the job mid-attempt.
    void stopEmailWorker()
      .catch(() => {})
      .then(closeRedis)
      .finally(() => server.close(() => process.exit(0)));
  });
}
