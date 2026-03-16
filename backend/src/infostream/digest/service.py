from __future__ import annotations

"""Digest orchestration service.

This service enforces the single-run invariant, executes source ingestion,
builds/validates digest output, and persists run/digest state in SQLite.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from infostream.core.config import BackendConfig
from infostream.core.hashing import canonicalize_url, compute_item_ref, content_hash
from infostream.core.state import StateStore
from infostream.core.validation import parse_rfc3339
from infostream.db.database import Database
from infostream.digest.provider import SummarizerProvider
from infostream.digest.schema import validate_citations, validate_digest_schema
from infostream.models.schemas import (
    DigestRecord,
    IncomingItem,
    ItemRecord,
    RunRecord,
    RunSourceResult,
    SettingsRecord,
    now_rfc3339,
)
from infostream.sources.executor import SourceExecutionError, SourceExecutor
from infostream.sources.manager import SourceManager


class DigestBusyError(Exception):
    pass


class DigestRunService:
    """Coordinates ingestion + summarization for one run at a time."""

    def __init__(
        self,
        *,
        db: Database,
        state_store: StateStore,
        source_manager: SourceManager,
        source_executor: SourceExecutor,
        summarizer: SummarizerProvider,
        config: BackendConfig,
    ) -> None:
        self._db = db
        self._state_store = state_store
        self._source_manager = source_manager
        self._source_executor = source_executor
        self._summarizer = summarizer
        self._config = config

        self._guard = asyncio.Lock()
        self._active_run_id: str | None = None
        self._active_task: asyncio.Task[None] | None = None
        self._active_sources: set[str] = set()

    def is_running(self) -> bool:
        return self._active_task is not None and not self._active_task.done()

    def active_sources(self) -> set[str]:
        return set(self._active_sources)

    # Start a run, ensure validity
    async def launch_run(self, trigger: str) -> str:
        async with self._guard:
            if self.is_running():
                raise DigestBusyError("digest already running")

            run_id = str(uuid.uuid4())
            run = RunRecord( # Add to QUEUE for scheduling
                run_id=run_id,
                trigger="scheduled" if trigger == "scheduled" else "manual",
                status="queued",
                started_at=now_rfc3339(),
            )
            self._db.create_run(run)
            task = asyncio.create_task(self._execute_run(run_id, run.trigger))
            self._active_run_id = run_id
            self._active_task = task
            task.add_done_callback(lambda _: self._clear_active(run_id))
            return run_id

    # Clear active task/source state when run finishes
    def _clear_active(self, run_id: str) -> None:
        if self._active_run_id == run_id:
            self._active_run_id = None
            self._active_task = None
            self._active_sources.clear()

    # Run digest engine start to finish
    # Mark running, execute all sources, store Items+cursors+results, build
    # digest, persist digest, finalize run status, recompute schedule
    async def _execute_run(self, run_id: str, trigger: str) -> None:
        try:
            self._db.update_run(
                run_id,
                status="running",
                finished_at=None,
                digest_id=None,
                error_code=None,
                error_message=None,
                raw_llm_output=None,
            )

            settings = self._state_store.read_settings()
            enabled_sources = self._source_manager.list_enabled()

            # Execute each source
            for source in enabled_sources:
                self._active_sources.add(source.uid)
                cursor = self._db.get_source_cursor(source.uid)
                try:
                    result = await self._source_executor.execute(source, cursor)
                    ingested_count = 0
                    for incoming in result.items: # Canonicalize into Item
                        canonical = self._to_canonical_item(incoming, source.uid)
                        _inserted, item = self._db.upsert_item(canonical, seen_at=now_rfc3339())
                        self._db.add_run_item(run_id, item.item_ref)
                        ingested_count += 1

                    # Set next cursor so source knows where it left off
                    self._db.set_source_cursor(source.uid, result.next_cursor, now_rfc3339())
                    source_result = RunSourceResult(
                        source_uid=source.uid,
                        status="succeeded",
                        duration_ms=result.duration_ms,
                        items_ingested_count=ingested_count,
                        stderr_tail=result.stderr_tail,
                    )
                # If it errors
                except SourceExecutionError as exc:
                    source_result = RunSourceResult(
                        source_uid=source.uid,
                        status="failed",
                        duration_ms=exc.duration_ms,
                        items_ingested_count=0,
                        stderr_tail=exc.stderr_tail,
                    )
                finally:
                    self._active_sources.discard(source.uid)

                self._db.add_run_source_result(run_id, source_result)

            # Group and store digest
            run_items = self._db.get_items_for_run(run_id)
            digest_payload, raw_output, digest_error = self._build_digest(run_items, settings)
            digest_id = str(uuid.uuid4())
            digest = DigestRecord(
                digest_id=digest_id,
                run_id=run_id,
                version=str(digest_payload.get("version", "0.1.0")),
                created_at=now_rfc3339(),
                output_json=digest_payload,
            )
            self._db.create_digest(digest)

            # Finalize run status and update the DB
            finished_at = now_rfc3339()
            self._db.update_run(
                run_id,
                status="succeeded",
                finished_at=finished_at,
                digest_id=digest_id,
                error_code=digest_error[0] if digest_error else None,
                error_message=digest_error[1] if digest_error else None,
                raw_llm_output=raw_output if digest_error else None,
            )

            self._recompute_schedule(settings, trigger, finished_at)
        except Exception as exc: # If run fails
            self._db.update_run(
                run_id,
                status="failed",
                finished_at=now_rfc3339(),
                digest_id=None,
                error_code="RUN_FAILED",
                error_message=str(exc),
                raw_llm_output=None,
            )

    def _build_digest(
        self,
        items: list[ItemRecord],
        settings: SettingsRecord,
    ) -> tuple[dict[str, Any], str, tuple[str, str] | None]:
        """Build digest payload.

        Flow:
        1. Primary summarize + validate.
        2. One repair attempt + validate.
        3. Deterministic fallback generation + validate.

        The run is allowed to succeed when fallback validates. Only fallback
        generation/validation failure escalates into a failed run.
        """
        raw_output = self._summarizer.summarize(items, settings.interests, settings.llm)
        payload, errors = self._parse_and_validate(raw_output, items)
        if not errors and payload is not None:
            return payload, raw_output, None

        # One repair attempt after schema/citation failure.
        repair_raw = self._summarizer.repair(
            raw_output,
            errors,
            items,
            settings.interests,
            settings.llm,
        )
        repaired_payload, repaired_errors = self._parse_and_validate(repair_raw, items)
        if not repaired_errors and repaired_payload is not None:
            return repaired_payload, repair_raw, None

        # Final fallback path; this must also validate before we accept it.
        fallback = self._fallback_digest(items, settings)
        fallback_schema_errors = validate_digest_schema(fallback)
        fallback_citation_errors = validate_citations(
            fallback,
            {item.item_ref for item in items},
        )
        fallback_errors = fallback_schema_errors + fallback_citation_errors
        if fallback_errors:
            raise RuntimeError(f"fallback digest validation failed: {'; '.join(fallback_errors)}")

        joined_errors = "; ".join(repaired_errors if repaired_errors else errors)
        return fallback, raw_output, ("LLM_FALLBACK_USED", joined_errors)

    def _parse_and_validate(
        self,
        raw_output: str,
        items: list[ItemRecord],
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """Parse raw JSON and apply schema + citation validation."""
        try:
            payload = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            return None, [f"invalid JSON: {exc}"]

        schema_errors = validate_digest_schema(payload)
        if schema_errors:
            return payload, schema_errors

        valid_refs = {item.item_ref for item in items}
        citation_errors = validate_citations(payload, valid_refs)
        if citation_errors:
            return payload, citation_errors

        return payload, []

    def _fallback_digest(self, items: list[ItemRecord], settings: SettingsRecord) -> dict[str, Any]:
        """Create deterministic fallback content from current-run items."""
        interest_names = self._parse_interests(settings.interests)
        refs = [item.item_ref for item in items[:5]]
        interest_payload: dict[str, list[dict[str, Any]]] = {}
        for name in interest_names:
            bullets: list[dict[str, Any]] = []
            if items: # If items exist in LLM output
                for item in items[:3]:
                    text = item.title or item.content_text or item.url or item.item_ref
                    bullets.append(
                        {
                            "text": f"Fallback summary: {text[:220]}",
                            "source_refs": [item.item_ref],
                            "confidence": "low",
                        }
                    )
            else: # If empty
                bullets.append(
                    {
                        "text": "No items were ingested for this run.",
                        "source_refs": refs,
                        "confidence": "low",
                    }
                )
            interest_payload[name] = bullets

        return {
            "version": "0.1.0",
            "new_communications": {},
            "important_dates_events": [],
            "interests": interest_payload,
            "links": [],
            "entities": [],
            "relations": [],
            "extraneous": "Digest generated via fallback path.",
        }

    def _parse_interests(self, interests_text: str) -> list[str]:
        """Convert free-form interests text to normalized interest tokens."""
        names = [part.strip() for part in interests_text.replace("\n", ",").split(",")]
        clean = [name for name in names if name]
        return clean if clean else ["general"]

    # Normalize incoming item from digest into an Item for the DB
    def _to_canonical_item(self, incoming: IncomingItem, source_uid: str) -> ItemRecord:
        text = incoming.content_text or ""
        c_hash = content_hash(text)
        normalized_url = canonicalize_url(incoming.url) if incoming.url else None
        item_ref = compute_item_ref(
            source_uid=source_uid,
            external_id=incoming.external_id,
            url=normalized_url,
            content_hash_value=c_hash,
        )
        return ItemRecord(
            item_ref=item_ref,
            source_uid=source_uid,
            external_id=incoming.external_id,
            url=normalized_url,
            title=incoming.title,
            created_at=incoming.created_at,
            ingested_at=now_rfc3339(),
            content_text=incoming.content_text,
            content_hash=c_hash,
            media_refs=incoming.media_refs,
            tags=incoming.tags,
        )

    # After a run, determine the next run time/schedule
    def _recompute_schedule(self, settings: SettingsRecord, trigger: str, finished_at: str) -> None:
        if settings.run_every and settings.spacing > 0:
            finished = parse_rfc3339(finished_at)
            next_run = (finished + timedelta(seconds=settings.spacing)).astimezone(UTC)
            settings.schedule = next_run.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            self._state_store.write_settings(settings)
            return

        if trigger == "scheduled" and not settings.run_every:
            settings.schedule = self._config.one_shot_schedule_sentinel
            self._state_store.write_settings(settings)
