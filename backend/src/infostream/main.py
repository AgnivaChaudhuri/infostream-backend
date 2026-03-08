from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from infostream.api.routes import router
from infostream.core.config import BackendConfig
from infostream.core.context import AppContext, create_context
from infostream.core.logging import configure_logging


def create_app(config: BackendConfig | None = None, port: int = 0) -> FastAPI:
    configure_logging()
    effective_config = config or BackendConfig()
    context: AppContext = create_context(effective_config, port=port)

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

    @app.on_event("startup")
    async def on_startup() -> None:
        await context.startup()

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        await context.shutdown()

    return app
