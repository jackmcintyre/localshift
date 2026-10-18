"""Build sensor: which commit this copy of LocalShift was deployed from.

The deploy writes ``build.json`` into the integration folder after copying it
(``scripts/write_build_stamp.sh``). It is not in git: a committed value would
be wrong the moment the next commit landed. The file is read once, off the
event loop, when the sensor is added; it can only change with a new deploy,
and a new deploy needs a restart or reload to load anyway.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.entity import EntityCategory

from .base import LocalShiftSensorBase

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from ..coordinator import LocalShiftCoordinator

BUILD_FILE = Path(__file__).resolve().parent.parent / "build.json"
UNKNOWN = "unknown"
SHORT_SHA_LENGTH = 7

_SHA_PATTERN = re.compile(r"[0-9a-f]{7,64}")


@dataclass(frozen=True)
class BuildInfo:
    """What the deploy recorded. Defaults describe a copy with no stamp."""

    commit: str = UNKNOWN
    build_time: str | None = None
    dirty: bool | None = None

    @property
    def short_commit(self) -> str:
        """Abbreviated commit, as git and GitHub show it."""
        if self.commit == UNKNOWN:
            return UNKNOWN
        return self.commit[:SHORT_SHA_LENGTH]


def read_build_info(path: Path = BUILD_FILE) -> BuildInfo:
    """Read the deploy's build stamp. Blocking: call it in the executor.

    A missing, unreadable or malformed file reads as ``BuildInfo()``, i.e.
    commit "unknown": a copy that was not deployed through a stamping deploy
    still loads.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return BuildInfo()
    if not isinstance(raw, dict):
        return BuildInfo()
    commit = raw.get("commit")
    if not isinstance(commit, str) or not _SHA_PATTERN.fullmatch(commit.lower()):
        return BuildInfo()
    build_time = raw.get("build_time")
    dirty = raw.get("dirty")
    return BuildInfo(
        commit=commit.lower(),
        build_time=build_time if isinstance(build_time, str) else None,
        dirty=dirty if isinstance(dirty, bool) else None,
    )


class BuildSensor(LocalShiftSensorBase):
    """State: short commit (or "unknown"). Attributes: commit, build_time, dirty."""

    _attr_unique_id = "localshift_build"
    _attr_name = "Build"
    _attr_icon = "mdi:source-commit"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
        entry: ConfigEntry,
        build_file: Path = BUILD_FILE,
    ) -> None:
        super().__init__(coordinator, entry)
        self._build_file = build_file
        self._build = BuildInfo()
        self._attr_native_value = self._build.short_commit

    async def async_added_to_hass(self) -> None:
        # No coordinator listener: the stamp does not change while loaded.
        self._build = await self.hass.async_add_executor_job(
            read_build_info, self._build_file
        )
        self._attr_native_value = self._build.short_commit

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "commit": self._build.commit,
            "build_time": self._build.build_time,
            "dirty": self._build.dirty,
        }
