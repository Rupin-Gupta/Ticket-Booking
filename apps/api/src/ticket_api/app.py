"""
The FastAPI application factory and the single place an error becomes a
response body.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from .config import ALLOWED_ORIGINS, CONFIGURED, IS_PROD, settings
from .db import dispose, ping
from .errors import ApiError
from .jobs.sweeper import start_sweeper, stop_sweeper
from .modules.auth.routes import router as auth_router
from .modules.bookings.routes import router as booking_router
from .modules.bookings.routes import verify_router
from .modules.events.routes import event_router
from .modules.events.routes import show_router as event_show_router
from .modules.lab.routes import router as lab_router
from .modules.organiser.routes import router as organiser_router
from .modules.seats.routes import hold_router
from .modules.seats.routes import show_router as seat_show_router
from .modules.venues.routes import router as venue_router
from .modules.waitlist.routes import router as waitlist_router
from .modules.waitlist.routes import show_router as waitlist_show_router

_STARTED = time.monotonic()

# helmet's defaults, minus the ones that only make sense for a server rendering
# HTML. This is a JSON API: it never returns a document, so a CSP governing
# script sources protects nothing. These four do real work.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=15552000; includeSubDomains",
}


async def database_status() -> Literal["up", "unreachable"]:
    """
    Round-trips one query to Postgres. Two jobs:

    1. tells a fresh clone whether its connection string actually *works*,
       rather than only whether it is present
    2. gives the daily keep-alive something to hit — Supabase pauses a free
       project after 7 days with no database activity, and unpausing is manual.
       A dashboard visit does not count; a query does.

    Reports rather than raises: an unreachable database is information, not a
    reason for the health endpoint itself to fail.
    """
    return "up" if await ping() else "unreachable"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    Starts the sweeper with the process and drains it on shutdown.

    Draining matters: cutting a sweep off mid-transaction would leave seats
    locked until Postgres notices the connection is gone, and the pool has to be
    disposed or the process will not exit.
    """
    task = start_sweeper()
    try:
        yield
    finally:
        await stop_sweeper(task)
        await dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        lifespan=lifespan,
        title="Ticket Booking API",
        version="1.0.0",
        description="Seat holds with row-level locking, FIFO waitlist offers, QR tickets.",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    # ---------------------------------------------------------- error shapes
    # Every failure leaves through here, so the body shape is identical whether
    # it came from a service, a validator, or a bug.

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, err: ApiError) -> JSONResponse:
        body: dict[str, object] = {"code": err.code, "message": err.message}
        if err.details:
            body["details"] = err.details
        return JSONResponse(status_code=err.status, content={"error": body})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, err: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "VALIDATION_FAILED",
                    "message": "Request validation failed.",
                    "details": [
                        {
                            # Drop the leading "body"/"query" segment so the path
                            # matches what the client actually sent.
                            "path": ".".join(str(p) for p in e["loc"][1:]),
                            "message": e["msg"],
                        }
                        for e in err.errors()
                    ],
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, err: Exception) -> JSONResponse:
        # Anything reaching here is a bug, not a handled condition. Log it in
        # full; never leak the message to the client in production.
        import traceback

        print("[unhandled]", "".join(traceback.format_exception(err)))
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Something went wrong." if IS_PROD else str(err),
                }
            },
        )

    # ----------------------------------------------------------------- routes

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, object]:
        """Liveness plus a wiring checklist, so a fresh clone can see what is
        still unconfigured without reading the code."""
        return {
            "ok": True,
            "env": settings.NODE_ENV,
            "uptimeSeconds": round(time.monotonic() - _STARTED),
            "configured": CONFIGURED,
            "database": await database_status(),
        }

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(venue_router, prefix="/api/v1")
    app.include_router(event_router, prefix="/api/v1")
    app.include_router(event_show_router, prefix="/api/v1")
    # Seat map and holds hang off a show but belong to their own module, so
    # they mount a second router on the same /shows prefix.
    app.include_router(seat_show_router, prefix="/api/v1")
    app.include_router(hold_router, prefix="/api/v1")
    app.include_router(waitlist_show_router, prefix="/api/v1")
    app.include_router(waitlist_router, prefix="/api/v1")
    app.include_router(booking_router, prefix="/api/v1")
    app.include_router(verify_router, prefix="/api/v1")
    app.include_router(organiser_router, prefix="/api/v1")
    app.include_router(lab_router, prefix="/api/v1")

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def not_found(request: Request, path: str) -> JSONResponse:
        raise ApiError.not_found("ROUTE_NOT_FOUND", f"No route for {request.method} /{path}.")

    return app
