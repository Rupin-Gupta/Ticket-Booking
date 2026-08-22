import { env } from '../env.js';
import { sweepExpiredHolds } from '../modules/seats/service.js';
import { sweepExpiredOffers } from '../modules/waitlist/service.js';
import { enqueueEmail } from './email.queue.js';

/**
 * Two checks, one interval (rule 4):
 *
 *   1. expired HOLDS  → AVAILABLE
 *   2. expired OFFERS → EXPIRED, then advanceWaitlist() decides where the seat
 *      goes — to the next person in line, or back on general sale if the queue
 *      has run dry. This is the loop that makes an ignored offer walk down the
 *      waitlist without anyone touching it.
 *
 * Deliberately NOT a BullMQ repeatable job. An idle BullMQ worker's blocking
 * poll costs roughly 518,000 Redis commands a month on its own, and a job
 * firing every ten seconds costs millions — against a free-tier allowance of
 * 500,000. This is a handful of indexed statements against a database we are
 * already connected to. Redis stays for the email queue and the Socket.IO
 * adapter, which genuinely need it. See ADR-018.
 *
 * Neither check is a correctness guarantee. `effectiveStatus()` already treats
 * an expired hold as free on every read, and an expired offer is refused on
 * accept regardless of whether it has been swept. The sweep is what makes both
 * visible to everyone else, and what moves the queue along when the offered
 * customer simply does nothing.
 *
 * Safe on several instances at once: every statement is idempotent and guarded
 * by its own WHERE clause, and the queue pick uses FOR UPDATE SKIP LOCKED.
 *
 * ponytail: setInterval, not node-cron. Ten seconds is a delay, not a schedule.
 */
export function startSweeper() {
  if (env.NODE_ENV === 'test') return () => {};

  let running = false;

  const sweep = async () => {
    const released = await sweepExpiredHolds();
    if (released > 0) console.log(`[sweeper] released ${released} expired hold(s)`);

    const { expired, offers } = await sweepExpiredOffers();
    if (expired > 0) {
      console.log(`[sweeper] expired ${expired} offer(s), re-offered ${offers.length}`);
    }

    // After the transactions have committed, never inside them: an email about
    // a seat a rollback took back is worse than a late one.
    for (const offer of offers) {
      void enqueueEmail({ kind: 'waitlist-offer', entryId: offer.entryId });
    }
  };

  const tick = async () => {
    // A slow sweep must not stack up behind itself.
    if (running) return;
    running = true;
    try {
      await sweep();
    } catch {
      // Supabase's transaction pooler recycles idle connections, so a sweep
      // that has been quiet can find its socket closed (P1017). Prisma
      // reconnects on the next attempt, so one retry turns a skipped sweep
      // into a completed one rather than an error log.
      try {
        await sweep();
      } catch (retryErr) {
        // Never let a failed sweep kill the process.
        console.error(
          '[sweeper] failed twice:',
          retryErr instanceof Error ? retryErr.message.split('\n')[0] : retryErr,
        );
      }
    } finally {
      running = false;
    }
  };

  const timer = setInterval(tick, env.SWEEPER_INTERVAL_MS);
  // Do not hold the event loop open on shutdown.
  timer.unref();
  // No eager tick: at import time nothing has connected yet, and firing here
  // only logs "can't reach database server" on every boot. The first sweep is
  // one interval away, well inside any hold or offer TTL.

  console.log(`sweeper running every ${env.SWEEPER_INTERVAL_MS}ms (holds + offers)`);
  return () => clearInterval(timer);
}
