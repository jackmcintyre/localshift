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
from .const import PLATFORMS
from .machine import Machine, Other


def consume(data: Snapshot):
    return data.used, Machine().run("a"), Other(), Plain(), Thing(), PLATFORMS


consume(Snapshot())
"""

SENSOR = """\
async def async_setup_entry(hass, entry, async_add_entities):
    return bool(hass and entry and async_add_entities)
"""

WHITELIST = '''\
"""Synthetic whitelist."""

# framework: homeassistant.config_entries
# why: HA calls the platform hook by name.
async_setup_entry

# framework: homeassistant.helpers.entity.Entity
# why: HA reads it by name.
_.icon

# file: machine.py
# kind: method
# site: pkg/machine.py
# evidence: "{name}"
# evidence: getattr(self, TABLE[key])
# why: string table plus getattr.
Machine._build_a

# file: data.py
# kind: unread-field
# site: tools/replay.py
# evidence: data.external
# why: read by a script on purpose.
Snapshot.external
'''

PYPROJECT = """\
[tool.vulture]
min_confidence = 60
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
    _write(tmp_path, "pkg/const.py", 'PLATFORMS = ["sensor"]\n')
    _write(tmp_path, "pkg/sensor.py", SENSOR)
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
        path.read_text()
        + "\n# file: machine.py\n# kind: method\n# why: trust me.\n"
        + "Machine._dead_method\n",
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


def _package_class(finding: gate.Finding) -> type:
    """The real class that owns a finding, imported from the integration."""
    module_name = "custom_components.localshift." + finding.file[: -len(".py")]
    module_name = module_name.replace("/", ".").removesuffix(".__init__")
    owner = importlib.import_module(module_name)
    for part in finding.qualname.split(".")[:-1]:
        owner = getattr(owner, part)
    return owner


def test_framework_members_belong_to_the_framework_class_named(
    repo_result, whitelist
) -> None:
    """``_.icon`` under ``framework: ...Entity`` must only cover Entities.

    Checked on the owner: every class the entry ends up covering has to be a
    real subclass of the framework class the block names, and that framework
    class has to define the member. A name that merely exists somewhere in
    Home Assistant is not enough.
    """
    layout, result = repo_result
    checked = 0
    for entry, matched in gate.match_whitelist(layout, whitelist, result.findings):
        if not (entry.framework and entry.owner == gate.ANY_OWNER):
            continue
        module_name, _, class_name = entry.framework.rpartition(".")
        framework_cls = getattr(importlib.import_module(module_name), class_name)
        assert inspect.isclass(framework_cls), entry.framework
        assert hasattr(framework_cls, entry.name), (
            f"{entry.label}: {entry.framework} has no attribute {entry.name}"
        )
        assert matched, entry.label
        for finding in matched:
            owner = _package_class(finding)
            assert issubclass(owner, framework_cls), (
                f"{entry.label} covers {finding.file} {finding.qualname}, but "
                f"{owner.__name__} is not a {entry.framework}"
            )
            checked += 1
    assert checked >= 60


def test_framework_module_hooks_are_called_by_home_assistant(
    repo_result, whitelist
) -> None:
    """Bare hooks: HA must call the name, and only in modules it loads."""
    from homeassistant.const import Platform  # noqa: PLC0415

    layout, result = repo_result
    loadable = {"__init__.py"} | {f"{p.value}.py" for p in Platform}
    assert gate.entry_modules(layout.package) <= loadable

    checked = 0
    for entry, matched in gate.match_whitelist(layout, whitelist, result.findings):
        if not (entry.framework and entry.owner is None):
            continue
        module = importlib.import_module(entry.framework)
        assert inspect.ismodule(module)
        assert f".{entry.name}(" in inspect.getsource(module), (
            f"{entry.label}: {entry.framework} never calls .{entry.name}()"
        )
        assert matched
        assert {f.file for f in matched} <= gate.entry_modules(layout.package)
        checked += 1
    assert checked == 3


def test_framework_blocks_hold_only_the_two_allowed_shapes(whitelist) -> None:
    for entry in whitelist:
        if entry.framework:
            assert entry.owner in {gate.ANY_OWNER, None}, entry.label


def test_store_migration_hook_exists_in_home_assistant() -> None:
    """The one whitelisted symbol whose reader is inside Home Assistant."""
    from homeassistant.helpers.storage import Store  # noqa: PLC0415

    assert hasattr(Store, "_async_migrate_func")


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
            assert entry.file and entry.kind, f"{entry.label}: not scoped to a file"


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
    # No global name globs: `_attr_*` and `async_step_*` are applied by the
    # gate, scoped to Home Assistant classes (review of PR #1147, item 6).
    assert "ignore_names" not in vulture


def test_whitelist_text_is_valid_python() -> None:
    """vulture has to be able to parse it."""
    compile(textwrap.dedent((REPO / gate.DEFAULT_WHITELIST).read_text()), "wl", "exec")


# ---------------------------------------------------------------------------
# Defects found in review of PR #1147. Each of these passed the gate.
# ---------------------------------------------------------------------------


def _drop_from_baseline(project: Path, needle: str) -> None:
    path = project / "dead_code_baseline.txt"
    lines = [line for line in path.read_text().splitlines() if needle not in line]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append(project: Path, rel: str, text: str) -> None:
    path = project / rel
    existing = path.read_text() if path.exists() else ""
    path.write_text(existing + text, encoding="utf-8")


