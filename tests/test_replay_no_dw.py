"""The replay harness's ``block`` arm and slice 1 gate (docs/PRICE_BLOCK_TARGET.md; #1110).

``scripts/replay_no_dw.py`` is the acceptance gate for the price block, so the
arm it runs and the verdict it prints are tested like product code: a gate that
quietly ran the wrong configuration would pass anything.

The engine-driven tests use the two captures that are committed
(``simulations/replay-nodw/2026-09-07.json``, the solar-deficit day, and
``simulations/replay/2026-09-03.json``, a day solar filled the battery), so they
run from a clean checkout.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from scripts import replay_no_dw as harness

REPO = Path(__file__).resolve().parents[1]
CAPTURE_0907 = REPO / "simulations" / "replay-nodw" / "2026-09-07.json"
CAPTURE_0903 = REPO / "simulations" / "replay" / "2026-09-03.json"

# The arms as they stood before the block arm was added. Existing arms are the
# baseline every earlier result in the README was measured on.
ARMS_BEFORE_BLOCK: dict[str, dict[str, Any]] = {
    "dw": {"no_dw": False, "overrides": {}},
    "no_dw": {"no_dw": True, "overrides": {}},
    "no_dw_p25": {"no_dw": True, "overrides": {"cheap_price_percentile": 25}},
    "no_dw_p100": {"no_dw": True, "overrides": {"cheap_price_percentile": 100}},
    "no_dw_mcs0": {"no_dw": True, "overrides": {"min_cycle_saving": 0}},
    "no_dw_open": {
        "no_dw": True,
        "overrides": {"cheap_price_percentile": 100, "min_cycle_saving": 0},
    },
    "no_dw_p100_mcs10": {
        "no_dw": True,
        "overrides": {"cheap_price_percentile": 100, "min_cycle_saving": 0.10},
    },
    "gateless_mcs25": {"no_dw": True, "gateless": True, "overrides": {}},
    "gateless_mcs10": {
        "no_dw": True,
        "gateless": True,
        "overrides": {"min_cycle_saving": 0.10},
    },
    "gateless_mcs0": {
        "no_dw": True,
        "gateless": True,
        "overrides": {"min_cycle_saving": 0.0},
    },
}


def _local(stamp: str | None) -> str | None:
    return datetime.fromisoformat(stamp).strftime("%H:%M") if stamp else None


def _plan(out: dict[str, Any]) -> list[tuple[Any, ...]]:
    """What the plan does, slot by slot, without the reason text."""
    return [(s["t"], s["act"], s["soc"], s["imp"], s["exp"]) for s in out["slots"]]


# ---------------------------------------------------------------------------
# Arm definitions
# ---------------------------------------------------------------------------


def test_block_arm_is_live_config_plus_the_price_block() -> None:
    arm = harness.ARMS["block"]

    assert arm["no_dw"] is True  # no clock demand window
    assert arm["switches"] == {"price_block_target": True}
    assert arm["overrides"] == {"block_min_spread": 0.08, "min_cycle_saving": 0.25}
    assert not arm.get("gateless")


def test_existing_arms_are_unchanged() -> None:
    others = {name: arm for name, arm in harness.ARMS.items() if name != "block"}

    assert others == ARMS_BEFORE_BLOCK


def test_live_config_does_not_turn_the_block_on_for_other_arms() -> None:
    assert "price_block_target" not in harness.LIVE_SWITCHES
    assert "block_min_spread" not in harness.LIVE_OVERRIDES


# ---------------------------------------------------------------------------
# 2026-09-07: the solar-deficit day
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def day_0907() -> dict[str, dict[str, Any]]:
    scenario = json.loads(CAPTURE_0907.read_text())
    return {
        name: harness.run_arm(copy.deepcopy(scenario), harness.ARMS[name])
        for name in ("dw", "no_dw", "block")
    }


def test_0907_dw_and_no_dw_arms_still_report_the_readme_numbers(
    day_0907: dict[str, dict[str, Any]],
) -> None:
    """The baseline the block is gated against has not moved."""
    assert day_0907["dw"]["projected_net_cost"] == pytest.approx(6.054, abs=0.001)
    assert day_0907["no_dw"]["projected_net_cost"] == pytest.approx(6.934, abs=0.001)


def test_0907_only_the_block_arm_has_a_block(
    day_0907: dict[str, dict[str, Any]],
) -> None:
    assert day_0907["block"]["target_block_active"] is True
    assert day_0907["dw"]["target_block_active"] is False
    assert day_0907["no_dw"]["target_block_active"] is False
    assert day_0907["dw"]["target_block_entry"] is None


def test_0907_block_runs_1630_to_midnight(
    day_0907: dict[str, dict[str, Any]],
) -> None:
    block = day_0907["block"]

    assert _local(block["target_block_entry"]) == "16:30"
    # The telemetry reports when the last slot (00:00) ends.
    assert _local(block["target_block_end"]) == "00:30"
    # The evening needs more than one battery, so the clamp pins the target.
    assert block["target_block_target_pct"] == pytest.approx(95.0)
    assert block["target_block_needed_kwh"] > 13.5


def test_0907_block_costs_no_more_than_dw(
    day_0907: dict[str, dict[str, Any]],
) -> None:
    block = day_0907["block"]["projected_net_cost"]

    assert block <= day_0907["dw"]["projected_net_cost"]
    assert block < day_0907["no_dw"]["projected_net_cost"]


def test_0907_block_funds_the_evening_before_it_starts(
    day_0907: dict[str, dict[str, Any]],
) -> None:
    block = day_0907["block"]

    assert block["charge_kwh"] > 0
    charge_times = [
        s["t"] for s in block["slots"] if str(s["act"]).startswith("charge_grid")
    ]
    assert charge_times
    assert max(charge_times) < "16:30"
    assert block["slot0_action"] == block["slots"][0]["act"]


def test_0907_no_arm_grid_charges_overnight(
    day_0907: dict[str, dict[str, Any]],
) -> None:
    """The #800 sawtooth check: nothing bought between 21:00 and 06:00."""
    for name, out in day_0907.items():
        assert out["overnight_charge_kwh"] == 0, name
        assert out["overnight_charge_slots"] == 0, name


