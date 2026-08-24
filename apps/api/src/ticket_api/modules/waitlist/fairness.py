"""
Making the queue checkable instead of merely trustworthy.

The waitlist is FIFO by `joinedAt`, and until now a customer had to take that on
faith: the system says they were fourth, and the system also decides who gets
the seat. Two mechanisms change that.

**A signed receipt at join time.** The customer is handed an HMAC over the facts
that decide their place — which queue, when they joined, what position that
was. They cannot forge one, and the server cannot later quietly rewrite the
facts without the signature failing.

**A hash-chained offer log.** Every offer appends a row whose hash covers the
previous row's hash. Changing or removing an earlier offer breaks every hash
after it, so re-ordering the queue after the fact is detectable by anyone, not
just by whoever runs the database.

Neither stops a determined operator with database access from rewriting
everything from scratch. What they do is make quiet, partial tampering — the
realistic kind — leave evidence. That is the honest claim, and the docs make it
rather than claiming proof.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import OfferLog, iso

#: Version the payload shape, so a future field cannot silently invalidate
#: every receipt already issued.
RECEIPT_VERSION = 1

GENESIS = "0" * 64


def _canonical(payload: dict[str, object]) -> str:
    """
    Stable bytes for a payload.

    `sort_keys` and no whitespace: two servers must hash the same facts to the
    same string, and a dict that iterates in insertion order would make the
    signature depend on how the dict was built.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def receipt_payload(
    *, entry_id: str, show_id: str, category_id: str, joined_at: datetime, position: int
) -> dict[str, object]:
    return {
        "v": RECEIPT_VERSION,
        "entryId": entry_id,
        "showId": show_id,
        "categoryId": category_id,
        "joinedAt": iso(joined_at),
        "position": position,
    }


def sign(payload: dict[str, object]) -> str:
    """HMAC-SHA256 over the canonical payload, keyed by the server secret."""
    return hmac.new(
        settings.JWT_SECRET.encode(), _canonical(payload).encode(), hashlib.sha256
    ).hexdigest()


def verify(payload: dict[str, object], signature: str) -> bool:
    """`compare_digest`, not `==`: signature checks are a timing-attack surface."""
    return hmac.compare_digest(sign(payload), signature)


def link_hash(prev_hash: str, payload: dict[str, object]) -> str:
    """
    The chain link: this row's hash covers the previous row's hash.

    Change any earlier row and every hash after it stops matching, which is what
    makes a quiet re-ordering visible.
    """
    return hashlib.sha256((prev_hash + _canonical(payload)).encode()).hexdigest()


async def append_offer(
    session: AsyncSession,
    *,
    show_id: str,
    category_id: str,
    entry_id: str,
    show_seat_id: str,
    position: int,
    at: datetime,
) -> OfferLog:
    """
    Appends one offer to the chain.

    Inside the offer's own transaction on purpose — unlike seat signals, this is
    not telemetry. A log missing the offer it describes would be worse than no
    log, because it would read as proof that the offer never happened.
    """
    # Truncate to milliseconds BEFORE hashing. The column is TIMESTAMP(3), so
    # Postgres stores a rounded value; hashing the microsecond-precision one
    # would produce a chain that never verifies against what was written — the
    # hash would cover a timestamp that does not exist anywhere.
    at = at.replace(microsecond=(at.microsecond // 1000) * 1000)

    prev = (
        (
            await session.execute(
                select(OfferLog)
                .where(OfferLog.show_id == show_id)
                .order_by(OfferLog.seq.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    seq = (prev.seq + 1) if prev else 1
    prev_hash = prev.hash if prev else GENESIS

    payload = {
        "v": RECEIPT_VERSION,
        "seq": seq,
        "showId": show_id,
        "categoryId": category_id,
        # The entry, not the customer. The log is public, and who is waiting for
        # which seat is nobody else's business.
        "entryId": entry_id,
        "showSeatId": show_seat_id,
        "position": position,
        "at": iso(at),
    }

    row = OfferLog(
        show_id=show_id,
        category_id=category_id,
        entry_id=entry_id,
        show_seat_id=show_seat_id,
        position=position,
        seq=seq,
        at=at,
        prev_hash=prev_hash,
        hash=link_hash(prev_hash, payload),
    )
    session.add(row)
    return row


def replay(rows: list[OfferLog]) -> tuple[bool, int | None]:
    """
    Recomputes the chain. Returns (intact, first broken sequence number).

    Anyone holding the public log can run this; it needs no secret. That is the
    point — verification that requires trusting the verifier proves nothing.
    """
    prev_hash = GENESIS
    for row in sorted(rows, key=lambda r: r.seq):
        payload = {
            "v": RECEIPT_VERSION,
            "seq": row.seq,
            "showId": row.show_id,
            "categoryId": row.category_id,
            "entryId": row.entry_id,
            "showSeatId": row.show_seat_id,
            "position": row.position,
            "at": iso(row.at),
        }
        if row.prev_hash != prev_hash or row.hash != link_hash(prev_hash, payload):
            return (False, row.seq)
        prev_hash = row.hash
    return (True, None)
