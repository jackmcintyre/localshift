"""The planner replay baseline (scripts/replay_plan_baseline.py), held in CI.

The full comparison is a script run: 84 captures under two arms takes about
45 seconds, too slow for every test run. CI runs it as its own workflow
(``.github/workflows/replay-baseline.yml``). What runs here is a four-capture
subset (a grid pre-charge ahead of the demand window, a spike export, a boost
charge on a hand-written scenario, and the one capture whose first slot is
synthetic), plus the checks that make the baseline trustworthy:

* it notices. The historical failure (a constructor argument removed from
  ``SlotBuilder``, swallowed by the facade) and a quiet change to a plan both
  have to come out as a readable diff;
* it is complete: every tracked capture has a baseline and nothing else does;
* the files are exactly what the script writes, so a hand edit cannot pass;
* it does not depend on the time zone of the machine replaying it, or on
  Home Assistant's default zone, and records nothing scheduled for deletion;
* the script reaches the engine only through ``compute_derived_values``, so
  it keeps working, unchanged, while the code around it is deleted.
"""

from __future__ import annotations

import ast
import json
import time
from datetime import UTC
from pathlib import Path

import pytest
from homeassistant.util import dt as dt_util

from custom_components.localshift.engine.slots import SlotBuilder
from scripts import replay_plan_baseline as rpb

REPO_ROOT = Path(__file__).resolve().parent.parent

PRECHARGE_DAY = "simulations/replay/2026-08-25.json"
SPIKE_INSTANT = "simulations/replay-1119/2026-10-12T151530.json"
BOOST_SCENARIO = "simulations/scenarios/cloudy-day.json"
# The price feed starts after "now", so slot 0 is synthesised from the clock.
SYNTHETIC_SLOT = "simulations/replay-1120/2026-10-04T213040.json"
# No ``ha_timezone`` of its own: the demand window falls back to HA's default.
NO_ZONE_SCENARIO = "simulations/scenarios/inverted-night.json"
CI_SUBSET = (PRECHARGE_DAY, SPIKE_INSTANT, BOOST_SCENARIO, SYNTHETIC_SLOT)


@pytest.mark.replay_baseline
@pytest.mark.parametrize("capture", CI_SUBSET)
def test_plan_matches_baseline(capture):
    differences = rpb.compare(REPO_ROOT / capture)
    assert not differences, (
        f"The plan for {capture} is not the one in simulations/plan-baseline:\n  "
        + "\n  ".join(differences[:40])
        + "\nIf this change is meant to alter plans, run "
        "`uv run scripts/replay_plan_baseline.py --write` and commit the diff. "
        "If it is not, it is not behaviour-neutral."
    )


class TestTheReplayIgnoresWhereItRuns:
    """Written on a laptop in Sydney, checked on CI in UTC: same bytes."""

    @pytest.fixture
    def machine_zone(self, monkeypatch):
        def set_zone(name: str) -> None:
            monkeypatch.setenv("TZ", name)
            time.tzset()

        yield set_zone
        monkeypatch.undo()
        time.tzset()

    @pytest.mark.parametrize("zone", ["UTC", "America/New_York"])
    def test_the_machine_time_zone_does_not_reach_the_plan(self, machine_zone, zone):
        """``SlotBuilder`` stamps a synthetic slot 0 with the machine's zone."""
        machine_zone(zone)
        assert rpb.compare(REPO_ROOT / SYNTHETIC_SLOT) == []

    def test_the_machine_time_zone_is_put_back(self, machine_zone):
        machine_zone("America/New_York")
        rpb.compare(REPO_ROOT / SYNTHETIC_SLOT)
        assert time.tzname[0] == "EST"

    def test_home_assistants_default_zone_does_not_reach_the_plan(self, monkeypatch):
        """The suite's default is UTC; live is Sydney; a test may leave either."""
        monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", UTC)
        assert rpb.compare(REPO_ROOT / NO_ZONE_SCENARIO) == []

    def test_the_replay_runs_in_the_live_zone(self):
        assert rpb.LIVE_TIME_ZONE.key == "Australia/Sydney"
        capture = REPO_ROOT / NO_ZONE_SCENARIO
        document = json.loads(rpb.baseline_path(capture).read_text())
        # Under UTC this scenario plans about 7 kWh of grid import, a state
        # the live system cannot be in.
        assert document["arms"]["captured"]["result"]["projected_import_kwh"] == 0.0