# ---------------------------------------------------------------------------
# 2026-09-03: solar fills the battery
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def day_0903() -> dict[str, dict[str, Any]]:
    scenario = json.loads(CAPTURE_0903.read_text())
    return {
        name: harness.run_arm(copy.deepcopy(scenario), harness.ARMS[name])
        for name in ("dw", "block")
    }


def test_0903_block_plan_is_identical_to_dw(
    day_0903: dict[str, dict[str, Any]],
) -> None:
    dw, block = day_0903["dw"], day_0903["block"]

    assert block["projected_net_cost"] == dw["projected_net_cost"]
    assert _plan(block) == _plan(dw)
    assert block["overnight_charge_kwh"] == 0


# ---------------------------------------------------------------------------
# Overnight tally
# ---------------------------------------------------------------------------


def _decision(stamp: str, *, charge: bool, imp: float = 0.0) -> dict[str, Any]:
    return {
        "timestamp_iso": stamp,
        "action": "charge_grid_normal" if charge else "hold",
        "grid_charge": charge,
        "grid_import_kwh": imp,
        "buy_price": 0.1,
        "predicted_soc_pct": 50.0,
    }


@pytest.mark.parametrize(
    ("stamp", "overnight"),
    [
        ("2026-09-07T14:30:00+10:00", False),
        ("2026-09-07T20:30:00+10:00", False),
        ("2026-09-07T21:00:00+10:00", True),
        ("2026-09-08T00:00:00+10:00", True),
        ("2026-09-08T05:30:00+10:00", True),
        ("2026-09-08T06:00:00+10:00", False),
    ],
)
def test_overnight_is_2100_to_0600(stamp: str, overnight: bool) -> None:
    tally = harness._Tally()
    tally.add(_decision("2026-09-07T09:00:00+10:00", charge=False))
    tally.add(_decision(stamp, charge=True, imp=1.5))

    assert tally.overnight_charge_kwh == (1.5 if overnight else 0.0)
    assert tally.overnight_charge_slots == (1 if overnight else 0)


def test_overnight_import_without_grid_charge_is_not_counted() -> None:
    """Load served from the grid overnight is not the battery cycling."""
    tally = harness._Tally()
    tally.add(_decision("2026-09-07T23:00:00+10:00", charge=False, imp=2.0))

    assert tally.overnight_charge_kwh == 0.0


