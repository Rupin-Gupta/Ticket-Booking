import { env } from '../env.js';
import { sweepExpiredHolds } from '../modules/seats/service.js';

/**
 * Releases expired holds on a timer.
 *
 * Deliberately NOT a BullMQ repeatable job. An idle BullMQ worker's blocking
 * poll costs roughly 518,000 Redis commands a month on its own, and a
 * repeatable job firing every ten seconds costs millions — against a free-tier
 * allowance of 500,000. This work is two indexed UPDATE statements against a
 * database we are already connected to; a queue adds a metered dependency and
 * buys nothing. Redis stays for the email queue and the Socket.IO adapter,
 * which genuinely need it. See ADR-018.
 *
 * `CLAUDE.md` rule 4 asks for "a scheduler or database-level expiry" — this is
 * the scheduler, and `effectiveStatus()` is the database-level half.
 *
 * Safe to run on several instances at once: the WHERE clause is the guard and
 * the statement is idempotent. Two sweepers converge rather than conflict.
 *
 * ponytail: setInterval, not node-cron. Ten seconds is not a schedule, it is a
 * delay. Reach for cron when something has to run at 3am on a Tuesday.
 */
export function startSweeper() {
  if (env.NODE_ENV === 'test') return () => {};

  let running = false;

  const tick = async () => {
    // A slow sweep must not stack up behind itself — on a cold database the
    // first query can take longer than the interval.
    if (running) return;
    running = true;
    try {
      const released = await sweepExpiredHolds();
      if (released > 0) console.log(`[sweeper] released ${released} expired hold(s)`);
    } catch (err) {
      // Never let a failed sweep kill the process. Correctness does not depend
      // on this running; it only makes expiry visible to other viewers sooner.
      console.error('[sweeper] failed', err);
    } finally {
      running = false;
    }
  };

  const timer = setInterval(tick, env.SWEEPER_INTERVAL_MS);
  // Do not hold the event loop open on shutdown.
  timer.unref();
  void tick();

  console.log(`sweeper running every ${env.SWEEPER_INTERVAL_MS}ms`);
  return () => clearInterval(timer);
}
