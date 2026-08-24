"""
Email templates.

Table-based layout with inline styles, because email clients are not browsers:
Outlook still renders through Word, and flexbox, grid and external stylesheets
are all unreliable. Colours are hard-coded rather than tokenised for the same
reason — custom properties do not survive most clients.
"""

from __future__ import annotations

from html import escape


def _esc(value: str) -> str:
    """Event titles and names come from users. Never interpolate them raw."""
    return escape(value, quote=True)


def _wrap(body: str) -> str:
    return f"""
<!doctype html>
<html lang="en"><body style="margin:0;padding:24px;background:#f6f7f9;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#0f172a">
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width:520px;margin:0 auto;background:#ffffff;border:1px solid #d8dde5;border-radius:12px">
    <tr><td style="padding:24px">{body}</td></tr>
  </table>
  <p style="max-width:520px;margin:16px auto 0;font-size:12px;color:#64748b;text-align:center">
    Ticket Booking · this message was sent because a booking was made with this address.
  </p>
</body></html>"""


def _row(label: str, value: str) -> str:
    return f"""
  <tr>
    <td style="padding:6px 0;font-size:14px;color:#475569">{label}</td>
    <td style="padding:6px 0;font-size:14px;text-align:right;font-weight:600">{value}</td>
  </tr>"""


def booking_confirmed_email(
    *,
    reference: str,
    event_title: str,
    venue: str,
    starts_at: str,
    seats: list[str],
    total: str,
    qr_data_url: str,
    verify_link: str,
) -> str:
    rows = "".join(
        [
            _row("Reference", _esc(reference)),
            _row("When", _esc(starts_at)),
            _row("Where", _esc(venue)),
            _row("Seats", _esc(", ".join(seats))),
            _row("Total", _esc(total)),
        ]
    )
    return _wrap(f"""
    <p style="margin:0 0 4px;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#ea580c">Booking confirmed</p>
    <h1 style="margin:0 0 16px;font-size:22px">{_esc(event_title)}</h1>

    <table role="presentation" cellpadding="0" cellspacing="0" width="100%">{rows}</table>

    <div style="margin:24px 0;padding:20px;background:#f6f7f9;border-radius:10px;text-align:center">
      <img src="{qr_data_url}" alt="QR code for booking {_esc(reference)}" width="200" height="200" style="display:block;margin:0 auto 12px;border-radius:6px" />
      <p style="margin:0;font-size:13px;color:#475569">Show this at the door.</p>
    </div>

    <p style="margin:0;font-size:13px;color:#475569">
      Cannot see the code? <a href="{verify_link}" style="color:#2563eb">Open your ticket</a>.
    </p>""")


def booking_cancelled_email(*, reference: str, event_title: str, seats: list[str]) -> str:
    return _wrap(f"""
    <p style="margin:0 0 4px;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#64748b">Booking cancelled</p>
    <h1 style="margin:0 0 16px;font-size:22px">{_esc(event_title)}</h1>
    <p style="margin:0 0 12px;font-size:14px;color:#475569">
      Booking <strong>{_esc(reference)}</strong> has been cancelled and seats
      {_esc(", ".join(seats))} released.
    </p>
    <p style="margin:0;font-size:14px;color:#475569">The QR code on that ticket no longer works.</p>""")


def show_cancelled_email(
    *, reference: str, event_title: str, venue: str, starts_at: str, seats: list[str]
) -> str:
    """
    The show is off. Different from a customer cancelling their own booking:
    they did not choose this, so it says who cancelled and what happens next
    rather than just confirming an action they took.
    """
    return _wrap(f"""
    <p style="margin:0 0 4px;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#b91c1c">Show cancelled</p>
    <h1 style="margin:0 0 16px;font-size:22px">{_esc(event_title)}</h1>
    <p style="margin:0 0 12px;font-size:14px;color:#475569">
      The performance at {_esc(venue)} on {_esc(starts_at)} has been cancelled by
      the organiser. Your booking <strong>{_esc(reference)}</strong> for
      {_esc(", ".join(seats))} has been cancelled with it, and you are not being
      charged.
    </p>
    <p style="margin:0;font-size:14px;color:#475569">
      The QR code on that ticket no longer works. Nothing is needed from you.
    </p>""")


def waitlist_offer_email(
    *,
    event_title: str,
    venue: str,
    starts_at: str,
    category: str,
    price: str,
    minutes: int,
    claim_url: str,
) -> str:
    rows = "".join(
        [
            _row("When", _esc(starts_at)),
            _row("Where", _esc(venue)),
            _row("Category", _esc(category)),
            _row("Price", _esc(price)),
        ]
    )
    return _wrap(f"""
    <p style="margin:0 0 4px;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#ea580c">A seat opened up</p>
    <h1 style="margin:0 0 12px;font-size:22px">{_esc(event_title)}</h1>

    <p style="margin:0 0 16px;font-size:14px;color:#475569">
      Someone cancelled, and you were next in line for {_esc(category)}.
      This seat is held for you and nobody else &mdash; but only for
      <strong>{minutes} minutes</strong>. After that it goes to the next person.
    </p>

    <table role="presentation" cellpadding="0" cellspacing="0" width="100%">{rows}</table>

    <div style="margin:24px 0;text-align:center">
      <a href="{claim_url}"
         style="display:inline-block;padding:14px 28px;background:#ea580c;color:#ffffff;text-decoration:none;border-radius:10px;font-weight:600;font-size:15px">
        Claim this seat
      </a>
    </div>

    <p style="margin:0;font-size:12px;color:#64748b;word-break:break-all">
      Or paste this link: {claim_url}
    </p>""")
