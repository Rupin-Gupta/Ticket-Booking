"""
Every failure the API returns is an ApiError. One shape, one place that decides
the status code — services raise, the handler formats.
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    @staticmethod
    def bad_request(code: str, message: str, details: Any = None) -> ApiError:
        return ApiError(400, code, message, details)

    @staticmethod
    def unauthorized(message: str = "Authentication required.") -> ApiError:
        return ApiError(401, "UNAUTHORIZED", message)

    @staticmethod
    def forbidden(message: str = "You do not have access to this resource.") -> ApiError:
        return ApiError(403, "FORBIDDEN", message)

    @staticmethod
    def not_found(code: str, message: str) -> ApiError:
        return ApiError(404, code, message)

    @staticmethod
    def conflict(code: str, message: str) -> ApiError:
        """Lost a race: seat taken, already waitlisted, offer already used."""
        return ApiError(409, code, message)

    @staticmethod
    def gone(code: str, message: str) -> ApiError:
        """Time-limited thing that has run out — an expired waitlist offer."""
        return ApiError(410, code, message)

    @staticmethod
    def too_many(code: str, message: str) -> ApiError:
        return ApiError(429, code, message)