class TestTheBaselineNotices:
    def test_a_swallowed_optimizer_failure_is_a_diff(self, monkeypatch):
        """The failure this exists for: no exception, no failing test, no plan."""

        def narrowed_init(self, config_options, ha_timezone):
            """SlotBuilder.__init__ with solar_accuracy_tracker removed."""

        monkeypatch.setattr(SlotBuilder, "__init__", narrowed_init)

        differences = rpb.compare(REPO_ROOT / PRECHARGE_DAY)

        for arm in rpb.ARMS:
            assert f"[{arm}] optimizer_result_present: true -> false" in differences
            assert (
                f'[{arm}] swallowed_failures: [] -> ["inline_optimizer"]' in differences
            )

    def test_a_changed_plan_is_a_diff(self, monkeypatch):
        """A plan that still solves, but from different slots, is caught by slot.

        The same family of fault as the one above, minus the exception: the
        slot builder quietly hands the planner half the solar.
        """
        real_build_slots = SlotBuilder.build_slots

        def dimmed(self, *args, **kwargs):
            slots, metadata = real_build_slots(self, *args, **kwargs)
            for slot in slots:
                slot.solar_kwh *= 0.5
            return slots, metadata

        monkeypatch.setattr(SlotBuilder, "build_slots", dimmed)

        differences = rpb.compare(REPO_ROOT / PRECHARGE_DAY)

        assert not any("optimizer_result_present" in d for d in differences)
        assert any("] slot 0 (" in d and "solar_kwh" in d for d in differences)
        assert any("plan slots differ" in d for d in differences)
        assert any("projected_net_cost" in d for d in differences)

    def test_a_removed_output_field_is_a_diff(self):
        capture = REPO_ROOT / PRECHARGE_DAY
        expected = json.loads(rpb.baseline_path(capture).read_text())
        actual = json.loads(json.dumps(expected))
        del actual["arms"]["strict"]["summary"]["dw_entry_soc_pct"]
        actual["arms"]["strict"]["decisions"].pop()

        differences = rpb.diff(expected, actual)

        assert any(
            line.startswith("[strict] summary.dw_entry_soc_pct:")
            and line.endswith("-> (removed)")
            for line in differences
        ), differences
        assert any("slots ->" in line for line in differences), differences

    def test_identical_documents_have_no_diff(self):
        capture = REPO_ROOT / PRECHARGE_DAY
        document = json.loads(rpb.baseline_path(capture).read_text())
        assert rpb.diff(document, json.loads(json.dumps(document))) == []


class TestTheBaselineIsComplete:
    def test_every_tracked_capture_has_a_baseline(self):
        captures = rpb.tracked_captures()
        assert len(captures) >= 84
        missing = [
            c.relative_to(REPO_ROOT).as_posix()
            for c in captures
            if not rpb.baseline_path(c).exists()
        ]
        assert not missing, (
            f"No baseline for {missing}. Run scripts/replay_plan_baseline.py --write."
        )

    def test_no_baseline_without_a_capture(self):
        orphans = rpb.orphans(rpb.tracked_captures())
        assert not orphans, f"Baselines whose capture is gone: {orphans}"

    def test_the_ci_subset_is_tracked(self):
        tracked = {c.relative_to(REPO_ROOT).as_posix() for c in rpb.tracked_captures()}
        assert set(CI_SUBSET) <= tracked

    def test_files_are_exactly_what_the_script_writes(self):
        for path in sorted(rpb.BASELINE_DIR.glob("*/*.json")):
            text = path.read_text()
            assert rpb.dumps(json.loads(text)) == text, (
                f"{path.name} is not in the script's own format: regenerate it, "
                "do not edit it."
            )

    def test_every_baseline_holds_a_plan_or_says_why_not(self):
        """Manual override is the one case with no plan; anything else is a bug."""
        without_plan = []
        for path in sorted(rpb.BASELINE_DIR.glob("*/*.json")):
            document = json.loads(path.read_text())
            for arm, record in document["arms"].items():
                assert record["swallowed_failures"] == [], (path.name, arm)
                if not record["optimizer_result_present"]:
                    without_plan.append(path.name)
                    assert record["commanded_mode"] == "manual", (path.name, arm)
        assert sorted(set(without_plan)) == ["manual-override-bypass.json"]


