"""The negative-FIT recoverability floor uses measured solar accuracy (Issue #1103).

The floor is the lowest SOC from which future solar still reaches the demand
window target: ``target - recoverable``. ``recoverable`` assumed a fixed 80% of
forecast net solar would arrive. On 2026-10-09 measured accuracy was 57.6%, the
target-reach checks were already discounting by it (#816,
``check_global_solar_sufficiency``), and this floor was not — so it sat at the
5% minimum through the morning.

The fix: the share of forecast solar counted as recoverable is
``min(0.8, measured accuracy)``, read from the same
``OptimizerConfig.solar_forecast_accuracy`` the #816 checks read. Good or
unmeasured accuracy leaves the floor exactly where it was.

Every replay here runs with the materiality gate from the same issue switched
OFF, so what is measured is the discount alone.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.computation_engine import ComputationEngine
from custom_components.localshift.const import NEGATIVE_FIT_RECOVERY_SOLAR_FACTOR
from custom_components.localshift.engine.optimizer_dp import (
    DPPlanner,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)
from custom_components.localshift.forecast.solar_accuracy import (
    MIN_SOLAR_CORRECTION_SAMPLES,
    SolarAccuracyTracker,
    SolarBiasMetrics,
)
from custom_components.localshift.sensors.optimizer import OptimizerSummarySensor
from tests.test_scenarios import (
    create_mock_entry,
    create_mock_get_entity_id,
    create_mock_get_switch_state,
    setup_coordinator_data,
    setup_mock_hass,
)

AEDT = timezone(timedelta(hours=11))
REPLAY = Path(__file__).parent.parent / "fixtures" / "replay" / "2026-10-09-0807.json"
MIN_SPILL_COST_CONST = "custom_components.localshift.const.NEGATIVE_FIT_MIN_SPILL_COST"
MEASURED_10_09 = 57.6
CAPACITY_KWH = 13.5
TARGET_PCT = 95.0
FLOOR_PCT = 5.0


def _tracker(accuracy_pct: float, samples: int = 200) -> SolarAccuracyTracker:
    """A real tracker holding a measured accuracy and nothing else.

    No bias buckets, so the only thing it changes in a plan is the accuracy the
    planner reads from it.
    """
    with patch("custom_components.localshift.forecast.solar_accuracy.Store"):
        tracker = SolarAccuracyTracker(MagicMock(), "issue-1103")
    tracker._metrics = SolarBiasMetrics(accuracy=accuracy_pct, sample_count=samples)
    return tracker


# ---------------------------------------------------------------------------
# Synthetic horizon through the real DP entry point (DPPlanner.plan)
# ---------------------------------------------------------------------------


def _horizon() -> list[SlotContext]:
    """08:00-16:00 in 30-minute slots, demand window at 15:00.

    Modest solar (1.0 kWh net per half hour, 14 kWh forecast before the demand
    window against 12.15 kWh between floor and target) and four hours of -5c
    feed-in from 11:00, with good morning prices to sell into before it.
    """
    start = datetime(2026, 10, 9, 8, 0, tzinfo=AEDT)
    slots = []
    for i in range(16):
        when = start + timedelta(minutes=30 * i)
        hour = when.hour + when.minute / 60
        in_dw = hour >= 15
        slots.append(
            SlotContext(
                slot_index=i,
                timestamp_iso=when.isoformat(),
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=-0.05 if 11 <= hour < 15 else 0.08,
                solar_kwh=0.0 if in_dw else 1.3,
                consumption_kwh=0.3,
                is_demand_window_slot=in_dw,
                is_demand_window_entry=hour == 15,
            )
        )
    return slots


def _plan(tracker: SolarAccuracyTracker | None, initial_soc_pct: float = 90.0):
    inputs = OptimizerInputs(
        slots=_horizon(),
        initial_soc_pct=initial_soc_pct,
        solar_accuracy_tracker=tracker,
        config=OptimizerConfig(
            battery_capacity_kwh=CAPACITY_KWH,
            min_soc_pct=FLOOR_PCT,
            max_soc_pct=100.0,
            demand_window_target_soc_pct=TARGET_PCT,
            optimization_mode="self_consumption",
            export_price_margin=0.10,
            negative_fit_min_spill_cost=0.0,  # materiality gate off
        ),
        cycle_id="issue-1103-accuracy",
    )
    return DPPlanner().plan(inputs)


def _lowest_soc(result) -> float:
    return min(d.predicted_soc_pct for d in result.decisions)


def _exported_kwh(result) -> float:
    return sum(
        d.grid_export_kwh
        for d in result.decisions
        if d.action == PlannerAction.EXPORT_PROACTIVE
    )


def _expected_floor_now(solar_factor: float, efficiency: float) -> float:
    """``target - recoverable`` at slot 0, by hand: 13 later slots of 1.0 kWh
    net before the 15:00 deadline (slot 0's own solar is not counted)."""
    recoverable_kwh = 13 * 1.0 * efficiency * solar_factor
    floor_pct = TARGET_PCT - recoverable_kwh / CAPACITY_KWH * 100.0
    return max(FLOOR_PCT, floor_pct)


def test_low_measured_accuracy_raises_the_floor_the_plan_is_held_to():
    """The production path: DPPlanner.plan reads the tracker, and the floor
    the plan is solved against is the accuracy-discounted one."""
    result = _plan(_tracker(MEASURED_10_09))
    efficiency = OptimizerConfig().charge_efficiency

    assert result.success
    assert result.negative_fit_window_open is True
    assert result.negative_fit_recovery_solar_factor == pytest.approx(0.576)
    expected = _expected_floor_now(0.576, efficiency)
    assert expected > 40.0  # nowhere near the 5% minimum
    assert result.negative_fit_floor_now_pct == pytest.approx(expected, abs=0.01)
    # ...and it binds: the plan does not sell below what the discounted solar
    # can put back. One SOC bin of slack for the DP grid.
    assert _lowest_soc(result) >= expected - 1.5


def test_low_accuracy_plan_exports_less_than_the_fixed_haircut_plan():
    """Same horizon, the only difference is the measured accuracy."""
    unmeasured = _plan(None)
    measured = _plan(_tracker(MEASURED_10_09))

    assert _exported_kwh(unmeasured) > 0.0
    assert _exported_kwh(measured) < _exported_kwh(unmeasured)
    assert _lowest_soc(measured) > _lowest_soc(unmeasured) + 10.0
    assert measured.negative_fit_floor_now_pct > unmeasured.negative_fit_floor_now_pct


@pytest.mark.parametrize("accuracy_pct", [80.0, 90.0, 100.0])
def test_good_measured_accuracy_plans_exactly_as_before(accuracy_pct):
    """Accuracy at or above the old fixed 80% changes nothing: same floor,
    same actions, same export."""
    baseline = _plan(None)
    good = _plan(_tracker(accuracy_pct))

    assert good.negative_fit_recovery_solar_factor == NEGATIVE_FIT_RECOVERY_SOLAR_FACTOR
    assert good.negative_fit_floor_now_pct == baseline.negative_fit_floor_now_pct
    assert [d.action for d in good.decisions] == [d.action for d in baseline.decisions]
    assert [d.predicted_soc_pct for d in good.decisions] == [
        d.predicted_soc_pct for d in baseline.decisions
    ]


def test_no_tracker_keeps_the_fixed_haircut():
    """No measurement at all (standalone use, tests): the old 0.8, not 1.0."""
    result = _plan(None)
    efficiency = OptimizerConfig().charge_efficiency

    assert (
        result.negative_fit_recovery_solar_factor == NEGATIVE_FIT_RECOVERY_SOLAR_FACTOR
    )
    assert result.negative_fit_floor_now_pct == pytest.approx(
        _expected_floor_now(0.8, efficiency), abs=0.01
    )


def test_tracker_with_no_records_keeps_the_fixed_haircut():
    """A tracker that has recorded nothing reports its 100% default, exactly
    as it does to the #816 checks, so the floor is the old one."""
    empty = _tracker(100.0, samples=0)
    baseline = _plan(None)
    result = _plan(empty)

    assert (
        result.negative_fit_recovery_solar_factor == NEGATIVE_FIT_RECOVERY_SOLAR_FACTOR
    )
    assert [d.action for d in result.decisions] == [
        d.action for d in baseline.decisions
    ]


def test_under_sampled_accuracy_is_used_the_same_way_816_uses_it():
    """Pinned on purpose. ``get_forecast_accuracy`` does not wait for the
    minimum sample count, so the #816 target-reach checks act on an accuracy
    measured from a handful of periods. This floor reads the same number, so
    the two cannot disagree about how much solar to believe."""
    few = _tracker(MEASURED_10_09, samples=MIN_SOLAR_CORRECTION_SAMPLES - 1)
    assert few.has_sufficient_samples() is False

    result = _plan(few)

    assert result.forecast_accuracy == pytest.approx(0.576)  # what #816 used
    assert result.negative_fit_recovery_solar_factor == pytest.approx(0.576)


def test_no_window_reports_no_factor_or_floor():
    """Nothing to explain when avoidance is not in play."""
    inputs = OptimizerInputs(
        slots=[
            SlotContext(
                slot_index=i,
                timestamp_iso=(
                    datetime(2026, 10, 9, 8, 0, tzinfo=AEDT) + timedelta(minutes=30 * i)
                ).isoformat(),
                slot_interval_minutes=30,
                buy_price=0.30,
                sell_price=0.08,
                solar_kwh=1.3,
                consumption_kwh=0.3,
            )
            for i in range(8)
        ],
        initial_soc_pct=90.0,
        solar_accuracy_tracker=_tracker(MEASURED_10_09),
        config=OptimizerConfig(battery_capacity_kwh=CAPACITY_KWH),
        cycle_id="issue-1103-accuracy-none",
    )
    result = DPPlanner().plan(inputs)

    assert result.negative_fit_recovery_solar_factor is None
    assert result.negative_fit_floor_now_pct is None


# ---------------------------------------------------------------------------
# Replay of the 2026-10-09 08:07 inputs through the production cycle, with the
# materiality gate OFF so the discount is the only thing being measured
# ---------------------------------------------------------------------------


def _replay(accuracy_pct: float | None):
    """Run the captured cycle through ComputationEngine.compute_derived_values.

    Same harness as ``test_negative_fit_materiality._replay`` with two
    differences: the materiality threshold is patched to 0 (gate disabled), and
    the tracker is attached the way the coordinator attaches it
    (``set_solar_accuracy_tracker``). Returns the coordinator data and the
    avoidance context the planner solved against.
    """
    from custom_components.localshift.engine import negative_fit

    scenario = json.loads(REPLAY.read_text())
    payload = dict(scenario["input"])
    payload["adaptive_params"] = {}
    test_time = datetime.fromisoformat(payload["test_time"])

    data = setup_coordinator_data(payload)
    hass = setup_mock_hass(payload)
    forecast = payload["general_forecast"]
    first = datetime.fromisoformat(forecast[0]["start_time"])
    last = datetime.fromisoformat(forecast[-1]["start_time"])
    data.forecast_horizon_hours = (last - first).total_seconds() / 3600.0 + float(
        forecast[-1]["duration"]
    ) / 60.0

    engine = ComputationEngine(
        hass,
        create_mock_entry(dict(scenario["config_overrides"])),
        create_mock_get_entity_id(),
        create_mock_get_switch_state(scenario["switch_states"]),
    )
    if accuracy_pct is not None:
        engine.set_solar_accuracy_tracker(_tracker(accuracy_pct))

    seen: dict = {}
    real_assess = negative_fit.assess_negative_fit_avoidance

    def recording_assess(inputs):
        assessment = real_assess(inputs)
        seen["context"] = assessment.context
        seen["slots"] = inputs.slots
        return assessment

    fetcher = engine._history_fetcher
    with ExitStack() as stack:
        for p in (
            patch("homeassistant.util.dt.now", return_value=test_time),
            patch.object(engine, "_get_historical_hourly_averages", return_value={}),
            patch.object(fetcher, "_historical_load_cache", {}),
            patch.object(fetcher, "_historical_load_sample_counts", {}),
            patch.object(fetcher, "_historical_load_source", "none"),
            patch.object(fetcher, "_recent_load_1hr_kw", payload["load_power_kw"]),
            patch(MIN_SPILL_COST_CONST, 0.0),
            patch(
                "custom_components.localshift.engine.core"
                ".assess_negative_fit_avoidance",
                recording_assess,
            ),
        ):
            stack.enter_context(p)
        for _ in range(2):
            engine.compute_derived_values(data)
    return data, seen


@pytest.fixture(scope="module")
def unmeasured():
    """No tracker: the fixed 0.8, which is what the floor used on the day."""
    return _replay(None)


@pytest.fixture(scope="module")
def measured():
    """The accuracy the tracker reported on the morning of 2026-10-09."""
    return _replay(MEASURED_10_09)


def _exports(data) -> dict[str, dict]:
    return {
        d["timestamp_iso"][11:16]: d
        for d in data.optimizer_decisions
        if d["action"] == "export_proactive"
    }


def _floor_at(seen, hhmm: str) -> float:
    for idx, slot in enumerate(seen["slots"]):
        if slot.timestamp_iso[11:16] == hhmm:
            return seen["context"].recoverability_floor_pct_by_slot[idx]
    raise AssertionError(f"no slot at {hhmm}")


def _floor_leaves_minimum_at(seen) -> str:
    for idx, slot in enumerate(seen["slots"]):
        if seen["context"].recoverability_floor_pct_by_slot[idx] > FLOOR_PCT + 0.01:
            return slot.timestamp_iso[11:16]
    raise AssertionError("floor never leaves the minimum")


def test_replay_gate_is_off_and_the_window_opens(unmeasured, measured):
    """Guard: both arms reach the avoidance branch, so nothing below passes
    because the materiality gate quietly removed the context."""
    for data, seen in (unmeasured, measured):
        assert data.optimizer_summary["success"] is True
        assert data.optimizer_summary["negative_fit_spill_threshold"] == 0.0
        assert data.optimizer_summary["negative_fit_suppressed"] is False
        assert seen["context"] is not None


def test_replay_fixed_haircut_floor_sits_at_minimum_until_noon(unmeasured):
    """The 'before': what the issue describes."""
    data, seen = unmeasured
    assert data.optimizer_summary["negative_fit_recovery_solar_factor"] == 0.8
    assert _floor_leaves_minimum_at(seen) == "12:00"
    assert _floor_at(seen, "11:30") == pytest.approx(FLOOR_PCT)

    exports = _exports(data)
    assert len(exports) == 8
    # Sold down to the 5% minimum at 11:30, three and a half hours before the
    # demand window.
    assert exports["11:30"]["predicted_soc_pct"] == pytest.approx(FLOOR_PCT, abs=0.1)


def test_replay_measured_accuracy_lifts_the_floor_from_mid_morning(measured):
    data, seen = measured
    summary = data.optimizer_summary
    assert summary["negative_fit_recovery_solar_factor"] == pytest.approx(0.576)
    assert _floor_leaves_minimum_at(seen) == "10:30"
    assert _floor_at(seen, "11:30") > 25.0
    assert _floor_at(seen, "12:00") > 35.0

    exports = _exports(data)
    # The late-morning sell-down to 5% is gone; nothing is exported after 11:00.
    assert "11:30" not in exports
    assert max(exports) == "11:00"
    # Every remaining export leaves the battery at or above its slot's floor.
    for hhmm, decision in exports.items():
        assert decision["predicted_soc_pct"] >= _floor_at(seen, hhmm) - 0.01
    assert exports["11:00"]["predicted_soc_pct"] > 20.0


def test_replay_measured_accuracy_exports_less_battery(unmeasured, measured):
    def kwh(pair) -> float:
        return sum(d["grid_export_kwh"] for d in _exports(pair[0]).values())

    assert len(_exports(measured[0])) < len(_exports(unmeasured[0]))
    assert kwh(measured) < kwh(unmeasured) - 3.0
    for data, _ in (unmeasured, measured):
        assert data.optimizer_precharge_backstop_active is False
        assert not any(d.get("grid_charge") for d in data.optimizer_decisions)
        assert data.optimizer_summary["terminal_shortfall_pct"] == 0.0


def test_replay_discount_alone_does_not_stop_the_early_morning_exports(measured):
    """Pinned so nobody reads this slice as the fix for the 10-09 morning.

    At 08:07 the forecast held far more net solar before 15:00 than the battery
    needs, so 57.6% of it still covers the whole climb from 5% to 95% and the
    floor at the start of the plan is the 5% minimum either way. The 08:20 to
    08:45 exports to the minimum are untouched. Stopping those is the
    materiality gate's job (or a bound on export volume), not the discount's.
    """
    data, _ = measured
    assert data.optimizer_summary["negative_fit_floor_now_pct"] == FLOOR_PCT
    exports = _exports(data)
    assert {"08:20", "08:25", "08:35", "08:45"} <= set(exports)
    assert exports["08:35"]["predicted_soc_pct"] == pytest.approx(FLOOR_PCT, abs=0.1)


def test_replay_good_measured_accuracy_is_the_same_plan(unmeasured):
    """The same morning with a forecast that had been measuring up: nothing
    about the plan changes, slot for slot."""
    good, good_seen = _replay(90.0)
    base, base_seen = unmeasured

    assert good.optimizer_summary["negative_fit_recovery_solar_factor"] == 0.8
    assert (
        good_seen["context"].recoverability_floor_pct_by_slot
        == base_seen["context"].recoverability_floor_pct_by_slot
    )
    keys = (
        "timestamp_iso",
        "action",
        "reason_code",
        "predicted_soc_pct",
        "grid_export_kwh",
        "grid_import_kwh",
    )
    assert [[d[k] for k in keys] for d in good.optimizer_decisions] == [
        [d[k] for k in keys] for d in base.optimizer_decisions
    ]
    assert (
        good.optimizer_summary["projected_net_cost"]
        == base.optimizer_summary["projected_net_cost"]
    )


def test_replay_publishes_the_factor_and_floor_on_the_sensor(measured):
    data, _ = measured
    coordinator = MagicMock()
    coordinator.data = data
    sensor = OptimizerSummarySensor.__new__(OptimizerSummarySensor)
    sensor.coordinator = coordinator
    attrs = sensor.extra_state_attributes

    assert attrs["negative_fit_recovery_solar_factor"] == pytest.approx(0.576)
    assert attrs["negative_fit_floor_now_pct"] == FLOOR_PCT
