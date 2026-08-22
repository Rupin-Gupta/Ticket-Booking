"""
Two checks, one interval (rule 4):

  1. expired HOLDS  -> AVAILABLE
  2. expired OFFERS -> EXPIRED, then advance_waitlist() decides where the seat
     goes — to the next person in line, or back on general sale if the queue has
     run dry. This is the loop that makes an ignored offer walk down the
     waitlist without anyone touching it.

Deliberately NOT an ARQ cron job. An idle Redis-polling worker costs roughly
518,000 commands a month on its own, and a job firing every ten seconds costs
millions — against a free-tier allowance of 500,000. This is a handful of
indexed statements against a database we are already connected to. Redis stays
for the email queue and the Socket.IO manager, which genuinely need it.
See ADR-018.

Neither check is a correctness guarantee. `effective_status()` already treats an
expired hold as free on every read, and an expired offer is refused on accept
regardless of whether it has been swept. The sweep is what makes both visible to
everyone else, and what moves the queue along when the offered customer simply
does nothing.

Safe on several instances at once: every statement is idempotent and guarded by
its own WHERE clause, and the queue pick uses FOR UPDATE SKIP LOCKED.

ponytail: an asyncio task with a sleep, not a scheduler library. Ten seconds is
a delay, not a schedule.
"""

from __future__ import annotations

import asyncio
import contextlib

from ..config import IS_TEST, settings
from ..modules.seats.service import sweep_expired_holds
from ..modules.waitlist.service import sweep_expired_offers
from .email_queue import enqueue_email


async def _sweep_once() -> None:
    released = await sweep_expired_holds()
    if released > 0:
        print(f"[sweeper] released {released} expired hold(s)")

    expired, offers = await sweep_expired_offers()
    if expired > 0:
        print(f"[sweeper] expired {expired} offer(s), re-offered {len(offers)}")

    # After the transactions have committed, never inside them: an email about
    # a seat a rollback took back is worse than a late one.
    for offer in offers:
        await enqueue_email({"kind": "waitlist-offer", "entryId": offer.entry_id})


async def _tick() -> None:
    try:
        await _sweep_once()
    except Exception:
        # Supabase's transaction pooler recycles idle connections, so a sweep
        # that has been quiet can find its socket closed. The pool reconnects on
        # the next attempt, so one retry turns a skipped sweep into a completed
        # one rather than an error log.
        try:
            await _sweep_once()
        except Exception as retry_err:  # noqa: BLE001 - never let a sweep kill the process
            print(f"[sweeper] failed twice: {str(retry_err).splitlines()[0]}")


async def _loop() -> None:
    interval = settings.SWEEPER_INTERVAL_MS / 1000
    # No eager tick: at startup nothing has connected yet, and firing here only
    # logs "cannot reach database" on every boot. The first sweep is one
    # interval away, well inside any hold or offer TTL.
    while True:
        await asyncio.sleep(interval)
        # Awaited in sequence, so a slow sweep cannot stack up behind itself.
        await _tick()


def start_sweeper() -> asyncio.Task[None] | None:
    if IS_TEST:
        return None
    task = asyncio.create_task(_loop())
    print(f"sweeper running every {settings.SWEEPER_INTERVAL_MS}ms (holds + offers)")
    return task


async def stop_sweeper(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
