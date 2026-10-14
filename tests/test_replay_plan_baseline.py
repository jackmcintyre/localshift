"""The planner replay baseline (scripts/replay_plan_baseline.py), held in CI.

The full comparison is a script run: 84 captures under three arms takes about
90 seconds, too slow for every test run. CI runs it as its own workflow
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
  it keeps working, unchanged, while the code around it is deleted;
* the live-like arm exercises what it claims to (solar bias correction, the
  accuracy discount, the confidence ceiling, the per-day load profiles), and
  its plans move when its seeds do.
"""

from __future__ import annotations

import ast
import json
import time
from datetime import UTC
from pathlib import Path

import pytest
from homeassistant.util import dt as dt_util

from custom_components.localshift.computation_engine import ComputationEngine
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
            # ``coverage`` and ``no_grant`` are the live-like arm's own.
            for section in ("result", "summary", "data", "coverage", "no_grant"):
                _keys(record.get(section, {}), found)
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
        assert "solar_bias_slots" in recorded
        assert "plan_mode_pending" in recorded
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

    The live-like arm adds one engine call, ``set_solar_accuracy_tracker``
    (the call the coordinator makes to attach the tracker), and two imports:
    the tracker, and the Solcast analysis record it builds.
    """
    tree = _script_tree()
    assert _members_of(tree, "engine") == {
        "_history_fetcher",
        "compute_derived_values",
        "set_solar_accuracy_tracker",
    }

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
        "custom_components.localshift.forecast.solar_accuracy",
        "custom_components.localshift.forecast.solcast_analysis",
        "custom_components.localshift.pricing.types",
        "tests",
    }
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("custom_components")
        for alias in node.names
    }
    assert imported_names == {
        "BatteryMode",
        "ComputationEngine",
        "ConfidenceInterval",
        "CoordinatorData",
        "ForecastSlot",
        "SolarAccuracyTracker",
        "SolcastAnalysis",
    }


def _script_tree() -> ast.Module:
    return ast.parse(Path(rpb.__file__).read_text())


def _members_of(tree: ast.Module, name: str) -> set[str]:
    """Attributes the script reads or writes on the variable called ``name``."""
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == name
    }


# Named for deletion by the open dead-code PRs (#1153, #1154, #1155) and the
# batches queued behind them. Most are in dead_code_baseline.txt today; they
# are restated because each PR removes its own lines from that file, and the
# replay must stay clear of them until the code itself is gone.
SCHEDULED_FOR_DELETION = frozenset({
    "optimizer_soc_underprepared",
    "optimizer_safety_block_reason",
    "hybrid_solar_accuracy",
    "_baseline_avg_kw",
    "_compute_context_bias",
    "_compute_context_additive_bias",
    "get_additive_correction",
    "get_profile_for_day",
    "_get_profile_for_day",
    "compute_weighted_confidence",
})


# The script sets this as a config option and as a switch state, which is how
# strict demand-window entry is turned on. The dead-code list holds a
# CoordinatorData field of the same name, which the script never touches.
OPTION_KEYS_SHARING_A_DEAD_NAME = frozenset({"allow_dw_entry_under_target"})


def _identifiers(tree: ast.Module) -> set[str]:
    """Every name the script could reach production code by.

    Variable and attribute names, imported names, keyword arguments, and any
    string literal that is itself an identifier (a ``getattr`` or ``patch``
    target, a dict key read off the planner).
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.update(node.name.split("."))
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.update(part for part in node.value.split(".") if part.isidentifier())
    return found


