from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

# This file defines the API responses - either success, or failure, and
# returns the appropriate messages/data.


def success(message: str, data: Any | None = None, status_code: int = 200) -> JSONResponse:
    payload: dict[str, Any] = {
        "ok": True,
        "message": message,
        "error_code": None,
    }
    if data is not None:
        payload["data"] = data
    return JSONResponse(payload, status_code=status_code)


def error(message: str, error_code: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "message": message,
            "error_code": error_code,
        },
        status_code=status_code,
    )
