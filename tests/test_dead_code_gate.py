"""The dead-code ratchet must be able to fail, in both directions (issue #986).

Two halves:

* The gate script against a tiny synthetic package, so each way it can fail
  is exercised on purpose: new dead code, a baselined symbol deleted without
  shrinking the baseline, a whitelisted method whose dispatch-table row was
  removed, a stale whitelist entry.
* The real repository: the gate passes, every finding is in exactly one of
  the whitelist and the baseline, and the whitelist's claims about Home
  Assistant are checked against Home Assistant itself.

If a test here fails after you changed production code, read the message the
gate prints: it says which of the two files to edit. Do not "fix" it by adding
the symbol to the baseline.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import textwrap
import tomllib
from pathlib import Path

import pytest

from scripts import dead_code_gate as gate

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Synthetic package
# ---------------------------------------------------------------------------

MACHINE = '''\
"""A string dispatch table, like state/mode_configs.py."""

TABLE = {"a": "_build_a"}


class Machine:
    def run(self, key):
        return getattr(self, TABLE[key])()

    def _build_a(self):
        return 1

    def _dead_method(self):
        return 2


class Other:
    """Same method name, no dispatch table: genuinely unused."""

    def _build_a(self):
        return 3
'''

ENTITY = """\
from homeassistant.helpers.entity import Entity


class Base(Entity):
    pass


class Thing(Base):
    def __init__(self):
        self._attr_name = "thing"

    @property
    def icon(self):
        return "mdi:thing"


class Plain:
    @property
    def icon(self):
        return "not an entity"
"""

DATA = """\
from dataclasses import dataclass


@dataclass
class Snapshot:
    used: int = 0
    unread: int = 0
    external: int = 0
"""

READER = """\
from .data import Snapshot
from .entity import Plain, Thing
from .machine import Machine, Other


def consume(data: Snapshot):
    return data.used, Machine().run("a"), Other(), Plain(), Thing()


consume(Snapshot())
"""

WHITELIST = '''\
"""Synthetic whitelist."""

# framework: homeassistant.helpers.entity.Entity
# why: HA reads it by name.
_.icon

# site: pkg/machine.py
# evidence: "{name}"
# evidence: getattr(self, TABLE[key])
# why: string table plus getattr.
Machine._build_a

# site: tools/replay.py
# evidence: data.external
# why: read by a script on purpose.
Snapshot.external
'''

PYPROJECT = """\
[tool.vulture]
min_confidence = 60
ignore_names = ["_attr_*"]
"""

EXPECTED_BASELINE = {
    ("method", "machine.py", "Machine._dead_method"),
    ("method", "machine.py", "Other._build_a"),
    ("property", "entity.py", "Plain.icon"),
    ("unread-field", "data.py", "Snapshot.unread"),
}


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _argv(root: Path, *extra: str) -> list[str]:
    return [
        "--root",
        str(root),
        "--package",
        "pkg",
        "--data-module",
        "data.py",
        "--data-class",
        "Snapshot",
        *extra,
    ]


def _baseline(root: Path) -> set[tuple[str, str, str]]:
    text = (root / "dead_code_baseline.txt").read_text(encoding="utf-8")
    return {(f.kind, f.file, f.qualname) for f in gate.parse_baseline(text)}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A clean synthetic project whose baseline has just been created."""
    _write(tmp_path, "pyproject.toml", PYPROJECT)
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/machine.py", MACHINE)
    _write(tmp_path, "pkg/entity.py", ENTITY)
    _write(tmp_path, "pkg/data.py", DATA)
    _write(tmp_path, "pkg/reader.py", READER)
    _write(tmp_path, "tools/replay.py", "print(data.external)\n")
    _write(tmp_path, "vulture_whitelist.py", WHITELIST)
    assert gate.main(_argv(tmp_path, "--update", "--allow-additions")) == 0
    return tmp_path


def test_fixture_is_classified_as_expected(project: Path) -> None:
    """Whitelisted symbols are out; everything else lands in the baseline.

    Also pins the scoping rules: ``Machine._build_a`` is whitelisted and
    ``Other._build_a`` is not (no bare-name blind spot), and the framework
    name ``icon`` only covers a class that derives from homeassistant.
    """
    assert _baseline(project) == EXPECTED_BASELINE
    assert gate.main(_argv(project)) == 0


