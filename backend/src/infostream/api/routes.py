"""HTTP route handlers for Infostream's backend API surface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Query, Request
from pydantic import ValidationError
from starlette.responses import StreamingResponse

from infostream.api.responses import error, success
from infostream.core.context import AppContext
from infostream.core.logging import get_logger
from infostream.core.validation import parse_rfc3339
from infostream.digest.service import DigestBusyError, RunAlreadyFinishedError, RunNotFoundError
from infostream.models.schemas import InstallSourceRequest, LlmRequest, ScheduleRequest
from infostream.sources.manager import SourceError

# This file implements all the API endpoints

router = APIRouter()  # The standard API router used in FastAPI
LOGGER = get_logger("api.routes")


def _ctx(request: Request) -> AppContext:
    """Return typed application context from FastAPI state."""
    return cast(AppContext, request.app.state.ctx)


def _parse_interests_payload(raw_body: bytes) -> str:
    """Parse and validate interests payload with strict 400-style semantics.

    This path intentionally avoids FastAPI/Pydantic auto-validation so malformed
    payloads map to the PDF-required INVALID_INTERESTS response.
    """
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Request body must be valid JSON") from exc

    if not isinstance(data, dict):
        raise ValueError("Payload must be a JSON object")
    interests = data.get("interests")
    if not isinstance(interests, str):
        raise ValueError("interests must be a string")
    return interests


def _parse_refs_query(refs: str, max_refs: int) -> list[str]:
    """Parse comma-separated item refs and enforce a hard cap."""
    refs_list = [part.strip() for part in refs.split(",") if part.strip()]
    if len(refs_list) > max_refs:
        raise ValueError(f"refs exceeds max size of {max_refs}")
    return refs_list


def _sse_encode(event: dict[str, Any]) -> str:
    """Encode event dict in SSE wire format."""
    event_type = str(event.get("type", "message"))
    payload = json.dumps(event, ensure_ascii=True)
    return f"event: {event_type}\ndata: {payload}\n\n"


@router.get("/health")
async def health(request: Request) -> Any:
    ctx = _ctx(request)
    return success(
        "Health check succeeded",
        {
            "api_version": ctx.config.api_version,
            "scheduler_running": ctx.scheduler.is_running(),
            "digest_running": ctx.digest_service.is_running(),
        },
    )


@router.get("/sources/installed")
async def sources_installed(request: Request) -> Any:
    ctx = _ctx(request)
    try:
        sources = [source.model_dump() for source in ctx.source_manager.list_installed()]
        LOGGER.debug(
            "sources_installed_loaded", message="loaded installed sources", count=len(sources)
        )
        return success("Installed sources loaded", {"sources": sources})
    except (FileNotFoundError, json.JSONDecodeError, ValidationError):
        LOGGER.exception("sources_installed_failed", message="failed to load installed sources")
        return error("installed_sources.json is corrupted", "INTERNAL_ERROR", 500)


@router.get("/sources/enabled")
async def sources_enabled(request: Request) -> Any:
    ctx = _ctx(request)
    try:
        sources = [source.model_dump() for source in ctx.source_manager.list_enabled()]
        LOGGER.debug("sources_enabled_loaded", message="loaded enabled sources", count=len(sources))
        return success("Enabled sources loaded", {"sources": sources})
    except (FileNotFoundError, json.JSONDecodeError, ValidationError):
        LOGGER.exception("sources_enabled_failed", message="failed to read enabled sources")
        return error("Unable to read enabled sources", "INTERNAL_ERROR", 500)


@router.post("/sources/{source_uid}/enable")
async def source_enable(request: Request, source_uid: str) -> Any:
    ctx = _ctx(request)
    try:
        source = ctx.source_manager.enable(source_uid)
        LOGGER.info("source_enabled", message="source enabled", source_uid=source_uid)
        return success("Source enabled", source.model_dump())
    except SourceError as exc:
        LOGGER.warning(
            "source_enable_rejected",
            message="source enable rejected",
            source_uid=source_uid,
            error_code=exc.error_code,
            detail=exc.message,
        )
        return error(exc.message, exc.error_code, exc.status_code)
    except OSError:
        LOGGER.exception(
            "source_enable_failed",
            message="source enable failed due to os error",
            source_uid=source_uid,
        )
        return error("Internal failure enabling source", "INTERNAL_ERROR", 500)


@router.post("/sources/{source_uid}/disable")
async def source_disable(request: Request, source_uid: str) -> Any:
    ctx = _ctx(request)
    try:
        source = ctx.source_manager.disable(source_uid)
        LOGGER.info("source_disabled", message="source disabled", source_uid=source_uid)
        return success("Source disabled", source.model_dump())
    except SourceError as exc:
        LOGGER.warning(
            "source_disable_rejected",
            message="source disable rejected",
            source_uid=source_uid,
            error_code=exc.error_code,
            detail=exc.message,
        )
        return error(exc.message, exc.error_code, exc.status_code)
    except OSError:
        LOGGER.exception(
            "source_disable_failed",
            message="source disable failed due to os error",
            source_uid=source_uid,
        )
        return error("Failure writing source state", "INTERNAL_ERROR", 500)


@router.post("/sources/install")
async def source_install(request: Request, payload: InstallSourceRequest) -> Any:
    ctx = _ctx(request)
    LOGGER.info(
        "source_install_requested", message="source install requested", source_path=payload.path
    )
    try:
        source = ctx.source_manager.install(Path(payload.path))
        LOGGER.info("source_installed", message="source installed", source_uid=source.uid)
        return success("Source installed", source.model_dump(), status_code=201)
    except SourceError as exc:
        LOGGER.warning(
            "source_install_rejected",
            message="source install rejected",
            source_path=payload.path,
            error_code=exc.error_code,
            detail=exc.message,
        )
        return error(exc.message, exc.error_code, exc.status_code)


@router.delete("/sources/{source_uid}")
async def source_uninstall(request: Request, source_uid: str) -> Any:
    ctx = _ctx(request)
    in_use = source_uid in ctx.digest_service.active_sources() or ctx.db.run_is_active_for_source(
        source_uid
    )
    try:
        ctx.source_manager.uninstall(source_uid, in_use=in_use)
        LOGGER.info("source_uninstalled", message="source uninstalled", source_uid=source_uid)
        return success("Source uninstalled")
    except SourceError as exc:
        LOGGER.warning(
            "source_uninstall_rejected",
            message="source uninstall rejected",
            source_uid=source_uid,
            error_code=exc.error_code,
            detail=exc.message,
        )
        return error(exc.message, exc.error_code, exc.status_code)


@router.get("/interests")
async def interests_get(request: Request) -> Any:
    ctx = _ctx(request)
    try:
        settings = ctx.state_store.read_settings()
        return success("Interests loaded", {"interests": settings.interests})
    except (OSError, ValidationError, json.JSONDecodeError):
        return error("settings.json is missing or invalid", "INTERNAL_ERROR", 500)


@router.put("/interests")
async def interests_put(request: Request) -> Any:
    """Update free-form interests text with strict payload validation."""
    ctx = _ctx(request)
    try:
        raw_body = await request.body()
        interests = _parse_interests_payload(raw_body)
    except ValueError as exc:
        LOGGER.warning(
            "interests_invalid_payload",
            message="invalid interests payload",
            detail=str(exc),
        )
        return error(str(exc), "INVALID_INTERESTS", 400)

    try:
        settings = ctx.state_store.read_settings()
        settings.interests = interests
        ctx.state_store.write_settings(settings)
        LOGGER.info("interests_updated", message="interests updated", length=len(interests))
        return success("Interests updated", {"interests": interests})
    except OSError:
        LOGGER.exception("interests_write_failed", message="failed to write interests settings")
        return error("Failed to write settings", "INTERNAL_ERROR", 500)


@router.get("/llm/current")
async def llm_get(request: Request) -> Any:
    ctx = _ctx(request)
    try:
        settings = ctx.state_store.read_settings()
        if not settings.llm.unique_id:
            return error("LLM is not configured", "LLM_NOT_CONFIGURED", 404)
        return success("LLM loaded", settings.llm.model_dump())
    except (ValidationError, OSError, json.JSONDecodeError):
        return error("LLM config is invalid", "INTERNAL_ERROR", 500)


@router.put("/llm/current")
async def llm_put(request: Request, payload: LlmRequest) -> Any:
    """Store current LLM provider selection/configuration."""
    ctx = _ctx(request)

    if not payload.unique_id.strip():
        LOGGER.warning(
            "llm_put_rejected",
            message="llm put rejected empty unique_id",
            error_code="LLM_NOT_FOUND",
        )
        return error("Model is not installed", "LLM_NOT_FOUND", 422)
    if payload.unique_id in ctx.config.unavailable_llm_ids:
        LOGGER.warning(
            "llm_put_rejected",
            message="llm put rejected unavailable model",
            error_code="LLM_UNAVAILABLE",
            unique_id=payload.unique_id,
        )
        return error("Model is unavailable", "LLM_UNAVAILABLE", 503)

    settings = ctx.state_store.read_settings()
    settings.llm.name = payload.name
    settings.llm.mode = payload.mode
    settings.llm.unique_id = payload.unique_id
    settings.llm.config = payload.config
    ctx.state_store.write_settings(settings)
    LOGGER.info(
        "llm_updated",
        message="llm configuration updated",
        unique_id=payload.unique_id,
        mode=payload.mode,
    )
    return success("LLM configuration updated", settings.llm.model_dump())


@router.post("/digest/run")
async def digest_run(request: Request) -> Any:
    ctx = _ctx(request)
    if not ctx.scheduler.is_running():
        LOGGER.warning(
            "digest_run_rejected",
            message="digest run rejected scheduler not ready",
            error_code="DIGEST_BUSY",
        )
        return error("Scheduler is not ready", "DIGEST_BUSY", 503)

    try:
        run_id = await ctx.digest_service.launch_run("manual")
        LOGGER.info("digest_run_accepted", message="manual digest run accepted", run_id=run_id)
        return success("Digest run accepted", {"run_id": run_id}, status_code=202)
    except DigestBusyError:
        LOGGER.warning(
            "digest_run_rejected",
            message="digest run rejected already running",
            error_code="DIGEST_ALREADY_RUNNING",
        )
        return error("Digest already running", "DIGEST_ALREADY_RUNNING", 409)
    except Exception:
        LOGGER.exception(
            "digest_run_failed", message="digest run launch failed", error_code="RUN_FAILED"
        )
        return error("Failure launching run", "RUN_FAILED", 500)


@router.put("/digest/schedule")
async def digest_schedule(request: Request, payload: ScheduleRequest) -> Any:
    ctx = _ctx(request)
    try:
        _ = parse_rfc3339(payload.schedule)
    except ValueError:
        LOGGER.warning(
            "digest_schedule_rejected",
            message="digest schedule rejected invalid rfc3339",
            error_code="INVALID_SCHEDULE",
            schedule=payload.schedule,
        )
        return error("Invalid schedule; expected RFC3339 with timezone", "INVALID_SCHEDULE", 422)

    if payload.run_every and payload.spacing <= 0:
        LOGGER.warning(
            "digest_schedule_rejected",
            message="digest schedule rejected invalid spacing",
            error_code="INVALID_SCHEDULE",
            spacing=payload.spacing,
        )
        return error("Invalid schedule spacing", "INVALID_SCHEDULE", 422)

    try:
        settings = ctx.state_store.read_settings()
        settings.schedule = payload.schedule
        settings.run_every = payload.run_every
        settings.spacing = payload.spacing
        ctx.state_store.write_settings(settings)
        LOGGER.info(
            "digest_schedule_updated",
            message="digest schedule updated",
            schedule=payload.schedule,
            run_every=payload.run_every,
            spacing=payload.spacing,
        )
        return success("Schedule updated", settings.model_dump())
    except Exception:
        LOGGER.exception(
            "digest_schedule_failed",
            message="digest schedule write failed",
            error_code="SCHEDULER_ERROR",
        )
        return error("Scheduler failure", "SCHEDULER_ERROR", 500)


@router.get("/digest/current")
async def digest_current(request: Request) -> Any:
    ctx = _ctx(request)
    try:
        digest = ctx.db.get_current_digest()
    except Exception:
        return error("Digest parse error", "DIGEST_CORRUPT", 500)
    if digest is None:
        return error("No digest exists yet", "DIGEST_NOT_FOUND", 404)
    return success("Current digest loaded", digest.model_dump())


@router.get("/digest/history")
async def digest_history(request: Request) -> Any:
    ctx = _ctx(request)
    try:
        digests = [digest.model_dump() for digest in ctx.db.list_digest_history()]
        return success("Digest history loaded", {"digests": digests})
    except Exception:
        return error("Database failure", "INTERNAL_ERROR", 500)


@router.get("/runs/latest")
async def runs_latest(request: Request) -> Any:
    ctx = _ctx(request)
    run = ctx.db.get_latest_run()
    if run is None:
        return error("No runs found", "RUN_NOT_FOUND", 404)
    results = [result.model_dump() for result in ctx.db.list_run_source_results(run.run_id)]
    return success("Latest run loaded", {"run": run.model_dump(), "sources": results})


@router.get("/runs/{run_id}")
async def runs_get(request: Request, run_id: str) -> Any:
    ctx = _ctx(request)
    run = ctx.db.get_run(run_id)
    if run is None:
        return error("Run not found", "RUN_NOT_FOUND", 404)
    results = [result.model_dump() for result in ctx.db.list_run_source_results(run_id)]
    return success("Run loaded", {"run": run.model_dump(), "sources": results})


@router.post("/runs/{run_id}/cancel")
async def runs_cancel(request: Request, run_id: str) -> Any:
    """Request cancellation of a queued/running run."""
    ctx = _ctx(request)
    try:
        await ctx.digest_service.cancel_run(run_id)
        LOGGER.info("run_cancel_requested", message="run cancel requested", run_id=run_id)
        return success("Run cancel requested", {"run_id": run_id})
    except RunNotFoundError:
        LOGGER.warning(
            "run_cancel_rejected",
            message="run cancel rejected missing run",
            run_id=run_id,
            error_code="RUN_NOT_FOUND",
        )
        return error("Run not found", "RUN_NOT_FOUND", 404)
    except RunAlreadyFinishedError:
        LOGGER.warning(
            "run_cancel_rejected",
            message="run cancel rejected terminal run",
            run_id=run_id,
            error_code="RUN_ALREADY_FINISHED",
        )
        return error("Run already finished", "RUN_ALREADY_FINISHED", 409)


@router.get("/items")
async def items_lookup(request: Request, refs: str = Query(default="")) -> Any:
    """Resolve item_ref citations into source/title/url metadata."""
    ctx = _ctx(request)
    try:
        refs_list = _parse_refs_query(refs, ctx.config.items_lookup_max_refs)
    except ValueError as exc:
        LOGGER.warning(
            "items_lookup_invalid_refs",
            message="invalid refs query",
            detail=str(exc),
        )
        return error(str(exc), "INVALID_REFS", 422)
    items = ctx.db.get_items_by_refs(refs_list)
    LOGGER.debug(
        "items_lookup_loaded",
        message="items lookup resolved refs",
        requested=len(refs_list),
        found=len(items),
    )
    return success("Items loaded", {"items": items})


@router.get("/events/stream")
async def events_stream(request: Request) -> StreamingResponse:
    """Server-Sent Events stream for run lifecycle notifications."""
    ctx = _ctx(request)
    LOGGER.debug("events_stream_connected", message="sse subscriber connected")
    queue = await ctx.event_broker.subscribe()

    async def generator() -> Any:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield _sse_encode(event)
                except TimeoutError:
                    # Keepalive comments prevent idle proxies from closing the stream.
                    yield ": keepalive\n\n"
        finally:
            await ctx.event_broker.unsubscribe(queue)
            LOGGER.debug("events_stream_disconnected", message="sse subscriber disconnected")

    return StreamingResponse(generator(), media_type="text/event-stream")