class TestTheScriptStaysClearOfCodeScheduledForDeletion:
    """All three arms, the live-like one included, must outlive the deletions.

    A replay that called a method on the deletion list would fail the moment
    that method went, and the PR removing it could not show identical plans.
    """

    def test_identifiers_are_read_from_every_kind_of_reference(self):
        tree = ast.parse(
            "from a.b import run_optimizer\n"
            "x.daily_forecast = f(adaptive_params=1)\n"
            "getattr(y, 'get_profile_for_day')\n"
            "patch('m.hybrid_solar_accuracy')\n"
        )
        assert _identifiers(tree) >= {
            "run_optimizer",
            "daily_forecast",
            "adaptive_params",
            "get_profile_for_day",
            "hybrid_solar_accuracy",
        }

    def test_the_script_names_nothing_scheduled_for_deletion(self):
        named = _identifiers(_script_tree())
        assert not named & SCHEDULED_FOR_DELETION

    def test_the_script_names_nothing_on_the_dead_code_list(self):
        if not DEAD_CODE_BASELINE.exists():
            pytest.skip("dead_code_baseline.txt is not on this branch yet")
        dead = _dead_names(DEAD_CODE_BASELINE.read_text())
        named = _identifiers(_script_tree()) - OPTION_KEYS_SHARING_A_DEAD_NAME
        overlap = {name: dead[name] for name in sorted(named & set(dead))}
        assert not overlap, (
            "scripts/replay_plan_baseline.py refers to names that are on the "
            f"dead-code list: {overlap}"
        )

    def test_the_tracker_is_used_only_through_calls_that_stay(self):
        """Seeded and read the way live does it; nothing private, nothing dying."""
        assert _members_of(_script_tree(), "tracker") == {
            "record_forecast",
            "backfill_actual",
            "apply_bias_correction",
            "metrics",
        }

    def test_the_history_fetcher_is_seeded_only_through_its_caches(self):
        """The fields a completed recorder fetch leaves behind, and no method."""
        assert _members_of(_script_tree(), "history") == {
            "_historical_load_cache",
            "_historical_load_sample_counts",
            "_historical_load_source",
            "_weekday_hourly_avg_kw",
            "_weekday_sample_counts",
            "_weekend_hourly_avg_kw",
            "_weekend_sample_counts",
            "_daily_hourly_avg_kw",
            "_daily_sample_counts",
            "_profile_source",
            "_recent_load_1hr_kw",
        }


LIVE_LIKE = rpb.LIVE_LIKE
# Sunday evening: the weekend aggregate feeds its load forecast.
WEEKEND_INSTANT = SYNTHETIC_SLOT


def _baseline_documents() -> list[dict]:
    return [
        json.loads(path.read_text())
        for path in sorted(rpb.BASELINE_DIR.glob("*/*.json"))
    ]


def _live_like_differences(capture: str) -> list[str]:
    """A fresh live-like replay of one capture against its baseline record."""
    path = REPO_ROOT / capture
    expected = json.loads(rpb.baseline_path(path).read_text())
    actual = json.loads(json.dumps(expected))
    actual["arms"][LIVE_LIKE] = rpb.replay(json.loads(path.read_text()), LIVE_LIKE)
    return rpb.diff(expected, actual)


@pytest.mark.replay_baseline
class TestTheLiveLikeArmExercisesWhatItClaims:
    """The learned inputs live always has are present, and they bite."""

    def test_the_baseline_meets_every_coverage_floor(self):
        totals = rpb.coverage_totals(_baseline_documents())
        assert set(rpb.COVERAGE_FLOORS) <= set(totals)
        assert rpb.coverage_shortfalls(totals) == []

    def test_solar_correction_is_active_in_every_planned_capture(self):
        """Past the tracker's 20-sample threshold, with accuracy below 100%."""
        for document in _baseline_documents():
            record = document["arms"][LIVE_LIKE]
            coverage = record["coverage"]
            assert coverage["solar_samples"] >= 300, document["capture"]
            assert 0.5 < coverage["solar_accuracy"] < 0.85, document["capture"]
            if (
                record["optimizer_result_present"]
                and coverage["confidence_ceiling_today"]
            ):
                assert coverage["confidence_ceiling_today"] < 1.0, document["capture"]

    def test_both_day_types_and_every_profile_rung_occur(self):
        totals = rpb.coverage_totals(_baseline_documents())
        assert totals["weekday_captures"] >= 20
        assert totals["weekend_captures"] >= 10
        for rung in ("day_profile", "weekday_aggregate", "weekend_aggregate"):
            assert totals[f"load_slots_from_{rung}"] >= 400, rung
        assert totals["load_slots_from_combined_profile"] > 0
        assert totals["load_slots_on_ceiling"] > 0

    def test_the_precharge_day_is_corrected_and_forecast_from_its_own_profile(self):
        document = json.loads(rpb.baseline_path(REPO_ROOT / PRECHARGE_DAY).read_text())
        coverage = document["arms"][LIVE_LIKE]["coverage"]
        assert coverage["day_type"] == "weekday"
        assert coverage["solar_bias_slots"] > 0
        assert coverage["load_slots"]["day"] > 0
        assert coverage["load_slots"]["aggregate_weekday"] > 0
        strict = document["arms"]["strict"]
        assert document["arms"][LIVE_LIKE]["decisions"] != strict["decisions"]

    def test_a_weekend_capture_is_forecast_from_the_weekend_aggregate(self):
        document = json.loads(
            rpb.baseline_path(REPO_ROOT / WEEKEND_INSTANT).read_text()
        )
        coverage = document["arms"][LIVE_LIKE]["coverage"]
        assert coverage["day_type"] == "weekend"
        assert coverage["load_slots"]["aggregate_weekend"] > 0

    def test_a_frozen_cycle_holds_the_mode_and_reports_the_plan_as_pending(self):
        """No decision grant: the capture's mode stays, the plan's is pending."""
        record = json.loads(rpb.baseline_path(REPO_ROOT / SPIKE_INSTANT).read_text())[
            "arms"
        ][LIVE_LIKE]
        assert record["commanded_mode"] == "proactive_export"
        assert record["no_grant"]["commanded_mode"] == "self_consumption"
        assert record["no_grant"]["plan_mode_pending"] == "proactive_export"

    def test_a_run_that_stops_exercising_the_arm_fails(self, capsys):
        """Plans can match while the arm has gone quiet; that is not a pass."""
        documents = _baseline_documents()
        for document in documents:
            document["arms"][LIVE_LIKE]["coverage"]["solar_bias_slots"] = 0
        totals = rpb.coverage_totals(documents)
        assert any(
            line.startswith("slots_with_solar_bias_applied: 0")
            for line in rpb.coverage_shortfalls(totals)
        )
        assert rpb._report_coverage(documents, enforce=True) == 1
        assert "no longer exercises" in capsys.readouterr().out
        # A partial run (--only) cannot say what every capture adds up to.
        assert rpb._report_coverage(documents[:2], enforce=False) == 0


