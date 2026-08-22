"""Tokens, human-facing references, and the QR image itself."""

from __future__ import annotations

import base64
import io
import secrets

import qrcode

from ..config import settings

# Re-exported so callers do not have to know which module owns the CSPRNG.
from ..security import random_token  # noqa: F401

# Human-facing booking reference alphabet.
#
# Deliberately short and deliberately NOT what the QR encodes: it is printed on
# tickets, read aloud at a counter, and quoted in email, so it has to be
# typo-resistant rather than unguessable. I and O are omitted because they are
# indistinguishable from 1 and 0 in most fonts.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def booking_reference() -> str:
    """e.g. BK-7F3K2"""
    return "BK-" + "".join(secrets.choice(ALPHABET) for _ in range(5))


def _web_origin() -> str:
    """First entry of WEB_URL — the rest are additional CORS origins, not links."""
    return settings.WEB_URL.split(",")[0].strip()


def offer_url(offer_token: str) -> str:
    """
    Where a waitlist offer email points.

    Time-limited and single use — the token is cleared the moment the offer is
    accepted or expires, so a forwarded link stops working rather than quietly
    handing the seat to whoever clicks it.
    """
    return f"{_web_origin()}/offers/{offer_token}"


def verify_url(qr_token: str) -> str:
    """The URL the QR resolves to. Scanning it hits the server, which is the
    only party that can say whether a ticket is real."""
    return f"{_web_origin()}/verify/{qr_token}"


def render_qr_data_url(qr_token: str) -> str:
    """
    PNG data URL of the QR.

    Encodes a verification URL rather than the booking's own data. A QR carrying
    raw JSON is forgeable by anyone with a QR generator — a scanner reading it
    has no way to tell a real ticket from a printed one — and a QR carrying the
    short human reference is guessable by hand.
    """
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        border=1,
        box_size=10,
    )
    qr.add_data(verify_url(qr_token))
    qr.make(fit=True)

    buffer = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
