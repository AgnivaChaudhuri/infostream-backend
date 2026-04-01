"""Shared API envelope builders used by all route handlers."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

def success(message: str, data: Any | None = None, status_code: int = 200) -> JSONResponse:
    """Create a standard success envelope with optional `data`."""
    payload: dict[str, Any] = {
        "ok": True,
        "message": message,
        "error_code": None,
    }
    if data is not None:
        payload["data"] = data
    return JSONResponse(payload, status_code=status_code)


def error(message: str, error_code: str, status_code: int) -> JSONResponse:
    """Create a standard error envelope with explicit error code."""
    return JSONResponse(
        {
            "ok": False,
            "message": message,
            "error_code": error_code,
        },
        status_code=status_code,
    )
