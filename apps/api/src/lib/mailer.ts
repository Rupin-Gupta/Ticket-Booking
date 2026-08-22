import { Resend } from 'resend';
import { env } from '../env.js';

export type Mail = {
  to: string;
  subject: string;
  html: string;
  /** Rendered inline in the HTML as a data URL and attached as a file, so the
   *  ticket survives a client that strips inline images. */
  attachments?: { filename: string; content: Buffer }[];
};

const resend = env.RESEND_API_KEY ? new Resend(env.RESEND_API_KEY) : null;

/**
 * Sends, or explains loudly why it did not.
 *
 * With no API key configured the message is logged rather than dropped, so a
 * fresh clone can walk the whole booking flow without an email account. That
 * is a development convenience, never a silent production failure — the log
 * says exactly what would have been sent and to whom.
 */
export async function sendMail(mail: Mail): Promise<void> {
  if (!resend) {
    console.warn(`[mail] RESEND_API_KEY not set — would have sent "${mail.subject}" to ${mail.to}`);
    return;
  }

  // Production never redirects: a customer's ticket must reach the customer.
  const redirect = env.NODE_ENV === 'production' ? undefined : env.MAIL_REDIRECT_TO;
  const to = redirect ?? mail.to;
  const subject = redirect ? `[to: ${mail.to}] ${mail.subject}` : mail.subject;

  if (redirect) console.log(`[mail] redirected ${mail.to} -> ${redirect}`);

  const { error } = await resend.emails.send({
    from: env.MAIL_FROM,
    to,
    subject,
    html: mail.html,
    ...(mail.attachments ? { attachments: mail.attachments } : {}),
  });

  // Thrown, not swallowed: this runs inside a job with retry and backoff, and
  // a failure that returns quietly would burn the retry budget doing nothing.
  if (error) {
    throw new Error(`Resend refused the message: ${error.name} — ${error.message}`);
  }
}