def test_new_unused_function_fails(project: Path, capsys) -> None:
    path = project / "pkg/machine.py"
    path.write_text(
        path.read_text() + "\n\ndef brand_new_helper():\n    return 0\n",
        encoding="utf-8",
    )

    assert gate.main(_argv(project)) == 1
    out = capsys.readouterr().out
    assert "NEW DEAD CODE" in out
    assert "brand_new_helper" in out
    assert "vulture_whitelist.py" in out  # tells the developer where to go


def test_deleting_baselined_symbol_without_shrinking_fails(
    project: Path, capsys
) -> None:
    path = project / "pkg/machine.py"
    path.write_text(
        path.read_text().replace("    def _dead_method(self):\n        return 2\n", ""),
        encoding="utf-8",
    )

    assert gate.main(_argv(project)) == 1
    out = capsys.readouterr().out
    assert "BASELINE IS STALE" in out
    assert "Machine._dead_method" in out
    assert "--update" in out

    # ...and the documented fix is one command.
    assert gate.main(_argv(project, "--update")) == 0
    assert _baseline(project) == EXPECTED_BASELINE - {
        ("method", "machine.py", "Machine._dead_method")
    }
    assert gate.main(_argv(project)) == 0


def test_update_refuses_to_add_without_the_flag(project: Path, capsys) -> None:
    path = project / "pkg/machine.py"
    path.write_text(
        path.read_text() + "\n\ndef brand_new_helper():\n    return 0\n",
        encoding="utf-8",
    )

    assert gate.main(_argv(project, "--update")) == 1
    assert "only shrinks" in capsys.readouterr().out
    assert _baseline(project) == EXPECTED_BASELINE

    assert gate.main(_argv(project, "--update", "--allow-additions")) == 0
    assert ("function", "machine.py", "brand_new_helper") in _baseline(project)


def test_unrelated_edits_do_not_churn_the_baseline(project: Path) -> None:
    """Keys carry no line numbers: moving code around changes nothing."""
    path = project / "pkg/machine.py"
    path.write_text("# a comment\n\n\n" + path.read_text(), encoding="utf-8")
    before = (project / "dead_code_baseline.txt").read_text()

    assert gate.main(_argv(project)) == 0
    assert gate.main(_argv(project, "--update")) == 0
    assert (project / "dead_code_baseline.txt").read_text() == before


def test_notes_survive_an_update(project: Path) -> None:
    path = project / "dead_code_baseline.txt"
    text = path.read_text().replace(
        "Machine._dead_method", "Machine._dead_method  # looks like an unwired fix"
    )
    path.write_text(text, encoding="utf-8")

    assert gate.main(_argv(project, "--update")) == 0
    assert "Machine._dead_method  # looks like an unwired fix" in path.read_text()


def test_removing_the_dispatch_row_fails_the_whitelist(project: Path, capsys) -> None:
    """The whitelisted method is only live because of the table row."""
    path = project / "pkg/machine.py"
    path.write_text(
        path.read_text().replace('TABLE = {"a": "_build_a"}', "TABLE = {}"),
        encoding="utf-8",
    )

    assert gate.main(_argv(project)) == 1
    out = capsys.readouterr().out
    assert "WHITELIST PROBLEMS" in out
    assert "no longer contains '\"_build_a\"'" in out
    assert "the symbol is dead" in out


def test_stale_whitelist_entry_fails(project: Path, capsys) -> None:
    """Deleting a whitelisted symbol must take its whitelist entry with it."""
    path = project / "pkg/machine.py"
    path.write_text(
        path.read_text().replace("    def _build_a(self):\n        return 1\n", ""),
        encoding="utf-8",
    )

    assert gate.main(_argv(project)) == 1
    out = capsys.readouterr().out
    assert "Machine._build_a: matches nothing" in out


def test_wildcard_owner_needs_a_framework(project: Path, capsys) -> None:
    """``_.name`` would hide the name in every class; only HA names get it."""
    path = project / "vulture_whitelist.py"
    path.write_text(
        path.read_text()
        + "\n# site: pkg/machine.py\n# evidence: _dead_method\n# why: no.\n"
        + "_._dead_method\n",
        encoding="utf-8",
    )

    assert gate.main(_argv(project)) == 1
    assert "only allowed under '# framework:'" in capsys.readouterr().out


