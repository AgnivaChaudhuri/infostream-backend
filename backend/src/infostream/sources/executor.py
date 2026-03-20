"""Source execution runtime.

This module executes source binaries, validates source output limits, ingests
media into local cache, and applies cache-retention policy.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from infostream.core.config import BackendConfig
from infostream.core.hashing import sha256_hex
from infostream.core.logging import get_logger
from infostream.models.schemas import IncomingItem, SourceOutput, SourceRecord


@dataclass
class SourceExecutionResult:
    """Container for successful source execution results."""

    source_uid: str
    status: str
    duration_ms: int
    stderr_tail: str
    items: list[IncomingItem]
    next_cursor: str | None


class SourceExecutionError(Exception):
    """Execution/runtime validation error for one source run."""

    def __init__(self, message: str, stderr_tail: str, duration_ms: int) -> None:
        self.message = message
        self.stderr_tail = stderr_tail
        self.duration_ms = duration_ms
        super().__init__(message)


class SourceExecutor:
    """Execute sources and transform source artifacts into canonical ingestion data."""

    def __init__(self, config: BackendConfig, media_root: Path) -> None:
        self._config = config
        self._media_root = media_root
        self._logger = get_logger("sources.executor")

    async def execute(self, source: SourceRecord, cursor: str | None) -> SourceExecutionResult:
        """Run one source executable and return parsed/ingested result."""
        self._logger.info(
            "source_execution_started",
            message="source execution started",
            source_uid=source.uid,
            cursor_present=cursor is not None,
        )
        source_dir = Path(source.exec_path)
        exec_path = source_dir / "src_exec"
        if not exec_path.exists():
            raise SourceExecutionError("src_exec not found", "src_exec missing", 0)

        argv = [str(exec_path)]
        if cursor:
            argv.extend(["--cursor", cursor])

        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=source_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._logger.debug(
            "source_process_spawned",
            message="source process spawned",
            source_uid=source.uid,
            pid=proc.pid,
        )

        stdout_bytes: bytes = b""
        stderr_bytes: bytes = b""
        timed_out = False

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._config.graceful_source_timeout_seconds,
            )
        except TimeoutError:
            timed_out = True
            self._logger.warning(
                "source_process_timeout_graceful",
                message="source process exceeded graceful timeout",
                source_uid=source.uid,
                timeout_seconds=self._config.graceful_source_timeout_seconds,
            )
            proc.terminate()
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self._config.hard_source_timeout_seconds
                    - self._config.graceful_source_timeout_seconds,
                )
            except TimeoutError:
                self._logger.error(
                    "source_process_timeout_hard",
                    message="source process exceeded hard timeout and will be killed",
                    source_uid=source.uid,
                    timeout_seconds=self._config.hard_source_timeout_seconds,
                )
                self._kill_process_tree(proc.pid)
                stdout_bytes, stderr_bytes = await proc.communicate()

        duration_ms = int((time.monotonic() - started) * 1000)
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        stderr_tail = stderr_text[-2000:]

        if timed_out:
            raise SourceExecutionError("source execution timed out", stderr_tail, duration_ms)

        if proc.returncode != 0:
            message = f"source exited with non-zero status {proc.returncode}"
            raise SourceExecutionError(message, stderr_tail, duration_ms)

        self._validate_output_size(source_dir)
        output = self._load_source_output(source_dir)
        self._ingest_media(source_dir, output)

        self._logger.info(
            "source_execution_completed",
            message="source execution completed",
            source_uid=source.uid,
            duration_ms=duration_ms,
            item_count=len(output.items),
            next_cursor_present=output.next_cursor is not None,
        )

        return SourceExecutionResult(
            source_uid=source.uid,
            status="succeeded",
            duration_ms=duration_ms,
            stderr_tail=stderr_tail,
            items=output.items,
            next_cursor=output.next_cursor,
        )

    def _validate_output_size(self, source_dir: Path) -> None:
        """Enforce per-source output caps from spec."""
        output_json = source_dir / "src_output.json"
        if not output_json.exists() or not output_json.is_file():
            raise SourceExecutionError("src_output.json missing", "src_output missing", 0)
        if output_json.stat().st_size > self._config.source_json_max_bytes:
            raise SourceExecutionError("src_output.json exceeds size cap", "json exceeds cap", 0)
        self._logger.debug(
            "source_output_json_size_valid",
            message="validated source output json size",
            source_dir=str(source_dir),
            json_size_bytes=output_json.stat().st_size,
        )

        media_dir = source_dir / "src_media"
        if media_dir.exists() and media_dir.is_dir():
            total = 0
            for path in media_dir.rglob("*"):
                if path.is_file():
                    total += path.stat().st_size
            if total > self._config.source_media_max_bytes:
                raise SourceExecutionError("src_media exceeds size cap", "media exceeds cap", 0)
            self._logger.debug(
                "source_media_size_valid",
                message="validated source media size",
                source_dir=str(source_dir),
                media_size_bytes=total,
            )

    def _load_source_output(self, source_dir: Path) -> SourceOutput:
        """Load and schema-validate src_output.json."""
        try:
            raw = (source_dir / "src_output.json").read_text(encoding="utf-8")
            output = SourceOutput.model_validate_json(raw)
            self._logger.debug(
                "source_output_parsed",
                message="parsed source output file",
                source_dir=str(source_dir),
                item_count=len(output.items),
            )
            return output
        except OSError as exc:
            raise SourceExecutionError(f"cannot read src_output.json: {exc}", str(exc), 0) from exc
        except ValidationError as exc:
            raise SourceExecutionError(f"invalid src_output.json: {exc}", str(exc), 0) from exc

    def _ingest_media(self, source_dir: Path, output: SourceOutput) -> None:
        """Copy source media to lt_media and map item media refs by content hash."""
        media_dir = source_dir / "src_media"
        media_lookup: dict[str, str] = {}

        if media_dir.exists() and media_dir.is_dir():
            for media_path in media_dir.rglob("*"):
                if not media_path.is_file():
                    continue
                content = media_path.read_bytes()
                media_hash = sha256_hex(content)
                ext = media_path.suffix
                out_path = self._media_root / f"{media_hash}{ext}"
                if not out_path.exists():
                    out_path.write_bytes(content)
                media_lookup[media_path.name] = media_hash
            self._enforce_media_cache_limit()
            self._logger.debug(
                "source_media_ingested",
                message="ingested source media files",
                source_dir=str(source_dir),
                media_file_count=len(media_lookup),
            )

        for item in output.items:
            refs: list[str] = list(item.media_refs)
            for name in item.media_files:
                resolved_media_hash = media_lookup.get(name)
                if resolved_media_hash and resolved_media_hash not in refs:
                    refs.append(resolved_media_hash)
            item.media_refs = refs

    def _kill_process_tree(self, pid: int | None) -> None:
        """Force-kill source process after hard timeout."""
        if pid is None:
            return
        try:
            if os.name == "posix":
                os.kill(pid, signal.SIGKILL)
                self._logger.warning(
                    "source_process_killed", message="source process killed", pid=pid
                )
        except OSError:
            return

    def _enforce_media_cache_limit(self) -> None:
        """Retain media cache under configured byte limit by deleting oldest files."""
        if not self._media_root.exists():
            return

        files: list[Path] = [path for path in self._media_root.glob("*") if path.is_file()]
        total_bytes = sum(path.stat().st_size for path in files)
        if total_bytes <= self._config.media_cache_max_bytes:
            return

        # Oldest files are evicted first to preserve more recently ingested media.
        files.sort(key=lambda path: path.stat().st_mtime)
        evicted_count = 0
        for path in files:
            size = path.stat().st_size
            try:
                path.unlink()
            except OSError:
                continue
            total_bytes -= size
            evicted_count += 1
            if total_bytes <= self._config.media_cache_max_bytes:
                break
        self._logger.warning(
            "media_cache_eviction",
            message="media cache retention evicted old files",
            evicted_count=evicted_count,
            remaining_bytes=total_bytes,
            max_bytes=self._config.media_cache_max_bytes,
        )
