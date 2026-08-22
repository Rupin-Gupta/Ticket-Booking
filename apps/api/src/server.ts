import { createServer } from 'node:http';
import { createApp } from './app.js';
import { env, configured } from './env.js';
import { startSweeper } from './jobs/sweeper.js';
import { startEmailWorker } from './jobs/email.queue.js';
import { startRealtime } from './realtime/index.js';
import { closeRedis } from './lib/redis.js';

const stopSweeper = startSweeper();
const stopEmailWorker = startEmailWorker();

// Socket.IO needs the raw HTTP server, not the Express app — it upgrades
// connections on the same port rather than opening a second one.
const server = createServer(createApp());
const stopRealtime = startRealtime(server);

server.listen(env.PORT, () => {
  console.log(`api listening on http://localhost:${env.PORT}  [${env.NODE_ENV}]`);

  const missing = Object.entries(configured)
    .filter(([, ok]) => !ok)
    .map(([name]) => name);
  if (missing.length) {
    console.warn(`not configured yet: ${missing.join(', ')} — see apps/api/.env.example`);
  }
});

// Drain in-flight requests instead of cutting them off mid-transaction, and
// let an in-flight email finish rather than losing the job mid-attempt.
for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.on(signal, () => {
    console.log(`\n${signal} — shutting down`);
    stopSweeper();
    void Promise.allSettled([stopRealtime(), stopEmailWorker()])
      .then(closeRedis)
      .finally(() => server.close(() => process.exit(0)));
  });
}
