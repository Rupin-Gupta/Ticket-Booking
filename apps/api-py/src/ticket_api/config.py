"""
Environment configuration, validated once at import.

Deliberately mirrors the retired TypeScript `env.ts` variable-for-variable, so
an existing `.env` keeps working without edits. Names stay SCREAMING_SNAKE even
where Python would prefer otherwise — they are the deployment contract, not
Python identifiers.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_dotenv() -> None:
    """
    Read .env into os.environ without a dependency.

    ponytail: pydantic-settings can read .env itself, but it will not find one
    two directories up when uvicorn is started from the repo root. Twelve lines
    beats debugging why config is empty in one shell and fine in another.
    """
    # parents: [0] ticket_api, [1] src, [2] the app root that holds .env
    app_root = Path(__file__).resolve().parents[2]
    for candidate in (Path.cwd() / ".env", app_root / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return


_load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=True)

    NODE_ENV: Literal["development", "test", "production"] = "development"
    PORT: int = 4000

    #: Comma-separated origins allowed to call the API.
    WEB_URL: str = "http://localhost:5173"

    # --- Infrastructure. Optional so the app boots before the accounts exist;
    # require_env() is what fails loudly when a subsystem actually needs one.
    DATABASE_URL: str | None = None
    DIRECT_URL: str | None = None
    REDIS_URL: str | None = None

    # --- Test database: a SECOND Supabase project. A suite that quietly writes
    # to production is worse than one that refuses to start.
    DATABASE_URL_TEST: str | None = None
    DIRECT_URL_TEST: str | None = None

    # --- Auth
    JWT_SECRET: str | None = None
    JWT_EXPIRES_IN: str = "15m"

    # --- Email
    RESEND_API_KEY: str | None = None
    MAIL_REDIRECT_TO: str | None = None
    MAIL_FROM: str = "Ticket Booking <onboarding@resend.dev>"

    # --- Seat hold / waitlist tuning. The brief calls the hold TTL
    # "configurable"; tests also need it at 2s without waiting ten minutes.
    HOLD_TTL_SECONDS: int = 600
    OFFER_TTL_SECONDS: int = 600
    SWEEPER_INTERVAL_MS: int = 10_000
    MAX_SEATS_PER_HOLD: int = 6
    MAX_ACTIVE_HOLDS_PER_USER: int = 2

    @field_validator("*", mode="before")
    @classmethod
    def _blank_as_unset(cls, v: object) -> object:
        """
        Treat an empty or whitespace-only value as absent.

        Copying .env.example leaves lines like `JWT_SECRET=""` behind, and an
        empty string is not None — it would pass the type check and then fail
        deep inside whichever subsystem trusted it.
        """
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("JWT_SECRET")
    @classmethod
    def _secret_long_enough(cls, v: str | None) -> str | None:
        if v is not None and len(v) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters.")
        return v


settings = Settings()

IS_PROD = settings.NODE_ENV == "production"
IS_TEST = settings.NODE_ENV == "test"

#: Origins allowed by CORS.
ALLOWED_ORIGINS = [o.strip() for o in settings.WEB_URL.split(",") if o.strip()]

#: What /health reports, so a fresh clone can see what is still unwired.
CONFIGURED = {
    "database": settings.DATABASE_URL is not None,
    "redis": settings.REDIS_URL is not None,
    "auth": settings.JWT_SECRET is not None,
    "email": settings.RESEND_API_KEY is not None,
}


def require_env(key: str) -> str:
    """
    Read a var that is optional at boot but required by whoever is asking.
    Fails with the fix, not just the symptom.
    """
    value = getattr(settings, key, None)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable {key}. See apps/api/.env.example."
        )
    return str(value)


def active_database_url() -> str:
    """
    The connection string this process should use.

    Under NODE_ENV=test this REFUSES to fall back to DATABASE_URL. A test suite
    that quietly writes to production is worse than one that will not start —
    the failure is loud, immediate, and names the fix.
    """
    if IS_TEST:
        if not settings.DATABASE_URL_TEST:
            raise RuntimeError(
                "DATABASE_URL_TEST is not set. Tests refuse to run against the "
                "production database. Create a second Supabase project and add "
                "DATABASE_URL_TEST and DIRECT_URL_TEST to apps/api/.env."
            )
        return settings.DATABASE_URL_TEST
    return require_env("DATABASE_URL")


def to_sqlalchemy_url(raw: str) -> str:
    """
    Adapt a Supabase connection string for SQLAlchemy + psycopg3.

    Two edits, both load-bearing:

    - `postgresql://` -> `postgresql+psycopg://` picks psycopg3 over the
      long-dead psycopg2 default.
    - `?pgbouncer=true` is a **Prisma-only** flag. psycopg passes unknown query
      parameters straight to the server, which rejects them, so leaving it in
      fails the connection outright.
    """
    import re

    url = re.sub(r"[?&]pgbouncer=true", "", raw)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def hold_ttl_seconds() -> int:
    return settings.HOLD_TTL_SECONDS


def offer_ttl_seconds() -> int:
    return settings.OFFER_TTL_SECONDS
