"""
Alembic environment.

The connection string comes from the application's own config rather than
alembic.ini, so `alembic upgrade head` under NODE_ENV=test hits the test
database and refuses to fall back to production — the same guarantee the app
itself makes.

Migrations run against DIRECT_URL (session pooler, :5432) rather than
DATABASE_URL (transaction pooler, :6543). pgbouncer in transaction mode cannot
hold the advisory lock Alembic takes for the duration of a migration, and DDL
in a transaction pooler is asking for trouble. Rule 14.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from ticket_api.config import IS_TEST, active_database_url, settings, to_sqlalchemy_url
from ticket_api.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def migration_url() -> str:
    direct = settings.DIRECT_URL_TEST if IS_TEST else settings.DIRECT_URL
    return to_sqlalchemy_url(direct or active_database_url())


config.set_main_option("sqlalchemy.url", migration_url().replace("%", "%%"))


def run_migrations_offline() -> None:
    context.configure(
        url=migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # Same reason as the runtime engine: the pooler cannot carry a prepared
        # statement across connections. Rule 16.
        connect_args={"prepare_threshold": None},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