DEAD_CODE_BASELINE = REPO_ROOT / "dead_code_baseline.txt"


def _dead_names(text: str) -> dict[str, str]:
    """Leaf names in a dead-code baseline, each with the line that lists it.

    Lines read ``<kind> <file> <qualified name>  [# note]``; the leaf of
    ``data.optimizer_safety_block_reason@2`` is ``optimizer_safety_block_reason``.
    """
    names: dict[str, str] = {}
    for line in text.splitlines():
        entry = line.split("#", 1)[0].split()
        if len(entry) < 3:
            continue
        leaf = entry[2].rsplit(".", 1)[-1].split("@", 1)[0]
        names.setdefault(leaf, " ".join(entry))
    return names


def _keys(value, found: set[str]) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            found.add(key)
            _keys(inner, found)
    elif isinstance(value, list):
        for inner in value:
            _keys(inner, found)


def _recorded_names() -> set[str]:
    """Every field name the baseline holds still, in the script or on disk."""
    found = set(rpb.DATA_FIELDS) | set(rpb.DECISION_COLUMNS)
    for path in sorted(rpb.BASELINE_DIR.glob("*/*.json")):
        for record in json.loads(path.read_text())["arms"].values():
            for section in ("result", "summary", "data"):
                _keys(record[section], found)
    return found


class TestTheBaselineRecordsNothingScheduledForDeletion:
    """A field on the dead-code list must not be part of the before-state.

    If it were, the PR that deletes it would change every baseline file, and
    "plans identical" could not be shown for exactly the change it exists to
    cover. ``optimizer_safety_block_reason`` was recorded at first and would
    have flipped ``"" -> null`` in all 168 records.
    """

    def test_dead_names_are_read_from_the_baseline_format(self):
        names = _dead_names(
            "# comment\n"
            "\n"
            "attribute    engine/optimizer_facade.py  data.optimizer_safety_block_reason@2\n"
            "unread-field coordinator/data.py  CoordinatorData.spike_end_time  # note\n"
            "function     engine/optimizer_runner.py  run_optimizer\n"
        )
        assert set(names) == {
            "optimizer_safety_block_reason",
            "spike_end_time",
            "run_optimizer",
        }

    def test_the_recorded_names_cover_all_three_sections(self):
        recorded = _recorded_names()
        assert "dw_entry_soc_pct" in recorded
        assert "planner_export_floor_pct" in recorded
        assert "predicted_soc_pct" in recorded
        assert "optimizer_safety_block_reason" not in recorded

    def test_no_recorded_field_is_on_the_dead_code_list(self):
        if not DEAD_CODE_BASELINE.exists():
            pytest.skip("dead_code_baseline.txt is not on this branch yet")
        dead = _dead_names(DEAD_CODE_BASELINE.read_text())
        overlap = {name: dead[name] for name in sorted(_recorded_names() & set(dead))}
        assert not overlap, (
            "The replay baseline records fields that are scheduled for deletion: "
            f"{overlap}. Drop them from scripts/replay_plan_baseline.py and "
            "regenerate, or the PR that deletes them cannot show identical plans."
        )


def test_the_script_reaches_the_engine_only_through_the_cycle_entry_point():
    """Keep the baseline independent of the code it is there to protect.

    The replay may construct a ``ComputationEngine``, seed its history fetcher,
    and call ``compute_derived_values``. Anything else it touched on the
    engine would be a private helper that a later deletion could remove or
    rename, and the baseline would then break for a reason that has nothing
    to do with plans. Test helpers are out for the same reason: they change
    when the code they exercise does.
    """
    tree = ast.parse(Path(rpb.__file__).read_text())
    engine_members = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "engine"
    }
    assert engine_members == {"_history_fetcher", "compute_derived_values"}

    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    project_imports = {
        name for name in imported if name.startswith(("custom_components", "tests"))
    }
    assert project_imports == {
        "custom_components.localshift.computation_engine",
        "custom_components.localshift.const",
        "custom_components.localshift.coordinator",
        "custom_components.localshift.pricing.types",
        "tests",
    }
