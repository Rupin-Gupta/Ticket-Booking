"""
Realtime seat updates.

**No authentication, on purpose.** Everything this layer emits is already
available from `GET /shows/:id/seats` without a token, and the broadcast shape
carries no `heldByMe` and no `holdExpiresAt` — those are per-viewer answers a
shared payload cannot give. Adding a handshake we never read from would be
security theatre; the protection that matters is that there is nothing private
in the payload to begin with.
"""

from __future__ import annotations

from typing import Any

import socketio

from ..config import ALLOWED_ORIGINS, IS_TEST, settings
from ..modules.seats.service import get_seat_map
from .emit import SEAT_SYNC, set_emitter, show_room

#: One socket has no business watching hundreds of shows at once.
MAX_ROOMS_PER_SOCKET = 10

SHOW_JOIN = "show:join"
SHOW_LEAVE = "show:leave"


def create_socket_server() -> socketio.AsyncServer:
    """
    python-socketio 5.x speaks Socket.IO protocol revision 5, which is what the
    frontend's socket.io-client 4.x speaks. The browser code needs no change.
    """
    manager: Any = None
    if settings.REDIS_URL:
        # Without this each process broadcasts only to its own connected
        # sockets, so with two instances roughly half the viewers silently miss
        # every update — and it never reproduces locally on one process
        # (rule 15). AsyncRedisManager handles both the publish and the
        # subscribe connection itself, which is the one thing simpler here than
        # in the Node version, where a subscriber client cannot issue ordinary
        # commands and two had to be tracked by hand for shutdown.
        manager = socketio.AsyncRedisManager(settings.REDIS_URL)
        print("realtime: redis manager wired (multi-instance safe)")
    else:
        print("realtime: no REDIS_URL — broadcasts will not cross process boundaries")

    sio = socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins=ALLOWED_ORIGINS,
        client_manager=manager,
        # Trim the default: a customer holding seats on a dead connection should
        # be noticed before their hold expires, not after.
        ping_timeout=20,
    )

    @sio.event
    async def connect(sid: str, _environ: dict[str, Any]) -> None:  # noqa: ARG001
        pass

    @sio.on(SHOW_JOIN)
    async def show_join(sid: str, payload: dict[str, Any] | None) -> None:
        show_id = (payload or {}).get("showId")
        if not isinstance(show_id, str) or not show_id:
            return
        if len(sio.rooms(sid)) > MAX_ROOMS_PER_SOCKET:
            return

        await sio.enter_room(sid, show_room(show_id))

        # A full snapshot on join, so a late arrival is never rendering a map
        # assembled from updates it missed. The viewer is anonymous here, hence
        # None — the browser already has its own map from the REST call and
        # reconciles ownership locally.
        try:
            seats = await get_seat_map(show_id, None)
        except Exception:
            # An unknown show id is a client bug, not something to crash on.
            return
        await sio.emit(
            SEAT_SYNC,
            {"showId": show_id, "seats": [s.model_dump() for s in seats]},
            to=sid,
        )

    @sio.on(SHOW_LEAVE)
    async def show_leave(sid: str, payload: dict[str, Any] | None) -> None:
        show_id = (payload or {}).get("showId")
        if isinstance(show_id, str) and show_id:
            await sio.leave_room(sid, show_room(show_id))

    set_emitter(sio)
    print("realtime listening (rooms keyed show:{id})")
    return sio


def realtime_enabled() -> bool:
    return not IS_TEST
