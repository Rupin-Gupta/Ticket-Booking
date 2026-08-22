from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import resend

from ..config import IS_PROD, settings


@dataclass(slots=True)
class Mail:
    to: str
    subject: str
    html: str
    #: Rendered inline in the HTML as a data URL *and* attached as a file, so
    #: the ticket survives a client that strips inline images.
    attachments: list[dict[str, object]] = field(default_factory=list)


if settings.RESEND_API_KEY:
    resend.api_key = settings.RESEND_API_KEY


async def send_mail(mail: Mail) -> None:
    """
    Sends, or explains loudly why it did not.

    With no API key configured the message is logged rather than dropped, so a
    fresh clone can walk the whole booking flow without an email account. That
    is a development convenience, never a silent production failure — the log
    says exactly what would have been sent and to whom.
    """
    if not settings.RESEND_API_KEY:
        print(f'[mail] RESEND_API_KEY not set — would have sent "{mail.subject}" to {mail.to}')
        return

    # Production never redirects: a customer's ticket must reach the customer.
    redirect = None if IS_PROD else settings.MAIL_REDIRECT_TO
    to = redirect or mail.to
    subject = f"[to: {mail.to}] {mail.subject}" if redirect else mail.subject

    if redirect:
        print(f"[mail] redirected {mail.to} -> {redirect}")

    params: dict[str, object] = {
        "from": settings.MAIL_FROM,
        "to": [to],
        "subject": subject,
        "html": mail.html,
    }
    if mail.attachments:
        params["attachments"] = mail.attachments

    # The Resend SDK is synchronous; run it off the event loop so a slow
    # provider cannot stall every other coroutine in the worker.
    #
    # Raised, not swallowed: this runs inside a job with retry and backoff, and
    # a failure that returns quietly would burn the retry budget doing nothing.
    await asyncio.to_thread(resend.Emails.send, params)
