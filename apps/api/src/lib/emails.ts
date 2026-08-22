/**
 * Email templates.
 *
 * Table-based layout with inline styles, because email clients are not
 * browsers: Outlook still renders through Word, and flexbox, grid and external
 * stylesheets are all unreliable. Colours are hard-coded rather than tokenised
 * for the same reason — custom properties do not survive most clients.
 */

const WRAP = (body: string) => `
<!doctype html>
<html lang="en"><body style="margin:0;padding:24px;background:#f6f7f9;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#0f172a">
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width:520px;margin:0 auto;background:#ffffff;border:1px solid #d8dde5;border-radius:12px">
    <tr><td style="padding:24px">${body}</td></tr>
  </table>
  <p style="max-width:520px;margin:16px auto 0;font-size:12px;color:#64748b;text-align:center">
    Ticket Booking · this message was sent because a booking was made with this address.
  </p>
</body></html>`;

const row = (label: string, value: string) => `
  <tr>
    <td style="padding:6px 0;font-size:14px;color:#475569">${label}</td>
    <td style="padding:6px 0;font-size:14px;text-align:right;font-weight:600">${value}</td>
  </tr>`;

export function bookingConfirmedEmail(input: {
  name: string;
  reference: string;
  eventTitle: string;
  venue: string;
  startsAt: string;
  seats: string[];
  total: string;
  qrDataUrl: string;
  verifyUrl: string;
}) {
  return WRAP(`
    <p style="margin:0 0 4px;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#ea580c">Booking confirmed</p>
    <h1 style="margin:0 0 16px;font-size:22px">${escapeHtml(input.eventTitle)}</h1>

    <table role="presentation" cellpadding="0" cellspacing="0" width="100%">
      ${row('Reference', escapeHtml(input.reference))}
      ${row('When', escapeHtml(input.startsAt))}
      ${row('Where', escapeHtml(input.venue))}
      ${row('Seats', escapeHtml(input.seats.join(', ')))}
      ${row('Total', escapeHtml(input.total))}
    </table>

    <div style="margin:24px 0;padding:20px;background:#f6f7f9;border-radius:10px;text-align:center">
      <img src="${input.qrDataUrl}" alt="QR code for booking ${escapeHtml(input.reference)}" width="200" height="200" style="display:block;margin:0 auto 12px;border-radius:6px" />
      <p style="margin:0;font-size:13px;color:#475569">Show this at the door.</p>
    </div>

    <p style="margin:0;font-size:13px;color:#475569">
      Cannot see the code? <a href="${input.verifyUrl}" style="color:#2563eb">Open your ticket</a>.
    </p>`);
}

export function bookingCancelledEmail(input: {
  reference: string;
  eventTitle: string;
  seats: string[];
}) {
  return WRAP(`
    <p style="margin:0 0 4px;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#64748b">Booking cancelled</p>
    <h1 style="margin:0 0 16px;font-size:22px">${escapeHtml(input.eventTitle)}</h1>
    <p style="margin:0 0 12px;font-size:14px;color:#475569">
      Booking <strong>${escapeHtml(input.reference)}</strong> has been cancelled and seats
      ${escapeHtml(input.seats.join(', '))} released.
    </p>
    <p style="margin:0;font-size:14px;color:#475569">The QR code on that ticket no longer works.</p>`);
}

/** Event titles and names come from users. Never interpolate them raw. */
function escapeHtml(value: string) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}
