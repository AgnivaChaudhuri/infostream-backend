from __future__ import annotations

"""HTTP route handlers for Infostream's backend API surface."""

import json
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Query, Request
from pydantic import ValidationError

from infostream.api.responses import error, success
from infostream.core.context import AppContext
from infostream.core.validation import parse_rfc3339
from infostream.digest.service import DigestBusyError
from infostream.models.schemas import InstallSourceRequest, LlmRequest, ScheduleRequest
from infostream.sources.manager import SourceError

# This file implements all the API endpoints

router = APIRouter() # The standard API router used in FastAPI


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
        return success("Installed sources loaded", {"sources": sources})
    except (FileNotFoundError, json.JSONDecodeError, ValidationError):
        return error("installed_sources.json is corrupted", "INTERNAL_ERROR", 500)


@router.get("/sources/enabled")
async def sources_enabled(request: Request) -> Any:
    ctx = _ctx(request)
    try:
        sources = [source.model_dump() for source in ctx.source_manager.list_enabled()]
        return success("Enabled sources loaded", {"sources": sources})
    except (FileNotFoundError, json.JSONDecodeError, ValidationError):
        return error("Unable to read enabled sources", "INTERNAL_ERROR", 500)


@router.post("/sources/{source_uid}/enable")
async def source_enable(request: Request, source_uid: str) -> Any:
    ctx = _ctx(request)
    try:
        source = ctx.source_manager.enable(source_uid)
        return success("Source enabled", source.model_dump())
    except SourceError as exc:
        return error(exc.message, exc.error_code, exc.status_code)
    except OSError:
        return error("Internal failure enabling source", "INTERNAL_ERROR", 500)


@router.post("/sources/{source_uid}/disable")
async def source_disable(request: Request, source_uid: str) -> Any:
    ctx = _ctx(request)
    try:
        source = ctx.source_manager.disable(source_uid)
        return success("Source disabled", source.model_dump())
    except SourceError as exc:
        return error(exc.message, exc.error_code, exc.status_code)
    except OSError:
        return error("Failure writing source state", "INTERNAL_ERROR", 500)


@router.post("/sources/install")
async def source_install(request: Request, payload: InstallSourceRequest) -> Any:
    ctx = _ctx(request)
    try:
        source = ctx.source_manager.install(Path(payload.path))
        return success("Source installed", source.model_dump(), status_code=201)
    except SourceError as exc:
        return error(exc.message, exc.error_code, exc.status_code)


@router.delete("/sources/{source_uid}")
async def source_uninstall(request: Request, source_uid: str) -> Any:
    ctx = _ctx(request)
    in_use = source_uid in ctx.digest_service.active_sources() or ctx.db.run_is_active_for_source(source_uid)
    try:
        ctx.source_manager.uninstall(source_uid, in_use=in_use)
        return success("Source uninstalled")
    except SourceError as exc:
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
        return error(str(exc), "INVALID_INTERESTS", 400)

    try:
        settings = ctx.state_store.read_settings()
        settings.interests = interests
        ctx.state_store.write_settings(settings)
        return success("Interests updated", {"interests": interests})
    except OSError:
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
    ctx = _ctx(request)

    if payload.unique_id not in {"stub-local", "stub-cloud"}:
        return error("Model is not installed", "LLM_NOT_FOUND", 422)
    if payload.unique_id in ctx.config.unavailable_llm_ids:
        return error("Model is unavailable", "LLM_UNAVAILABLE", 503)

    settings = ctx.state_store.read_settings()
    settings.llm.name = payload.name
    settings.llm.mode = payload.mode
    settings.llm.unique_id = payload.unique_id
    settings.llm.config = payload.config
    ctx.state_store.write_settings(settings)
    return success("LLM configuration updated", settings.llm.model_dump())


@router.post("/digest/run")
async def digest_run(request: Request) -> Any:
    ctx = _ctx(request)
    if not ctx.scheduler.is_running():
        return error("Scheduler is not ready", "DIGEST_BUSY", 503)

    try:
        run_id = await ctx.digest_service.launch_run("manual")
        return success("Digest run accepted", {"run_id": run_id}, status_code=202)
    except DigestBusyError:
        return error("Digest already running", "DIGEST_ALREADY_RUNNING", 409)
    except Exception:
        return error("Failure launching run", "RUN_FAILED", 500)


@router.put("/digest/schedule")
async def digest_schedule(request: Request, payload: ScheduleRequest) -> Any:
    ctx = _ctx(request)
    try:
        _ = parse_rfc3339(payload.schedule)
    except ValueError:
        return error("Invalid schedule; expected RFC3339 with timezone", "INVALID_SCHEDULE", 422)

    if payload.run_every and payload.spacing <= 0:
        return error("Invalid schedule spacing", "INVALID_SCHEDULE", 422)

    try:
        settings = ctx.state_store.read_settings()
        settings.schedule = payload.schedule
        settings.run_every = payload.run_every
        settings.spacing = payload.spacing
        ctx.state_store.write_settings(settings)
        return success("Schedule updated", settings.model_dump())
    except Exception:
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


@router.get("/items")
async def items_lookup(request: Request, refs: str = Query(default="")) -> Any:
    """Resolve item_ref citations into source/title/url metadata."""
    ctx = _ctx(request)
    try:
        refs_list = _parse_refs_query(refs, ctx.config.items_lookup_max_refs)
    except ValueError as exc:
        return error(str(exc), "INVALID_REFS", 422)
    items = ctx.db.get_items_by_refs(refs_list)
    return success("Items loaded", {"items": items})
