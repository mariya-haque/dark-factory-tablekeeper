"""Error contract (spec §5): every 4xx/5xx body is {"error": {"code", "message"}}."""
from __future__ import annotations

from starlette.responses import JSONResponse


class JSONUtf8Response(JSONResponse):
    """JSON with an explicit charset, as §3.4 requires."""

    media_type = "application/json; charset=utf-8"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str | None = None):
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message or code.replace("_", " ")


def error_response(status: int, code: str, message: str) -> JSONUtf8Response:
    return JSONUtf8Response({"error": {"code": code, "message": message}}, status_code=status)


def malformed(message: str = "request body is malformed") -> ApiError:
    return ApiError(400, "malformed_request", message)


def invalid(message: str) -> ApiError:
    return ApiError(422, "validation_failed", message)


def not_found(message: str = "not found") -> ApiError:
    return ApiError(404, "not_found", message)
