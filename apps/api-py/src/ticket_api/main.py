"""
Process entrypoint.

  Development:  uvicorn ticket_api.main:asgi --reload --port 4000
  Production:   uvicorn ticket_api.main:asgi --host 0.0.0.0 --port $PORT

Socket.IO wraps the FastAPI app rather than listening separately: it upgrades
connections on the same port, exactly as the Node version did with the raw HTTP
server.
"""

from __future__ import annotations

import socketio

from .app import create_app
from .config import CONFIGURED, settings
from .realtime.server import create_socket_server, realtime_enabled

app = create_app()

if realtime_enabled():
    sio = create_socket_server()
    #: The object uvicorn serves. Socket.IO handles /socket.io/*; everything
    #: else falls through to FastAPI.
    asgi: object = socketio.ASGIApp(sio, other_asgi_app=app)
else:
    asgi = app


def _report_configuration() -> None:
    missing = [name for name, ok in CONFIGURED.items() if not ok]
    if missing:
        print(f"not configured yet: {', '.join(missing)} — see apps/api/.env.example")


_report_configuration()


def main() -> None:
    """`python -m ticket_api.main`, for parity with `npm start`."""
    import uvicorn

    uvicorn.run(
        "ticket_api.main:asgi",
        host="0.0.0.0",  # noqa: S104 - Render routes to the container's public interface
        port=settings.PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
