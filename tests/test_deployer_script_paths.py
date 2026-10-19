"""The scripts the HA host's pull deployer calls take their paths from it.

The deployer runs deploy_check.py (--preflight inside the HA container,
--before/--after on the host), ha_entity_dump.py for the dumps, and
write_build_stamp.sh after every ship. None of them may fall back to a path
that only existed on the old Mac deploy workstation: the component folder comes
from --component-dir, the HA connection from HA_URL/HA_TOKEN (or --url/--token),
and the stamp's folders from its arguments.
"""

from __future__ import annotations

import json
import re
import types
from datetime import datetime
from pathlib import Path

import pytest

from scripts import deploy_check as dc
from scripts import ha_entity_dump

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOYER_SCRIPTS = [
    REPO_ROOT / "scripts" / "deploy_check.py",
    REPO_ROOT / "scripts" / "ha_entity_dump.py",
    REPO_ROOT / "scripts" / "write_build_stamp.sh",
    REPO_ROOT / "deploy.sh",
]
MAC_PATH = re.compile(r"/Volumes/|/Users/|~/Library|/private/var/")


@pytest.mark.parametrize("script", DEPLOYER_SCRIPTS, ids=lambda p: p.name)
def test_no_mac_workstation_paths(script):
    hits = [
        f"{script.name}:{n}: {line.strip()}"
        for n, line in enumerate(script.read_text().splitlines(), 1)
        if MAC_PATH.search(line)
    ]
    assert not hits, "Mac-only path in a script the deployer runs:\n" + "\n".join(hits)


def _dump_file(path: Path) -> Path:
    ents = {
        dc.HEALTH: {"state": "68/68", "attributes": {}},
        dc.AUTOMATION: {"state": "on", "attributes": {}},
    }
    path.write_text(
        json.dumps({
            "meta": {},
            "groups": {"g": [{"entity_id": eid, **e} for eid, e in ents.items()]},
        })
    )
    return path


def _log_file(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ls = "[custom_components.localshift.engine.optimizer_facade]"
    path.write_text(
        "\n".join(
            f"{stamp}.000 INFO (MainThread) {ls} Pre-charge decision: x"
            for _ in range(2)
        )
    )
    return path


@pytest.fixture
def live_component(tmp_path):
    """A component folder under an HA config dir, with no compiled files."""
    config = tmp_path / "ha-config"
    comp = config / "custom_components" / "localshift"
    comp.mkdir(parents=True)
    (comp / "sensor.py").write_text("x = 1\n")
    return config, comp


def test_component_dir_is_not_taken_from_the_environment(
    tmp_path, live_component, monkeypatch, capsys
):
    config, _comp = live_component
    monkeypatch.setenv("HA_CONFIG", str(config))
    dump = _dump_file(tmp_path / "d.json")
    log = _log_file(tmp_path / "ha.log")

    dc.main(["--before", str(dump), "--after", str(dump), "--log", str(log)])

    assert "compiled files" not in capsys.readouterr().out


def test_component_dir_is_taken_from_the_argument(tmp_path, live_component, capsys):
    _config, comp = live_component
    dump = _dump_file(tmp_path / "d.json")
    log = _log_file(tmp_path / "ha.log")

    rc = dc.main([
        "--before",
        str(dump),
        "--after",
        str(dump),
        "--log",
        str(log),
        "--component-dir",
        str(comp),
    ])

    out = capsys.readouterr().out
    assert "compiled files" in out
    assert str(comp) in out  # judged the folder it was given
    assert rc == 1  # no compiled files under it


def test_ha_connection_comes_from_the_environment(tmp_path, monkeypatch):
    # A home with no workstation config: only the environment can answer.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("HA_URL", "http://ha.invalid:8123/")
    monkeypatch.setenv("HA_TOKEN", "t0k")
    for var in ("HOMEASSISTANT_URL", "HOMEASSISTANT_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    url, token = ha_entity_dump.resolve_credentials(
        types.SimpleNamespace(url=None, token=None)
    )

    assert (url, token) == ("http://ha.invalid:8123", "t0k")


def test_ha_connection_missing_exits_without_a_default(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for var in ("HA_URL", "HA_TOKEN", "HOMEASSISTANT_URL", "HOMEASSISTANT_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(SystemExit) as exc:
        ha_entity_dump.resolve_credentials(types.SimpleNamespace(url=None, token=None))

    assert "HA_URL/HA_TOKEN" in str(exc.value.code)


def test_deploy_sh_passes_the_flag_when_it_calls_itself():
    # --watch re-runs deploy.sh; without the flag the inner run would refuse.
    calls = [
        line.strip()
        for line in (REPO_ROOT / "deploy.sh").read_text().splitlines()
        if '"$SCRIPT_DIR/deploy.sh"' in line
    ]
    assert calls
    assert all("--i-am-not-the-deployer" in c for c in calls), calls
