"""Source package lifecycle management.

This module validates, installs, enables, disables, and uninstalls source
extensions defined by the Infostream source package contract.
"""

from __future__ import annotations

import json
import platform
import shutil
import uuid
from pathlib import Path

from pydantic import ValidationError

from infostream.core.config import BackendConfig
from infostream.core.files import atomic_write_json, read_json
from infostream.core.logging import get_logger
from infostream.core.paths import BackendPaths
from infostream.core.validation import is_semver
from infostream.models.schemas import InstalledSourcesRecord, SourceInfo, SourceRecord


class SourceError(Exception):
    """Uniform source-lifecycle error with API-friendly metadata."""

    def __init__(self, message: str, error_code: str, status_code: int) -> None:
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        super().__init__(message)


class SourceManager:
    """Persistent source registry and install-time package validator."""

    def __init__(self, paths: BackendPaths, config: BackendConfig) -> None:
        self._paths = paths
        self._config = config
        self._logger = get_logger("sources.manager")

    def _load(self) -> InstalledSourcesRecord:
        """Load installed source registry from disk."""
        data = read_json(self._paths.installed_sources)
        record = InstalledSourcesRecord.model_validate(data)
        self._logger.debug(
            "source_registry_loaded",
            message="loaded installed source registry",
            count=len(record.sources),
        )
        return record

    def _save(self, record: InstalledSourcesRecord) -> None:
        """Persist installed source registry atomically."""
        atomic_write_json(self._paths.installed_sources, record.model_dump())
        self._logger.debug(
            "source_registry_saved",
            message="saved installed source registry",
            count=len(record.sources),
        )

    def list_installed(self) -> list[SourceRecord]:
        """Return all installed sources."""
        record = self._load()
        return record.sources

    def list_enabled(self) -> list[SourceRecord]:
        """Return enabled sources only."""
        return [source for source in self.list_installed() if source.enabled]

    def get_source(self, source_uid: str) -> SourceRecord | None:
        """Look up a source by backend-assigned source UID."""
        for source in self.list_installed():
            if source.uid == source_uid:
                return source
        return None

    def enable(self, source_uid: str) -> SourceRecord:
        """Enable a source if it exists and supports the current platform."""
        self._logger.info(
            "source_enable_requested", message="enable source requested", source_uid=source_uid
        )
        record = self._load()
        source = next((item for item in record.sources if item.uid == source_uid), None)
        if source is None:
            raise SourceError("Source not found", "SOURCE_NOT_FOUND", 404)

        if source.enabled:
            raise SourceError("Source already enabled", "SOURCE_ALREADY_ENABLED", 409)

        host_platform = platform.system().lower()
        if source.platforms and host_platform not in {p.lower() for p in source.platforms}:
            raise SourceError(
                "Source does not support this platform",
                "PLATFORM_NOT_SUPPORTED",
                403,
            )

        source.enabled = True
        self._save(record)
        self._logger.info("source_enabled", message="source enabled", source_uid=source_uid)
        return source

    def disable(self, source_uid: str) -> SourceRecord:
        """Disable an enabled source."""
        self._logger.info(
            "source_disable_requested", message="disable source requested", source_uid=source_uid
        )
        record = self._load()
        source = next((item for item in record.sources if item.uid == source_uid), None)
        if source is None:
            raise SourceError("Source not found", "SOURCE_NOT_FOUND", 404)

        if not source.enabled:
            raise SourceError("Source already disabled", "SOURCE_ALREADY_DISABLED", 409)

        source.enabled = False
        self._save(record)
        self._logger.info("source_disabled", message="source disabled", source_uid=source_uid)
        return source

    def install(self, source_path: Path) -> SourceRecord:
        """Validate and install a source package into the internal source root."""
        self._logger.info(
            "source_install_requested",
            message="source install requested",
            source_path=str(source_path),
        )
        if not source_path.exists() or not source_path.is_dir():
            raise SourceError("Source directory is invalid", "INVALID_SOURCE_PACKAGE", 400)

        src_info_path = source_path / "src_info.json"
        src_exec_path = source_path / "src_exec"
        if not src_info_path.exists() or not src_exec_path.exists():
            raise SourceError(
                "Source package missing required files",
                "INVALID_SOURCE_STRUCTURE",
                422,
            )

        # Parse source metadata and validate schema-level requirements.
        try:
            info_data = json.loads(src_info_path.read_text(encoding="utf-8"))
            info = self._parse_source_info(info_data)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise SourceError(
                f"Source metadata is invalid: {exc}",
                "INVALID_SOURCE_STRUCTURE",
                422,
            ) from exc

        # Enforce semver and interface compatibility requirements.
        if not is_semver(info.version) or not is_semver(info.interface_version):
            raise SourceError(
                "Source version fields must be SemVer strings",
                "INVALID_SOURCE_STRUCTURE",
                422,
            )
        if not self._is_compatible_interface_version(info.interface_version):
            raise SourceError(
                "Source interface version is incompatible with this backend",
                "INVALID_SOURCE_STRUCTURE",
                422,
            )

        record = self._load()
        if any(
            source.name == info.name and source.developer == info.developer
            for source in record.sources
        ):
            raise SourceError("Source is already installed", "SOURCE_ALREADY_INSTALLED", 409)

        source_uid = f"src-{uuid.uuid4().hex[:12]}"
        install_dir = self._paths.sources_dir / source_uid
        try:
            shutil.copytree(source_path, install_dir)
        except OSError as exc:  # If something fails due to system constraints
            raise SourceError(f"Source install failed: {exc}", "INSTALL_FAILED", 500) from exc

        source = SourceRecord(
            uid=source_uid,
            name=info.name,
            developer=info.developer,
            version=info.version,
            description=info.description,
            platforms=info.platforms,
            enabled=False,
            exec_path=str(install_dir),
            interface_version=info.interface_version,
        )
        record.sources.append(source)
        self._save(record)
        self._logger.info(
            "source_install_completed",
            message="source install completed",
            source_uid=source_uid,
            source_name=source.name,
            source_version=source.version,
        )
        return source

    def uninstall(self, source_uid: str, *, in_use: bool = False) -> None:
        """Remove a source package from disk and source registry."""
        self._logger.info(
            "source_uninstall_requested",
            message="source uninstall requested",
            source_uid=source_uid,
            in_use=in_use,
        )
        if in_use:
            raise SourceError("Source is currently in use", "SOURCE_IN_USE", 409)

        record = self._load()
        index = next(
            (idx for idx, item in enumerate(record.sources) if item.uid == source_uid),
            None,
        )
        if index is None:
            raise SourceError("Source not found", "SOURCE_NOT_FOUND", 404)

        source = record.sources[index]
        try:
            shutil.rmtree(source.exec_path)
        except OSError as exc:
            raise SourceError(
                f"Failed to uninstall source: {exc}",
                "UNINSTALL_FAILED",
                500,
            ) from exc

        del record.sources[index]
        self._save(record)
        self._logger.info("source_uninstalled", message="source uninstalled", source_uid=source_uid)

    def _parse_source_info(self, info_data: dict[str, object]) -> SourceInfo:
        """Parse source metadata in canonical or snake_case aliases."""
        if "Name" in info_data:
            return SourceInfo.model_validate(info_data)

        normalized = {
            "Name": info_data.get("name"),
            "Description": info_data.get("description"),
            "Version": info_data.get("version"),
            "Interface version": info_data.get("interface_version"),
            "Developer": info_data.get("developer", "unknown"),
            "Platform": info_data.get("platforms", []),
        }
        return SourceInfo.model_validate(normalized)

    def _is_compatible_interface_version(self, source_interface_version: str) -> bool:
        """Apply Option B compatibility: major versions must match."""
        try:
            source_major = int(source_interface_version.split(".", 1)[0])
            backend_major = int(self._config.source_interface_version.split(".", 1)[0])
        except ValueError:
            return False
        return source_major == backend_major