@pytest.mark.replay_baseline
class TestTheLiveLikeArmIsSensitiveToItsSeeds:
    """If a seed can change and no plan moves, the seed proves nothing."""

    def test_an_unchanged_seed_reproduces_the_baseline(self):
        assert _live_like_differences(PRECHARGE_DAY) == []

    def test_halving_the_solar_bias_changes_the_plan(self, monkeypatch):
        halved = {
            weather: {bucket: bias / 2 for bucket, bias in by_time.items()}
            for weather, by_time in rpb.SOLAR_BIAS.items()
        }
        monkeypatch.setattr(rpb, "SOLAR_BIAS", halved)

        differences = _live_like_differences(PRECHARGE_DAY)

        assert any("coverage.solar_accuracy" in d for d in differences)
        assert any("] slot " in d and "solar_kwh" in d for d in differences)
        assert any("projected_net_cost" in d for d in differences)

    def test_swapping_the_weekday_and_weekend_profiles_changes_the_plan(
        self, monkeypatch
    ):
        monkeypatch.setattr(rpb, "WEEKDAY_LOAD_KW", rpb.WEEKEND_LOAD_KW)
        monkeypatch.setattr(rpb, "WEEKEND_LOAD_KW", rpb.WEEKDAY_LOAD_KW)

        differences = _live_like_differences(PRECHARGE_DAY)

        assert any("] slot " in d and "consumption_kwh" in d for d in differences)
        assert any("projected_net_cost" in d for d in differences)

    def test_a_tracker_that_is_never_attached_changes_the_plan(self, monkeypatch):
        """The gap this arm closes: the deletion that detaches it is now seen."""
        monkeypatch.setattr(
            ComputationEngine, "set_solar_accuracy_tracker", lambda self, tracker: None
        )

        differences = _live_like_differences(PRECHARGE_DAY)

        assert any("coverage.solar_bias_slots" in d for d in differences)
        assert any("] slot " in d and "solar_kwh" in d for d in differences)

    def test_the_seeds_do_not_reach_the_other_arms(self, monkeypatch):
        monkeypatch.setattr(
            rpb,
            "SOLAR_BIAS",
            {
                weather: dict.fromkeys(by_time, 0.4)
                for weather, by_time in rpb.SOLAR_BIAS.items()
            },
        )
        monkeypatch.setattr(rpb, "WEEKDAY_LOAD_KW", rpb.WEEKEND_LOAD_KW)
        path = REPO_ROOT / PRECHARGE_DAY
        expected = json.loads(rpb.baseline_path(path).read_text())
        scenario = json.loads(path.read_text())

        for arm in ("captured", "strict"):
            assert rpb.replay(scenario, arm) == expected["arms"][arm], arm
