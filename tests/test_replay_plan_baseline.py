"""The planner replay baseline (scripts/replay_plan_baseline.py), held in CI.

The full comparison is a script run: 84 captures under two arms takes about
45 seconds, too slow for every test run. What runs here is a three-capture
subset chosen to cover the three things the planner does that matter most (a
grid pre-charge ahead of the demand window, a spike export, a boost charge on
a hand-written scenario), plus the checks that make the baseline trustworthy:

* it notices. The historical failure (a constructor argument removed from
  ``SlotBuilder``, swallowed by the facade) and a quiet change to a plan both
  have to come out as a readable diff;
* it is complete: every tracked capture has a baseline and nothing else does;
* the files are exactly what the script writes, so a hand edit cannot pass;
* the script reaches the engine only through ``compute_derived_values``, so
  it keeps working, unchanged, while the code around it is deleted.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from custom_components.localshift.engine.slots import SlotBuilder
from scripts import replay_plan_baseline as rpb

REPO_ROOT = Path(__file__).resolve().parent.parent

PRECHARGE_DAY = "simulations/replay/2026-08-25.json"
SPIKE_INSTANT = "simulations/replay-1119/2026-10-12T151530.json"
BOOST_SCENARIO = "simulations/scenarios/cloudy-day.json"
CI_SUBSET = (PRECHARGE_DAY, SPIKE_INSTANT, BOOST_SCENARIO)


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
