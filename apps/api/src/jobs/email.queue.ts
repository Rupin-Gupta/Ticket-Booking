import { Queue, Worker, type JobsOptions } from 'bullmq';
import { Prisma } from '@prisma/client';
import { getRedis } from '../lib/redis.js';
import { sendMail } from '../lib/mailer.js';
import { bookingCancelledEmail, bookingConfirmedEmail } from '../lib/emails.js';
import { renderQrDataUrl, verifyUrl } from '../lib/qr.js';
import { prisma } from '../lib/prisma.js';

const QUEUE = 'email';

export type EmailJob =
  | { kind: 'booking-confirmed'; bookingId: string }
  | { kind: 'booking-cancelled'; bookingId: string };

const JOB_OPTIONS: JobsOptions = {
  // Five tries over roughly a minute and a half. A provider blip should not
  // cost somebody their ticket, and a permanent failure should stop quickly.
  attempts: 5,
  backoff: { type: 'exponential', delay: 3_000 },
  removeOnComplete: { count: 50 },
  removeOnFail: { count: 100 },
};

let queue: Queue<EmailJob> | null = null;

function getQueue(): Queue<EmailJob> | null {
  const connection = getRedis();
  if (!connection) return null;
  queue ??= new Queue<EmailJob>(QUEUE, { connection });
  return queue;
}

/**
 * Hands an email to the queue and returns immediately.
 *
 * Never awaited by a request handler in a way that can fail it. A booking is
 * already committed by the time this runs; if the queue is unreachable the
 * booking still stands and the failure is logged loudly rather than turned
 * into a 500 for a customer whose seat is confirmed in the database.
 */
export async function enqueueEmail(job: EmailJob): Promise<void> {
  const q = getQueue();
  if (!q) {
    console.warn(`[email] REDIS_URL not set — ${job.kind} for ${job.bookingId} was not queued`);
    return;
  }
  try {
    await q.add(job.kind, job, JOB_OPTIONS);
  } catch (err) {
    console.error(`[email] could not queue ${job.kind} for ${job.bookingId}`, err);
  }
}

/**
 * Renders and sends. Reads the booking fresh rather than trusting a payload
 * serialised minutes ago — by the time a retry runs, the booking may have been
 * cancelled, and sending a confirmation for a cancelled booking is worse than
 * sending nothing.
 */
async function process(job: EmailJob) {
  const booking = await prisma.booking.findUnique({
    where: { id: job.bookingId },
    select: {
      reference: true,
      status: true,
      qrToken: true,
      customer: { select: { name: true, email: true } },
      show: {
        select: {
          startsAt: true,
          event: { select: { title: true, venue: { select: { name: true } } } },
        },
      },
      seats: {
        select: {
          priceAtBooking: true,
          showSeat: { select: { seat: { select: { row: true, number: true } } } },
        },
      },
    },
  });

  if (!booking) {
    console.warn(`[email] booking ${job.bookingId} no longer exists, skipping`);
    return;
  }

  const seats = booking.seats.map((s) => `${s.showSeat.seat.row}${s.showSeat.seat.number}`);

  if (job.kind === 'booking-cancelled') {
    await sendMail({
      to: booking.customer.email,
      subject: `Cancelled — ${booking.reference}`,
      html: bookingCancelledEmail({
        reference: booking.reference,
        eventTitle: booking.show.event.title,
        seats,
      }),
    });
    return;
  }

  if (booking.status !== 'CONFIRMED') {
    console.warn(`[email] booking ${booking.reference} is ${booking.status}, not confirming`);
    return;
  }

  // Decimal arithmetic, not Number(). A float cannot hold 0.10, and money that
  // is off by a cent in an email is money the customer will ask about.
  const total = booking.seats.reduce((sum, s) => sum.add(s.priceAtBooking), new Prisma.Decimal(0));
  const qrDataUrl = await renderQrDataUrl(booking.qrToken);

  await sendMail({
    to: booking.customer.email,
    subject: `Your tickets — ${booking.show.event.title} (${booking.reference})`,
    html: bookingConfirmedEmail({
      name: booking.customer.name,
      reference: booking.reference,
      eventTitle: booking.show.event.title,
      venue: booking.show.event.venue.name,
      startsAt: booking.show.startsAt.toUTCString(),
      seats,
      total: total.toFixed(2),
      qrDataUrl,
      verifyUrl: verifyUrl(booking.qrToken),
    }),
    attachments: [
      {
        filename: `ticket-${booking.reference}.png`,
        content: Buffer.from(qrDataUrl.split(',')[1]!, 'base64'),
      },
    ],
  });

  console.log(`[email] sent ${job.kind} for ${booking.reference} to ${booking.customer.email}`);
}

let worker: Worker<EmailJob> | null = null;

export function startEmailWorker() {
  const connection = getRedis();
  if (!connection) {
    console.warn('email worker not started — REDIS_URL is not set');
    return async () => {};
  }

  worker = new Worker<EmailJob>(QUEUE, async (job) => process(job.data), {
    connection,
    // The single most important setting on Upstash's metered free tier.
    // Default is 5s, which means a worker with nothing to do issues ~12
    // blocking commands a minute — about 518,000 a month, the entire free
    // allowance, before a single email is sent. At 60s it costs ~43,000.
    // A pushed job wakes the blocked worker immediately, so this is not
    // added latency.
    drainDelay: 60,
    concurrency: 3,
  });

  worker.on('failed', (job, err) =>
    console.error(`[email] job ${job?.id} failed (attempt ${job?.attemptsMade}):`, err.message),
  );

  console.log('email worker running (drainDelay 60s)');
  return async () => {
    await worker?.close();
    worker = null;
  };
}
