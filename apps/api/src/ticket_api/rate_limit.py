"""
Per-IP rate limits.

Rate limits exist for the attacks a correctness guard cannot see. A row lock
stops two people racing for one seat; it does nothing about one script trying
ten thousand passwords, or holding every seat in the venue on purpose.

ponytail: an in-process sliding window rather than slowapi. The retired
TypeScript API used express-rate-limit with its default *memory* store, so
per-process counting is not a regression — it is exactly the behaviour being
replaced. Adding a dependency to reproduce it would be pure ceremony. If limits
ever need to hold across Render instances, the upgrade is a shared Redis
counter, and this is the one file that changes.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request

from .config import IS_TEST
from .errors import ApiError

#: Stop tracking an address once its window has been empty this long, so the
#: table cannot grow without bound on a public endpoint.
_IDLE_EVICT_SECONDS = 3600


def _client_ip(request: Request) -> str:
    """
    Render terminates TLS at its proxy, so the socket address is the proxy's.
    The first entry in X-Forwarded-For is the original client.

    Trusting a client-supplied header is normally wrong; here the header is
    rewritten by Render's proxy on the way in, and the fallback is the socket
    address, so a forged value cannot do better than rate-limit itself.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def limiter(
    window_seconds: int, limit: int, code: str, message: str
) -> Callable[[Request], Coroutine[Any, Any, None]]:
    hits: dict[str, deque[float]] = {}

    async def dependency(request: Request) -> None:
        # Disabled under test — the concurrency suite deliberately fires 20
        # simultaneous requests at one endpoint, which is exactly what this
        # blocks.
        if IS_TEST:
            return

        now = time.monotonic()
        cutoff = now - window_seconds

        for addr in [
            a for a, seen in hits.items() if seen and seen[-1] < now - _IDLE_EVICT_SECONDS
        ]:
            del hits[addr]

        seen = hits.setdefault(_client_ip(request), deque())
        while seen and seen[0] < cutoff:
            seen.popleft()

        if len(seen) >= limit:
            raise ApiError.too_many(code, message)
        seen.append(now)

    return dependency


#: Password guessing is the whole threat here, so this one is tight.
login_limiter = limiter(
    15 * 60, 10, "TOO_MANY_LOGIN_ATTEMPTS", "Too many login attempts. Try again in a few minutes."
)

#: Stops bulk account creation without getting in a real person's way.
register_limiter = limiter(
    60 * 60,
    5,
    "TOO_MANY_REGISTRATIONS",
    "Too many accounts created from this address. Try again later.",
)

#: Holds are the contended endpoint — the one a script would hammer to lock a
#: venue. Generous enough that a real person picking seats, changing their mind
#: and picking again never sees it.
hold_limiter = limiter(
    60, 20, "TOO_MANY_HOLD_ATTEMPTS", "Too many seat requests. Wait a moment and try again."
)