# ---------------------------------------------------------------------------
# Delta output
# ---------------------------------------------------------------------------


def test_delta_pairs_keep_the_baseline_comparisons() -> None:
    assert harness._delta_pairs(["dw", "no_dw", "no_dw_p25"]) == [
        ("dw", "no_dw"),
        ("dw", "no_dw_p25"),
    ]


def test_delta_pairs_report_block_minus_dw_once_when_dw_is_baseline() -> None:
    assert harness._delta_pairs(["dw", "no_dw", "block"]) == [
        ("dw", "no_dw"),
        ("dw", "block"),
    ]


def test_delta_pairs_add_block_minus_dw_when_dw_is_not_the_baseline() -> None:
    assert harness._delta_pairs(["no_dw", "dw", "block"]) == [
        ("no_dw", "dw"),
        ("no_dw", "block"),
        ("dw", "block"),
    ]


def test_delta_pairs_block_first_still_reports_block_minus_dw() -> None:
    assert ("dw", "block") in harness._delta_pairs(["block", "dw"])


def test_print_deltas_names_block_minus_dw(capsys: pytest.CaptureFixture[str]) -> None:
    results = {
        "2026-09-07": {
            "dw": {"projected_net_cost": 6.054, "charge_kwh": 13.18},
            "block": {"projected_net_cost": 6.000, "charge_kwh": 13.50},
        }
    }

    harness._print_deltas(results, "dw", "block")

    out = capsys.readouterr().out
    assert "block minus dw, per day" in out
    assert "2026-09-07: cost -0.054" in out


# ---------------------------------------------------------------------------
# Gate verdict
# ---------------------------------------------------------------------------


def _arm(cost: float, plan: str = "..C..", overnight: float = 0.0) -> dict[str, Any]:
    return {
        "projected_net_cost": cost,
        "overnight_charge_kwh": overnight,
        "slots": [
            {"t": f"{i:02d}:00", "act": a, "soc": 50.0, "imp": 0.0, "exp": 0.0}
            for i, a in enumerate(plan)
        ],
    }


def test_gate_row_identical_plan() -> None:
    row = harness.gate_row("d", {"dw": _arm(0.31), "block": _arm(0.31)})

    assert row["relation"] == "identical"
    assert row["ok"] is True


def test_gate_row_same_cost_different_plan_is_not_identical() -> None:
    row = harness.gate_row("d", {"dw": _arm(0.31), "block": _arm(0.31, plan=".C...")})

    assert row["relation"] == "equal cost, different plan"
    assert row["ok"] is True


def test_gate_row_block_cheaper() -> None:
    row = harness.gate_row("d", {"dw": _arm(6.054), "block": _arm(6.0, plan="C....")})

    assert row["relation"] == "below"
    assert row["delta"] == pytest.approx(-0.054)
    assert row["ok"] is True


def test_gate_row_block_dearer_fails() -> None:
    row = harness.gate_row("d", {"dw": _arm(6.054), "block": _arm(6.2, plan="C....")})

    assert row["relation"] == "above"
    assert row["ok"] is False


def test_gate_row_overnight_charge_fails_even_when_cheaper() -> None:
    row = harness.gate_row(
        "d", {"dw": _arm(6.054), "block": _arm(6.0, plan="C....", overnight=1.2)}
    )

    assert row["overnight_charge_kwh"] == 1.2
    assert row["ok"] is False


def test_gate_row_errored_arm_fails() -> None:
    row = harness.gate_row("d", {"dw": _arm(0.31), "block": {"error": "boom"}})

    assert row["relation"] == "incomparable"
    assert row["ok"] is False


def test_gate_rows_skip_days_without_both_arms() -> None:
    results = {
        "a": {"dw": _arm(0.1), "block": _arm(0.1)},
        "b": {"dw": _arm(0.1), "no_dw": _arm(0.1)},
    }

    assert [row["day"] for row in harness.gate_rows(results)] == ["a"]


def test_print_gate_verdict(capsys: pytest.CaptureFixture[str]) -> None:
    good = {"a": {"dw": _arm(0.1), "block": _arm(0.1)}}
    bad = {"a": {"dw": _arm(0.1), "block": _arm(0.2, plan="C....")}}

    assert harness._print_gate(good) is True
    assert "slice 1 gate: PASS" in capsys.readouterr().out
    assert harness._print_gate(bad) is False
    assert "slice 1 gate: FAIL" in capsys.readouterr().out


