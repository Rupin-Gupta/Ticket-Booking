"""
Database engine and transaction helpers.

The one file where a wrong default silently costs correctness, so the reasoning
lives here rather than in a commit message.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import IS_TEST, active_database_url, to_sqlalchemy_url

# Prisma's client-side maxWait/timeout, re-expressed as server-side timeouts.
# Postgres enforces these itself, which is strictly stronger: a client-side
# deadline can be missed by a client that is wedged, a server-side one cannot.
DEFAULT_LOCK_TIMEOUT_MS = 15_000
DEFAULT_STATEMENT_TIMEOUT_MS = 20_000

engine = create_async_engine(
    to_sqlalchemy_url(active_database_url()),
    connect_args={
        # RULE 16. Supabase's transaction pooler is pgbouncer, which cannot
        # carry a prepared statement across pooled connections — the statement
        # is prepared on one backend and executed on another, which has never
        # heard of it. None disables preparation entirely.
        #
        # This is why the driver is psycopg3 and not asyncpg: asyncpg leaks
        # prepared statements through the same pooler even with its own cache
        # disabled (supabase/supabase#39227, still open). Verified here at 250
        # concurrent contenders on one row: 1 winner, 0 errors.
        "prepare_threshold": None,
    },
    # Modest pool. The pooler in front of us is doing the real multiplexing;
    # opening a hundred connections to a pgbouncer just queues them there.
    pool_size=10,
    max_overflow=10,
    # Supabase free tier drops idle connections; without this the first query
    # after a quiet period fails instead of transparently reconnecting.
    pool_pre_ping=True,
    echo=False,
)

Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def transaction(
    *,
    lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
) -> AsyncIterator[AsyncSession]:
    """
    A transaction with bounded waiting, for every locked seat mutation.

    Without a lock_timeout a contended `SELECT ... FOR UPDATE` waits forever:
    twenty customers racing for one seat would pile up until the connection
    pool is exhausted and *unrelated* requests start failing. Bounded waiting
    turns that into a clean, fast rejection for the losers.

    `set_config(..., true)` rather than `SET LOCAL` because set_config accepts
    bind parameters — SET does not, and interpolating into DDL is the habit
    that eventually meets a value from `req` (RULE 13).
    """
    async with Session() as session, session.begin():
        await session.execute(
            text("SELECT set_config('lock_timeout', :ms, true)"),
            {"ms": f"{int(lock_timeout_ms)}ms"},
        )
        await session.execute(
            text("SELECT set_config('statement_timeout', :ms, true)"),
            {"ms": f"{int(statement_timeout_ms)}ms"},
        )
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A plain session for reads. No transaction wrapper, no timeouts."""
    async with Session() as session:
        yield session


async def ping() -> bool:
    """Cheapest possible liveness probe, for /health."""
    try:
        async with Session() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose() -> None:
    """Close the pool. Tests hang on exit without this."""
    await engine.dispose()


__all__ = [
    "IS_TEST",
    "Session",
    "dispose",
    "engine",
    "ping",
    "session_scope",
    "transaction",
]
