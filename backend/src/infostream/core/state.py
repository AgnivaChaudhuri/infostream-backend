from __future__ import annotations

from infostream.core.files import atomic_write_json, read_json
from infostream.core.paths import BackendPaths
from infostream.models.schemas import SettingsRecord


class StateStore:
    def __init__(self, paths: BackendPaths) -> None:
        self._paths = paths

    def read_settings(self) -> SettingsRecord:
        data = read_json(self._paths.settings)
        return SettingsRecord.model_validate(data)

    def write_settings(self, settings: SettingsRecord) -> None:
        atomic_write_json(self._paths.settings, settings.model_dump())
