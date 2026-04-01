"""ASGI module export for process managers and production servers."""

from __future__ import annotations

from infostream.main import create_app

# Expose a fully initialized FastAPI app for `uvicorn infostream.asgi:app`.
app = create_app()
