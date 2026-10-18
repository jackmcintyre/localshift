"""Tests for the build sensor: which commit this copy of LocalShift came from.

The deploy writes ``build.json`` next to the integration's code; the sensor
reads it once when it is added and reports the commit. A missing or damaged
file must read as "unknown" rather than fail setup.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers.entity import EntityCategory

from custom_components.localshift.sensors.build import (
    BUILD_FILE,
    UNKNOWN,
    BuildInfo,
    BuildSensor,
    read_build_info,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
STAMP_SCRIPT = REPO_ROOT / "scripts" / "write_build_stamp.sh"

SHA = "0123456789abcdef0123456789abcdef01234567"


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestReadBuildInfo:
    def test_build_file_lives_in_the_integration_folder(self):
        assert BUILD_FILE.name == "build.json"
        assert BUILD_FILE.parent.name == "localshift"
        assert (BUILD_FILE.parent / "manifest.json").exists()

    def test_missing_file_reads_unknown(self, tmp_path):
        info = read_build_info(tmp_path / "build.json")
        assert info == BuildInfo()
        assert info.commit == UNKNOWN
        assert info.short_commit == UNKNOWN
        assert info.build_time is None
        assert info.dirty is None

    def test_file_present_exposes_the_commit(self, tmp_path):
        path = _write(
            tmp_path / "build.json",
            {"commit": SHA, "build_time": "2026-10-18T01:02:03Z", "dirty": False},
        )
        info = read_build_info(path)
        assert info.commit == SHA
        assert info.short_commit == SHA[:7]
        assert info.build_time == "2026-10-18T01:02:03Z"
        assert info.dirty is False

    @pytest.mark.parametrize(
        "content",
        [
            "not json",
            json.dumps(["a", "list"]),
            json.dumps({"build_time": "2026-10-18T01:02:03Z"}),
            json.dumps({"commit": 12345}),
            json.dumps({"commit": "unknown"}),
            json.dumps({"commit": "not-a-sha!"}),
            json.dumps({"commit": "abc"}),
        ],
    )
    def test_damaged_file_reads_unknown(self, tmp_path, content):
        path = tmp_path / "build.json"
        path.write_text(content, encoding="utf-8")
        assert read_build_info(path) == BuildInfo()

    def test_wrongly_typed_optional_fields_are_dropped(self, tmp_path):
        path = _write(
            tmp_path / "build.json",
            {"commit": SHA, "build_time": 17, "dirty": "yes"},
        )
        info = read_build_info(path)
        assert info.commit == SHA
        assert info.build_time is None
        assert info.dirty is None

    def test_uppercase_sha_is_normalised(self, tmp_path):
        path = _write(tmp_path / "build.json", {"commit": SHA.upper()})
        assert read_build_info(path).commit == SHA


def _hass_running_executor_jobs_inline() -> MagicMock:
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *args: func(*args))
    return hass


class TestBuildSensor:
    def _sensor(self, path: Path) -> BuildSensor:
        sensor = BuildSensor(MagicMock(), MagicMock(), build_file=path)
        sensor.hass = _hass_running_executor_jobs_inline()
        return sensor

    def test_identity(self, tmp_path):
        sensor = self._sensor(tmp_path / "build.json")
        assert sensor._attr_unique_id == "localshift_build"
        assert sensor._attr_name == "Build"
        assert sensor.entity_category is EntityCategory.DIAGNOSTIC

    def test_defaults_to_the_integration_build_file(self):
        sensor = BuildSensor(MagicMock(), MagicMock())
        assert sensor._build_file == BUILD_FILE

    @pytest.mark.asyncio
    async def test_missing_file_reports_unknown(self, tmp_path):
        sensor = self._sensor(tmp_path / "build.json")
        await sensor.async_added_to_hass()
        assert sensor.native_value == UNKNOWN
        assert sensor.extra_state_attributes == {
            "commit": UNKNOWN,
            "build_time": None,
            "dirty": None,
        }

    @pytest.mark.asyncio
    async def test_file_present_reports_the_sha(self, tmp_path):
        path = _write(
            tmp_path / "build.json",
            {"commit": SHA, "build_time": "2026-10-18T01:02:03Z", "dirty": True},
        )
        sensor = self._sensor(path)
        await sensor.async_added_to_hass()
        assert sensor.native_value == SHA[:7]
        assert sensor.extra_state_attributes == {
            "commit": SHA,
            "build_time": "2026-10-18T01:02:03Z",
            "dirty": True,
        }

    @pytest.mark.asyncio
    async def test_reads_off_the_event_loop_and_ignores_coordinator(self, tmp_path):
        coordinator = MagicMock()
        sensor = BuildSensor(coordinator, MagicMock(), build_file=tmp_path / "x")
        sensor.hass = _hass_running_executor_jobs_inline()
        await sensor.async_added_to_hass()
        sensor.hass.async_add_executor_job.assert_awaited_once_with(
            read_build_info, tmp_path / "x"
        )
        coordinator.async_add_listener.assert_not_called()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    component = repo / "custom_components" / "localshift"
    component.mkdir(parents=True)
    (component / "manifest.json").write_text("{}", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "custom_components")
    _git(
        repo,
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=t",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-q",
        "-m",
        "init",
    )
    return repo


def _stamp(repo: Path, dest: Path) -> BuildInfo:
    subprocess.run(["bash", str(STAMP_SCRIPT), str(repo), str(dest)], check=True)
    return read_build_info(dest / "build.json")


class TestWriteBuildStamp:
    """The deploy's half: scripts/write_build_stamp.sh writes what the sensor reads."""

    def test_clean_checkout_stamps_head(self, repo, tmp_path):
        dest = tmp_path / "dest"
        dest.mkdir()
        info = _stamp(repo, dest)
        assert info.commit == _git(repo, "rev-parse", "HEAD")
        assert info.dirty is False
        assert info.build_time is not None and info.build_time.endswith("Z")

    def test_uncommitted_change_is_marked_dirty(self, repo, tmp_path):
        manifest = repo / "custom_components" / "localshift" / "manifest.json"
        manifest.write_text('{"changed": true}', encoding="utf-8")
        dest = tmp_path / "dest"
        dest.mkdir()
        assert _stamp(repo, dest).dirty is True

    def test_not_a_checkout_stamps_unknown(self, tmp_path):
        dest = tmp_path / "dest"
        dest.mkdir()
        plain = tmp_path / "plain"
        plain.mkdir()
        assert _stamp(plain, dest) == BuildInfo()
