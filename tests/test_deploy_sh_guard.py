"""deploy.sh refuses to run unless a human passes --i-am-not-the-deployer.

Deploys are done by the HA host's pull deployer. deploy.sh is kept only for a
human in an emergency, so a bare run (an agent, a habit, an old runbook) must
stop before it reads or writes anything. None of these tests reach HA: the
refusal exits first, and the pass-through runs point HA_CONFIG at a folder
that does not exist, so deploy.sh stops at its own config check.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SH = REPO_ROOT / "deploy.sh"
FLAG = "--i-am-not-the-deployer"


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"HA_LONG_LIVED_TOKEN", "HA_TOKEN", "HA_URL"}
    }
    env["HA_CONFIG"] = str(tmp_path / "no-such-ha-config")
    return subprocess.run(
        ["bash", str(DEPLOY_SH), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    "args",
    [(), ("--dry-run",), ("--status",), ("--reserve",), ("--restart",)],
)
def test_refuses_without_the_flag(tmp_path, args):
    result = _run(tmp_path, *args)

    assert result.returncode == 2
    assert "pull deployer" in result.stderr
    assert FLAG in result.stderr
    # Refused before any of the script's own work started.
    assert "HA config directory not found" not in result.stdout + result.stderr
    assert not (tmp_path / "no-such-ha-config").exists()


def test_flag_passes_through_to_the_normal_checks(tmp_path):
    result = _run(tmp_path, FLAG, "--dry-run")

    assert result.returncode == 1
    assert "HA config directory not found" in result.stdout
    assert "pull deployer" not in result.stderr


def test_flag_is_accepted_in_any_position(tmp_path):
    result = _run(tmp_path, "--dry-run", FLAG)

    assert "HA config directory not found" in result.stdout


def test_flag_is_stripped_before_argument_parsing(tmp_path):
    # The other arguments still reach the parser unchanged.
    result = _run(tmp_path, FLAG, "--bogus")

    assert result.returncode == 1
    assert "Unknown argument: --bogus" in result.stdout
    assert FLAG not in result.stdout