def test_framework_block_cannot_launder_a_scoped_symbol(project: Path, capsys) -> None:
    """Review item 1: '# framework:' skipped every check for 'Owner.name'."""
    _append(
        project,
        "vulture_whitelist.py",
        "\n# framework: homeassistant.helpers.entity.Entity\n# why: no.\n"
        "Machine._dead_method\n",
    )
    _drop_from_baseline(project, "Machine._dead_method")

    assert gate.main(_argv(project)) == 1
    assert "framework" in capsys.readouterr().out


def test_framework_block_cannot_launder_a_module_function(
    project: Path, capsys
) -> None:
    """Review item 1: a bare name under '# framework:' matched any function."""
    _append(project, "pkg/machine.py", "\n\ndef run_planner():\n    return 0\n")
    _append(
        project,
        "vulture_whitelist.py",
        "\n# framework: homeassistant.helpers.entity.Entity\n# why: no.\nrun_planner\n",
    )

    assert gate.main(_argv(project)) == 1
    assert "run_planner" in capsys.readouterr().out


def test_module_hook_only_counts_in_a_module_ha_loads(project: Path, capsys) -> None:
    """Review item 2: a dead async_setup_entry in a helper module was live."""
    _append(
        project,
        "pkg/machine.py",
        "\n\nasync def async_setup_entry(hass, entry, add):\n"
        "    return bool(hass and entry and add)\n",
    )

    assert gate.main(_argv(project)) == 1
    out = capsys.readouterr().out
    assert "NEW DEAD CODE" in out
    assert "machine.py  async_setup_entry" in out


def test_scoped_entry_does_not_cover_a_same_named_class_elsewhere(
    project: Path, capsys
) -> None:
    """Review item 2: a second ``Machine._build_a`` in another file was live."""
    _write(
        project,
        "pkg/elsewhere.py",
        "class Machine:\n    def _build_a(self):\n        return 9\n",
    )
    _append(
        project, "pkg/reader.py", "\nfrom . import elsewhere\n\nelsewhere.Machine()\n"
    )

    assert gate.main(_argv(project)) == 1
    out = capsys.readouterr().out
    assert "NEW DEAD CODE" in out
    assert "elsewhere.py  Machine._build_a" in out


def test_unannotated_attribute_on_the_data_class_is_not_dropped(
    project: Path, capsys
) -> None:
    """Review item 3: the census skipped it and the filter discarded it."""
    _append(project, "pkg/data.py", "    LEGACY_FLAG = 7\n")

    assert gate.main(_argv(project)) == 1
    assert "Snapshot.LEGACY_FLAG" in capsys.readouterr().out


ALL_KINDS = (
    "attribute",
    "class",
    "function",
    "import",
    "method",
    "property",
    "unreachable_code",
    "variable",
    gate.UNREAD_FIELD,
)


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_baseline_round_trips_every_kind(kind: str) -> None:
    """Review item 4: 'unreachable_code' was written with no separator."""
    finding = gate.Finding(kind, "engine/core.py", "Planner.solve")
    text = gate.render_baseline({finding: "a note"})
    assert gate.parse_baseline(text) == {finding: "a note"}


def test_unreachable_code_survives_an_update(project: Path) -> None:
    """Review item 4, end to end: the gate must still run after baselining it."""
    _append(
        project,
        "pkg/reader.py",
        "\n\ndef after_return():\n    return 1\n    print('never')\n\n\nafter_return()\n",
    )
    assert gate.main(_argv(project, "--update", "--allow-additions")) == 0
    assert gate.main(_argv(project)) == 0


def test_second_dead_symbol_with_the_same_name_is_seen(project: Path, capsys) -> None:
    """Review item 5: same kind, file and name collapsed into one key."""
    _append(
        project,
        "pkg/machine.py",
        "\n\nif TABLE:\n\n    def twin():\n        return 1\n\nelse:\n\n"
        "    def twin():\n        return 2\n",
    )
    assert gate.main(_argv(project)) == 1
    out = capsys.readouterr().out
    assert "machine.py  twin\n" in out
    assert "machine.py  twin@2" in out


def test_attr_glob_only_covers_framework_classes(project: Path, capsys) -> None:
    """Review item 6: ``_attr_*`` was ignored on every class."""
    _append(
        project,
        "pkg/entity.py",
        "\n\nclass NotAnEntity:\n    def __init__(self):\n"
        "        self._attr_bogus = 1\n",
    )
    _append(
        project, "pkg/reader.py", "\nfrom .entity import NotAnEntity\n\nNotAnEntity()\n"
    )

    assert gate.main(_argv(project)) == 1
    assert "NotAnEntity._attr_bogus" in capsys.readouterr().out


def test_async_step_only_covers_flow_classes(project: Path, capsys) -> None:
    """Review item 6: ``async_step_*`` was ignored on every class."""
    _write(
        project,
        "pkg/flow.py",
        "from homeassistant.config_entries import ConfigFlow\n\n\n"
        "class Flow(ConfigFlow):\n    async def async_step_user(self, i=None):\n"
        "        return i\n\n\n"
        "class Helper:\n    async def async_step_orphan(self):\n        return 0\n",
    )
    _append(
        project,
        "pkg/reader.py",
        "\nfrom .flow import Flow, Helper\n\nFlow()\nHelper()\n",
    )

    assert gate.main(_argv(project)) == 1
    out = capsys.readouterr().out
    assert "Helper.async_step_orphan" in out
    assert "Flow.async_step_user" not in out


def test_census_that_finds_no_fields_fails(project: Path, capsys) -> None:
    """Review item 7: a renamed data class turned the census off silently."""
    argv = [a if a != "Snapshot" else "RenamedAway" for a in _argv(project)]

    assert gate.main(argv) == 1
    assert "found no fields" in capsys.readouterr().out
