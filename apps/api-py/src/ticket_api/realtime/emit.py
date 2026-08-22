"""
The publish side of realtime, kept in its own module with no dependencies.

Services import this; `realtime/server.py` imports the services to build the
snapshot. Splitting them is what keeps the import graph acyclic — and it means
a service can announce a change without knowing Socket.IO exists.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

SEAT_UPDATE = "seat:update"
SEAT_SYNC = "seat:sync"


def show_room(show_id: str) -> str:
    return f"show:{show_id}"


class Emitter(Protocol):
    async def emit(
        self, event: str, data: object, room: str | None = None
    ) -> None:  # pragma: no cover - structural type
        ...


_emitter: Emitter | None = None


def set_emitter(next_emitter: Emitter | None) -> None:
    global _emitter
    _emitter = next_emitter


def broadcast_seats(show_id: str, seats: list[dict[str, str]]) -> None:
    """
    Announce seat changes to everyone watching a show.

    **Call this after the transaction commits, never inside it.** Emitting from
    within means a rolled-back transaction has already told every browser the
    seat is gone, and nothing ever corrects them.

    Fire-and-forget: scheduled on the running loop rather than awaited, so a
    slow or wedged Socket.IO client can never delay a confirmed booking. A
    missed broadcast is a stale screen for a few seconds, not a wrong answer —
    the next read recomputes effective status from the database.

    Silently does nothing when realtime is not running — under test, or before
    the server has started.
    """
    if _emitter is None or not seats:
        return
    payload = {"showId": show_id, "seats": seats}
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (a sync script); nothing is listening anyway
    loop.create_task(_emitter.emit(SEAT_UPDATE, payload, room=show_room(show_id)))


def broadcast_status(show_id: str, seat_ids: list[str], status: str) -> None:
    """Convenience for the common case: several seats moving to one status."""
    broadcast_seats(show_id, [{"id": seat_id, "status": status} for seat_id in seat_ids])
