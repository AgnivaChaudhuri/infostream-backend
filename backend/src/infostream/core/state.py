from __future__ import annotations

from infostream.core.files import atomic_write_json, read_json
from infostream.core.logging import get_logger
from infostream.core.paths import BackendPaths
from infostream.models.schemas import SettingsRecord

# some abstractions for reading/writing settings and seeing
# the application's current state


class StateStore:
    def __init__(self, paths: BackendPaths) -> None:
        self._paths = paths
        self._logger = get_logger("core.state")

    def read_settings(self) -> SettingsRecord:
        data = read_json(self._paths.settings)
        settings = SettingsRecord.model_validate(data)
        self._logger.debug("settings_loaded", message="loaded settings")
        return settings

    def write_settings(self, settings: SettingsRecord) -> None:
        atomic_write_json(self._paths.settings, settings.model_dump())
        self._logger.info("settings_updated", message="settings updated")