def test_entry_without_evidence_is_rejected(project: Path, capsys) -> None:
    path = project / "vulture_whitelist.py"
    path.write_text(
        path.read_text() + "\n# why: trust me.\nMachine._dead_method\n",
        encoding="utf-8",
    )

    assert gate.main(_argv(project)) == 1
    assert "needs '# site:'" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The real repository
# ---------------------------------------------------------------------------

# Names that mean something in many places in this codebase. A whitelist entry
# for one of these, however scoped, deserves a second look (issue #1074).
BROAD_NAMES = frozenset({
    "available",
    "config",
    "data",
    "get",
    "mode",
    "name",
    "options",
    "result",
    "state",
    "status",
    "update",
    "value",
})


@pytest.fixture(scope="module")
def repo_result() -> tuple[gate.Layout, gate.Result]:
    args = argparse.Namespace(
        root=str(REPO),
        package=gate.DEFAULT_PACKAGE,
        whitelist=gate.DEFAULT_WHITELIST,
        baseline=gate.DEFAULT_BASELINE,
        data_module=gate.DEFAULT_DATA_MODULE,
        data_class=gate.DEFAULT_DATA_CLASS,
    )
    layout = gate.build_layout(args)
    return layout, gate.run(layout)


@pytest.fixture(scope="module")
def whitelist() -> list[gate.WhitelistEntry]:
    return gate.parse_whitelist((REPO / gate.DEFAULT_WHITELIST).read_text())


def test_repo_gate_passes(repo_result) -> None:
    layout, result = repo_result
    assert result.ok, "\n" + gate.report(result, layout)


def test_every_finding_is_in_exactly_one_file(repo_result) -> None:
    _, result = repo_result
    baseline = set(result.baseline)
    assert not (result.suppressed & baseline), "whitelisted AND baselined"
    assert result.suppressed | baseline == result.findings


def test_framework_entries_exist_in_home_assistant(whitelist) -> None:
    """A '# framework:' entry cites Home Assistant, so ask Home Assistant."""
    checked = 0
    for entry in whitelist:
        if not entry.framework:
            continue
        try:
            module = importlib.import_module(entry.framework)
        except ModuleNotFoundError:
            module_name, _, class_name = entry.framework.rpartition(".")
            owner = getattr(importlib.import_module(module_name), class_name)
            assert hasattr(owner, entry.name), (
                f"{entry.label}: {entry.framework} has no attribute {entry.name}"
            )
        else:
            assert entry.name in inspect.getsource(module), (
                f"{entry.label}: {entry.framework} never mentions {entry.name}"
            )
        checked += 1
    assert checked >= 10


def test_whitelist_is_scoped_and_narrow(whitelist) -> None:
    for entry in whitelist:
        assert entry.name not in BROAD_NAMES, (
            f"{entry.label}: too broad a name to whitelist"
        )
        assert entry.why, f"{entry.label}: no reason given"
        if entry.owner == gate.ANY_OWNER:
            assert entry.framework, f"{entry.label}: wildcard owner without framework"
        if not entry.framework:
            assert entry.evidence, f"{entry.label}: no dynamic access site cited"


def test_battery_write_path_is_whitelisted_not_baselined(repo_result) -> None:
    """The mode builders and executors are reached by getattr and are LIVE.

    A naive gate lists these twelve methods as unused. They are how the
    integration writes to the Powerwall.
    """
    _, result = repo_result
    live = {
        f.qualname
        for f in result.suppressed
        if f.qualname.startswith(("StateMachine._build_", "StateMachine._execute_"))
    }
    assert len(live) == 12
    assert not {
        f.qualname
        for f in result.baseline
        if f.qualname.startswith(("StateMachine._build_", "StateMachine._execute_"))
    }


def test_pyproject_wiring() -> None:
    """Review findings #1073, #1075, #1077."""
    config = tomllib.loads((REPO / "pyproject.toml").read_text())
    vulture = config["tool"]["vulture"]
    assert gate.DEFAULT_WHITELIST in vulture["paths"]
    assert gate.DEFAULT_WHITELIST in config["tool"]["ruff"]["extend-exclude"]
    assert vulture["min_confidence"] == 60
    # Globs are for conventions Home Assistant defines by pattern, nothing else.
    assert sorted(vulture["ignore_names"]) == ["_attr_*", "async_step_*"]


def test_whitelist_text_is_valid_python() -> None:
    """vulture has to be able to parse it."""
    compile(textwrap.dedent((REPO / gate.DEFAULT_WHITELIST).read_text()), "wl", "exec")
