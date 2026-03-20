from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response

from infostream.api.routes import router
from infostream.core.config import BackendConfig
from infostream.core.context import AppContext, create_context
from infostream.core.logging import configure_logging, get_logger


def create_app(config: BackendConfig | None = None, port: int = 0) -> FastAPI:
    effective_config = config or BackendConfig()
    configure_logging(effective_config)
    context: AppContext = create_context(effective_config, port=port)
    logger = get_logger("main")

    app = FastAPI(title="Infostream Backend", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.ctx = context
    app.include_router(router)

    @app.middleware("http")
    async def request_logging_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Log request lifecycle with duration and status code."""
        request_id = uuid.uuid4().hex[:10]
        started = time.monotonic()
        request_logger = logger.bind(
            component="api.request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            query=str(request.url.query),
            client_ip=request.client.host if request.client else None,
        )
        request_logger.debug("request_started", message="request started")
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.monotonic() - started) * 1000)
            request_logger.exception(
                "request_failed",
                message="request failed with unhandled exception",
                duration_ms=duration_ms,
            )
            raise

        duration_ms = int((time.monotonic() - started) * 1000)
        request_logger.info(
            "request_completed",
            message="request completed",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response

    @app.on_event("startup")
    async def on_startup() -> None:
        logger.info("app_startup", message="app startup event")
        await context.startup()

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        logger.info("app_shutdown", message="app shutdown event")
        await context.shutdown()

    return app