def test_print_gate_is_silent_without_the_block_arm(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert harness._print_gate({"a": {"dw": _arm(0.1)}}) is None
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# Flap test (#1111): the same day captured at several hours
# ---------------------------------------------------------------------------

DAY = "2026-09-07"


def _point(clock: str, slot0: str, entry: str | None) -> dict[str, Any]:
    return {
        "at": f"{DAY}T{clock}:00+10:00",
        "slot0_action": slot0,
        "entry": f"{DAY}T{entry}:00+10:00" if entry else None,
    }


def test_flap_steady_entry_and_action_passes() -> None:
    verdict = harness.flap_verdict([
        _point("09:00", "hold", "16:30"),
        _point("11:00", "charge_grid_boost", "16:30"),
        _point("13:00", "charge_grid_boost", "16:30"),
        _point("14:30", "hold", "16:30"),
    ])

    assert verdict["verdict"] == "PASS"
    assert verdict["reasons"] == []
    assert [p["entry_moved_min"] for p in verdict["pairs"]] == [0, 0, 0]
    assert [p["action_changed"] for p in verdict["pairs"]] == [True, False, True]


def test_flap_entry_moving_one_slot_passes() -> None:
    verdict = harness.flap_verdict([
        _point("09:00", "hold", "16:30"),
        _point("11:00", "hold", "17:00"),
        _point("13:00", "hold", "16:30"),
    ])

    assert verdict["verdict"] == "PASS"
    assert [p["entry_moved_min"] for p in verdict["pairs"]] == [30, -30]
    assert all(p["entry_ok"] for p in verdict["pairs"])


def test_flap_entry_moving_two_slots_fails() -> None:
    verdict = harness.flap_verdict([
        _point("09:00", "hold", "16:30"),
        _point("11:00", "hold", "17:30"),
    ])

    assert verdict["verdict"] == "FAIL"
    assert verdict["pairs"][0]["entry_moved_min"] == 60
    assert verdict["pairs"][0]["entry_ok"] is False
    assert any("09:00 -> 11:00" in reason for reason in verdict["reasons"])


def test_flap_block_disappearing_fails() -> None:
    verdict = harness.flap_verdict([
        _point("09:00", "hold", "16:30"),
        _point("11:00", "hold", None),
    ])

    assert verdict["verdict"] == "FAIL"
    assert verdict["pairs"][0]["entry_moved_min"] is None
    assert verdict["pairs"][0]["entry_ok"] is False


def test_flap_charge_hold_charge_fails() -> None:
    verdict = harness.flap_verdict([
        _point("09:00", "charge_grid_normal", "16:30"),
        _point("11:00", "hold", "16:30"),
        _point("13:00", "charge_grid_boost", "16:30"),
        _point("14:30", "hold", "16:30"),
    ])

    assert verdict["verdict"] == "FAIL"
    assert verdict["action_alternates"] is True
    assert any("charge / hold / charge" in reason for reason in verdict["reasons"])


def test_flap_charge_resuming_after_two_holds_still_fails() -> None:
    verdict = harness.flap_verdict([
        _point("09:00", "charge_grid_normal", "16:30"),
        _point("11:00", "hold", "16:30"),
        _point("13:00", "hold_strict", "16:30"),
        _point("14:30", "charge_grid_normal", "16:30"),
    ])

    assert verdict["action_alternates"] is True
    assert verdict["verdict"] == "FAIL"


def test_flap_hold_charge_hold_is_a_finished_charge_not_a_flap() -> None:
    verdict = harness.flap_verdict([
        _point("09:00", "hold", "16:30"),
        _point("11:00", "charge_grid_boost", "16:30"),
        _point("13:00", "hold", "16:30"),
    ])

    assert verdict["action_alternates"] is False
    assert verdict["verdict"] == "PASS"


def test_flap_normal_to_boost_is_one_charge_run() -> None:
    verdict = harness.flap_verdict([
        _point("09:00", "charge_grid_normal", "16:30"),
        _point("11:00", "charge_grid_boost", "16:30"),
        _point("13:00", "hold", "16:30"),
    ])

    assert verdict["action_alternates"] is False
    assert verdict["pairs"][0]["action_changed"] is True


def test_flap_with_no_block_in_any_capture_is_not_a_pass() -> None:
    """Nothing to flap means nothing was tested."""
    verdict = harness.flap_verdict([
        _point("09:00", "hold", None),
        _point("11:00", "hold", None),
    ])

    assert verdict["verdict"] == "INCONCLUSIVE"


def test_flap_needs_two_captures() -> None:
    verdict = harness.flap_verdict([_point("09:00", "hold", "16:30")])

    assert verdict["verdict"] == "INCONCLUSIVE"
    assert verdict["pairs"] == []


def test_flap_points_are_ordered_by_capture_time() -> None:
    verdict = harness.flap_verdict([
        _point("13:00", "hold", "17:30"),
        _point("09:00", "hold", "16:30"),
        _point("11:00", "hold", "17:00"),
    ])

    assert [(p["from"][11:16], p["to"][11:16]) for p in verdict["pairs"]] == [
        ("09:00", "11:00"),
        ("11:00", "13:00"),
    ]
    assert verdict["verdict"] == "PASS"


def _capture_at(scenario: dict[str, Any], clock: str) -> dict[str, Any]:
    """The 2026-09-07 capture as it would look taken later the same day: the
    price forecast starts at ``clock`` and the earlier slots are gone."""
    later = copy.deepcopy(scenario)
    start = datetime.fromisoformat(f"{DAY}T{clock}:00+10:00")
    later["input"]["test_time"] = start.isoformat()
    for key in ("general_forecast", "feed_in_forecast"):
        later["input"][key] = [
            row
            for row in later["input"][key]
            if datetime.fromisoformat(row["start_time"]) >= start
        ]
    return later


def test_load_flap_days_groups_by_day_in_time_order(tmp_path: Path) -> None:
    scenario = json.loads(CAPTURE_0907.read_text())
    other = _capture_at(scenario, "09:00")
    other["input"]["test_time"] = "2026-09-08T09:00:00+10:00"
    # Written out of order, and named so that a name sort would get it wrong.
    (tmp_path / "b.json").write_text(json.dumps(_capture_at(scenario, "09:00")))
    (tmp_path / "a.json").write_text(json.dumps(_capture_at(scenario, "13:00")))
    (tmp_path / "c.json").write_text(json.dumps(other))

    days = harness.load_flap_days(tmp_path)

    assert list(days) == ["2026-09-07", "2026-09-08"]
    assert [name for name, _ in days["2026-09-07"]] == ["b.json", "a.json"]
    assert [name for name, _ in days["2026-09-08"]] == ["c.json"]


def test_load_flap_days_empty_directory(tmp_path: Path) -> None:
    assert harness.load_flap_days(tmp_path) == {}


@pytest.fixture(scope="module")
def flap_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    scenario = json.loads(CAPTURE_0907.read_text())
    out = tmp_path_factory.mktemp("flap")
    (out / "2026-09-07T0900.json").write_text(json.dumps(scenario))
    (out / "2026-09-07T1300.json").write_text(
        json.dumps(_capture_at(scenario, "13:00"))
    )
    return out


def test_run_flap_drives_the_block_arm_on_each_capture(flap_dir: Path) -> None:
    captures = harness.load_flap_days(flap_dir)["2026-09-07"]

    points = harness.flap_points(captures)

    assert [p["capture"] for p in points] == [
        "2026-09-07T0900.json",
        "2026-09-07T1300.json",
    ]
    assert [p["at"][11:16] for p in points] == ["09:00", "13:00"]
    # The first capture is the committed one, run as the block arm runs it.
    assert _local(points[0]["entry"]) == "16:30"
    assert points[0]["slot0_action"] == "hold"
    for point in points:
        assert point["entry"] is not None
        assert point["slot0_action"] in harness.ACTION_LETTER
        assert point["cost"] is not None
        # Carried forward from the capture before it, as live carries it.
        assert "entry_carried" in point
    assert points[0]["entry_carried"] == points[0]["entry"]


def test_run_flap_prints_a_verdict_per_day(
    flap_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    passed = harness.run_flap(flap_dir)

    out = capsys.readouterr().out
    assert "flap test 2026-09-07" in out
    assert "09:00 -> 13:00" in out
    expected = harness.flap_verdict(
        harness.flap_points(harness.load_flap_days(flap_dir)["2026-09-07"])
    )["verdict"]
    assert f"flap test 2026-09-07: {expected}" in out
    assert passed is (expected == "PASS")


def test_run_flap_with_no_captures_is_not_a_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert harness.run_flap(tmp_path) is False
    assert "flap captures not present" in capsys.readouterr().out


def test_flap_argument_defaults_to_the_flap_directory() -> None:
    assert harness._parse_args(["--flap"]).flap == "simulations/replay-nodw-flap"
    assert harness._parse_args(["--flap", "somewhere"]).flap == "somewhere"
    assert harness._parse_args([]).flap is None


def test_committed_0907_flap_captures_pass() -> None:
    """The reference solar-deficit day, captured at 09:00, 11:00, 13:00, 14:30."""
    days = harness.load_flap_days(REPO / "simulations" / "replay-nodw-flap")
    captures = days["2026-09-07"]

    points = harness.flap_points(captures)

    assert [p["at"][11:16] for p in points] == ["09:00", "11:00", "13:00", "14:30"]
    assert [_local(p["entry"]) for p in points] == ["16:30"] * 4
    assert harness.flap_verdict(points)["verdict"] == "PASS"


def test_committed_0907_0900_flap_capture_is_the_gate_capture() -> None:
    flap = REPO / "simulations" / "replay-nodw-flap" / "2026-09-07T0900.json"

    assert flap.read_bytes() == CAPTURE_0907.read_bytes()


# ---------------------------------------------------------------------------
# Flap test with the block's state carried between captures (#1114)
# ---------------------------------------------------------------------------


# With the block's state carried between captures, as measured on 2026-10-09.
# Two of the five still fail: 2026-09-21 on the slot-0 action (the entry itself
# holds at 17:00), 2026-09-23 because the block is dropped once its disappearance
# has outlasted the dwell. See simulations/replay-nodw/README.md.
COMMITTED_FLAP_VERDICTS = {
    "2026-09-07": "PASS",
    "2026-09-11": "PASS",
    "2026-09-21": "FAIL",
    "2026-09-23": "FAIL",
    "2026-09-26": "PASS",
}


def _morning_gone(scenario: dict[str, Any], clock: str) -> dict[str, Any]:
    """A later capture of 2026-09-07 on which the cheap morning never came back.

    Every price before the evening is 12c, so against the horizon alone the
    17-19c evening is under the 8c spread and a cold detector finds no block.
    That is the #1111 failure: the trough has rolled off the horizon.
    """
    later = _capture_at(scenario, clock)
    evening = datetime.fromisoformat(f"{DAY}T16:30:00+10:00")
    for row in later["input"]["general_forecast"]:
        if datetime.fromisoformat(row["start_time"]) < evening:
            row["per_kwh"] = 0.12
    later["input"]["general_price"] = 0.12
    return later


@pytest.fixture(scope="module")
def rolled_off_points() -> list[dict[str, Any]]:
    scenario = json.loads(CAPTURE_0907.read_text())
    return harness.flap_points([
        ("0900.json", scenario),
        ("1300.json", _morning_gone(scenario, "13:00")),
    ])


def test_past_prices_are_the_entries_that_start_before_the_capture() -> None:
    scenario = json.loads(CAPTURE_0907.read_text())
    captured_at = datetime.fromisoformat(scenario["input"]["test_time"])

    past = harness.past_prices(scenario)

    # The capture records the interval that had just ended, and nothing earlier.
    assert [stamp for stamp, _ in past] == ["2026-09-07T08:55:00+10:00"]
    assert all(datetime.fromisoformat(stamp) < captured_at for stamp, _ in past)
    assert past[0][1] == scenario["input"]["general_forecast"][0]["per_kwh"]


def test_past_prices_of_a_capture_with_none() -> None:
    scenario = _capture_at(json.loads(CAPTURE_0907.read_text()), "13:00")

    assert harness.past_prices(scenario) == []


def test_past_prices_are_not_remembered_while_the_memory_is_suspended() -> None:
    """Inside a block live remembers nothing, so the replay must not either."""
    scenario = json.loads(CAPTURE_0907.read_text())
    suspended = {"target_block_trough_resume_iso": "2026-09-07T21:00:00+10:00"}

    assert harness._with_past_prices(suspended, scenario) == suspended
    assert harness._with_past_prices({}, scenario)["target_block_trough"] == [
        tuple(harness.past_prices(scenario)[0])
    ]


def test_run_arm_reports_the_state_it_leaves_behind() -> None:
    scenario = json.loads(CAPTURE_0907.read_text())

    out = harness.run_arm(scenario, harness.ARMS["block"])

    state = out["price_block_state"]
    assert set(state) == set(harness.PRICE_BLOCK_STATE_FIELDS)
    assert state["target_block_entry_iso"] == out["target_block_entry"]
    assert state["target_block_settled"] is True
    assert out["target_block_trough_price"] == out["slots"][0]["buy"]
    assert out["target_block_pending_change"] is None


def test_cold_start_loses_the_block_and_carried_state_keeps_it(
    rolled_off_points: list[dict[str, Any]],
) -> None:
    first, later = rolled_off_points

    assert _local(first["entry"]) == "16:30"
    # Cold: each capture alone, as #1111 measured it.
    assert later["entry"] is None
    # Carried: the 09:00 trough is remembered, as live would remember it.
    assert _local(later["entry_carried"]) == "16:30"
    assert later["trough_price"] == pytest.approx(first["trough_price"])
    assert later["trough_price"] < 0.08


def test_first_capture_trough_is_seeded_from_its_own_past_price() -> None:
    """The one past price a capture holds is the interval that had just ended."""
    scenario = json.loads(CAPTURE_0907.read_text())
    cheap = copy.deepcopy(scenario)
    cheap["input"]["general_forecast"][0]["per_kwh"] = 0.01

    plain = harness.flap_points([("a.json", scenario)])[0]
    seeded = harness.flap_points([("a.json", cheap)])[0]

    assert plain["trough_price"] == pytest.approx(
        min(harness.past_prices(scenario)[0][1], plain["slot0_price"])
    )
    assert seeded["trough_price"] == pytest.approx(0.01)
    assert seeded["trough_at"] == "2026-09-07T08:55:00+10:00"


def test_carried_verdict_is_read_from_the_carried_columns(
    rolled_off_points: list[dict[str, Any]],
) -> None:
    assert harness.flap_verdict(rolled_off_points)["verdict"] == "FAIL"
    assert harness.flap_carried_verdict(rolled_off_points)["verdict"] == "PASS"


def test_flap_points_report_the_dw_arm_alongside(
    rolled_off_points: list[dict[str, Any]],
) -> None:
    for point in rolled_off_points:
        assert point["dw_cost"] is not None
        assert point["cost_carried"] is not None
        assert point["charge_kwh_carried"] is not None


def test_run_flap_verdict_is_the_carried_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = json.loads(CAPTURE_0907.read_text())
    (tmp_path / "2026-09-07T0900.json").write_text(json.dumps(scenario))
    (tmp_path / "2026-09-07T1300.json").write_text(
        json.dumps(_morning_gone(scenario, "13:00"))
    )

    passed = harness.run_flap(tmp_path)

    out = capsys.readouterr().out
    assert passed is True
    assert "flap test 2026-09-07: PASS" in out
    assert "cold start, each capture alone: FAIL" in out
    assert "block appeared or disappeared" in out


def test_committed_flap_captures_verdicts_with_state_carried() -> None:
    """The five solar-deficit days of #1111, re-run for #1114.

    Pinned as measured, FAIL included: this is the record the README quotes.
    """
    days = harness.load_flap_days(REPO / "simulations" / "replay-nodw-flap")

    verdicts = {
        day: harness.flap_carried_verdict(harness.flap_points(captures))["verdict"]
        for day, captures in days.items()
    }

    assert verdicts == COMMITTED_FLAP_VERDICTS


def test_flap_day_prints_the_dw_arm_slot0_as_a_control(
    rolled_off_points: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    """Whether the clock-window arm also goes charge / hold / charge on these
    captures says whether an alternation is the block's doing at all."""
    for point in rolled_off_points:
        assert point["slot0_action_dw"] in harness.ACTION_LETTER

    harness._print_flap_day(DAY, rolled_off_points)

    out = capsys.readouterr().out
    expected = harness._charge_resumes([
        p["slot0_action_dw"] for p in rolled_off_points
    ])
    assert (
        f"control, dw arm slot-0 goes charge / hold / charge: "
        f"{'yes' if expected else 'no'}" in out
    )
