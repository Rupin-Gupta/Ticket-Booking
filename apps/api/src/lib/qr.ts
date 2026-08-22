import { randomBytes } from 'node:crypto';
import QRCode from 'qrcode';
import { env } from '../env.js';

/**
 * Tokens that stand in for a real seat are bearer credentials. 32 bytes from
 * the CSPRNG — never Math.random(), which is seeded predictably and is not
 * designed to resist anyone trying to guess the next value, and never derived
 * from a counter or the booking id.
 */
export const randomToken = () => randomBytes(32).toString('hex');

/**
 * Human-facing booking reference, e.g. BK-7F3K2.
 *
 * Deliberately short and deliberately NOT what the QR encodes: it is printed
 * on tickets, read aloud at a counter, and quoted in email, so it has to be
 * typo-resistant rather than unguessable. I and O are omitted because they are
 * indistinguishable from 1 and 0 in most fonts.
 */
const ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';

export function bookingReference(): string {
  const bytes = randomBytes(5);
  let out = '';
  for (const byte of bytes) out += ALPHABET[byte % ALPHABET.length];
  return `BK-${out}`;
}

/** The URL the QR resolves to. Scanning it hits the server, which is the only
 *  party that can say whether a ticket is real. */
/**
 * Where a waitlist offer email points. Time-limited and single use — the token
 * is cleared the moment the offer is accepted or expires, so a forwarded link
 * stops working rather than quietly handing the seat to whoever clicks it.
 */
export const offerUrl = (offerToken: string) =>
  `${env.WEB_URL.split(',')[0]!.trim()}/offers/${offerToken}`;

export const verifyUrl = (qrToken: string) =>
  `${env.WEB_URL.split(',')[0]!.trim()}/verify/${qrToken}`;

/**
 * PNG data URL of the QR.
 *
 * Encodes a verification URL rather than the booking's own data. A QR carrying
 * raw JSON is forgeable by anyone with a QR generator — a scanner reading it
 * has no way to tell a real ticket from a printed one — and a QR carrying the
 * short human reference is guessable by hand.
 */
export const renderQrDataUrl = (qrToken: string) =>
  QRCode.toDataURL(verifyUrl(qrToken), {
    errorCorrectionLevel: 'M',
    margin: 1,
    width: 320,
  });
