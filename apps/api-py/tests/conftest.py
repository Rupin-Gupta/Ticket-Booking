"""
Shared fixtures.

NODE_ENV is set here, before anything imports ticket_api, because
`config.active_database_url()` reads it at import time and refuses to fall back
to the production database. Setting it in the shell as well is harmless; setting
it *only* in the shell would mean a bare `pytest` silently pointed at
production, which is exactly the failure this whole arrangement exists to
prevent.
"""

from __future__ import annotations

import os

os.environ.setdefault("NODE_ENV", "test")

import asyncio  # noqa: E402
import secrets  # noqa: E402
import socket  # noqa: E402
from collections.abc import AsyncIterator, Callable  # noqa: E402
from datetime import timedelta  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
import uvicorn  # noqa: E402
from sqlalchemy import text  # noqa: E402

from ticket_api.app import create_app  # noqa: E402
from ticket_api.db import Session, dispose  # noqa: E402
from ticket_api.models import (  # noqa: E402
    Event,
    Role,
    Seat,
    SeatCategory,
    Show,
    ShowSeat,
    User,
    Venue,
    utcnow,
)
from ticket_api.security import hash_password, sign_access_token  # noqa: E402

# Order matters: children before parents, or the foreign keys refuse.
_TABLES = [
    "BookingSeat",
    "Booking",
    "WaitlistEntry",
    "ShowSeat",
    "SeatCategory",
    "Show",
    "Event",
    "Seat",
    "Venue",
    "User",
]


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
async def clean_database() -> AsyncIterator[None]:
    """
    Every test starts from an empty database.

    TRUNCATE ... CASCADE rather than per-test cleanup: it is one statement, it
    cannot leave orphans behind, and a test that fails half way through still
    leaves the next one a clean slate.
    """
    # Interpolated, not bound — SQL identifiers cannot be parameters. Safe here
    # and only here: _TABLES is a hardcoded constant, never anything from a
    # request. Rule 13 is about request data reaching raw SQL, which this is not.
    statement = "TRUNCATE " + ", ".join(f'"{t}"' for t in _TABLES) + " CASCADE"
    async with Session() as session:
        await session.execute(text(statement))
        await session.commit()
    yield


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """In-process client. Fine for everything except the concurrency tests."""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def live_server() -> AsyncIterator[str]:
    """
    A real uvicorn listener on a real port.

    The concurrency tests need this: httpx's in-process ASGI transport can
    serialise every request through one task, so a race run against it would
    pass even if the lock did nothing.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    await task


# One hash for the whole session. Argon2id is deliberately expensive, and
# hashing the same password once per user would dominate the suite's runtime.
_PASSWORD = "correct horse battery"
_HASH = hash_password(_PASSWORD)


@pytest.fixture
def password() -> str:
    return _PASSWORD


@pytest.fixture
def make_user() -> Callable[..., object]:
    async def _make(role: Role = Role.CUSTOMER, name: str | None = None) -> tuple[str, str]:
        """Returns (user_id, bearer_token)."""
        who = name or secrets.token_hex(4)
        async with Session() as session:
            user = User(
                email=f"{who}-{secrets.token_hex(3)}@example.test",
                name=who,
                password_hash=_HASH,
                role=role,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user.id, sign_access_token({"sub": user.id, "role": user.role.value})

    return _make


@pytest.fixture
def auth() -> Callable[[str], dict[str, str]]:
    return lambda token: {"Authorization": f"Bearer {token}"}


@pytest.fixture
def make_show() -> Callable[..., object]:
    """
    A venue, an event, one priced category and a show with its seats.

    Built directly rather than through the API because most tests are about what
    happens *after* a show exists, and driving six endpoints to get there makes
    every failure look like a setup failure.
    """

    async def _make(
        *, seats: int = 4, section: str = "Main", price: str = "100"
    ) -> dict[str, object]:
        tag = secrets.token_hex(4)
        async with Session() as session:
            organiser = User(
                email=f"org-{tag}@example.test",
                name="Organiser",
                password_hash=_HASH,
                role=Role.ORGANISER,
            )
            venue = Venue(name=f"Venue {tag}", address="1 Test Street")
            session.add_all([organiser, venue])
            await session.flush()

            seat_rows = [
                Seat(
                    venue_id=venue.id,
                    section=section,
                    row="A",
                    number=n,
                    pos_x=float(n),
                    pos_y=0.0,
                )
                for n in range(1, seats + 1)
            ]
            event = Event(
                organiser_id=organiser.id,
                venue_id=venue.id,
                title=f"Event {tag}",
                type="CONCERT",
            )
            session.add_all([*seat_rows, event])
            await session.flush()

            category = SeatCategory(
                event_id=event.id, name=section, price=price, sections=[section]
            )
            show = Show(event_id=event.id, starts_at=utcnow() + timedelta(days=30))
            session.add_all([category, show])
            await session.flush()

            show_seats = [
                ShowSeat(show_id=show.id, seat_id=s.id, category_id=category.id) for s in seat_rows
            ]
            session.add_all(show_seats)
            await session.commit()

            return {
                "organiser_id": organiser.id,
                "organiser_token": sign_access_token(
                    {"sub": organiser.id, "role": Role.ORGANISER.value}
                ),
                "venue_id": venue.id,
                "event_id": event.id,
                "category_id": category.id,
                "show_id": show.id,
                "seat_ids": [ss.id for ss in show_seats],
            }

    return _make


@pytest.fixture(scope="session", autouse=True)
async def _close_pool() -> AsyncIterator[None]:
    yield
    await dispose()
