"""Negative-FIT avoidance needs a materiality threshold (Issue #1103).

2026-10-09 08:07: the only non-positive feed-in slots before the 15:00 demand
window were 13:30 (-0.04c) and 14:00 (-0.13c) — well under a cent of exposure.
That opened the avoidance window, which drops the normal export value test, and
the plan exported the battery to its floor through the morning at 1 to 7c. Live,
that became eight export episodes and the pre-charge runway backstop firing at
12:50.

The gate: no avoidance context unless the spill the window would actually cost
(surplus that cannot be stored, times the magnitude of the non-positive sell
price) reaches ``min_cycle_saving`` dollars.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.computation_engine import ComputationEngine
from custom_components.localshift.engine.negative_fit import (
    derive_negative_fit_avoidance_context,
)
from custom_components.localshift.engine.optimizer_dp import (
    DPPlanner,
    OptimizerConfig,
    OptimizerInputs,
    PlannerAction,
    SlotContext,
)
from custom_components.localshift.engine.types import (
    PlannerReasonCode as ReasonCode,
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
LIVE_MIN_CYCLE_SAVING = 0.25


# ---------------------------------------------------------------------------
# Synthetic horizons through the real DP entry point (DPPlanner.solve)
# ---------------------------------------------------------------------------


def _horizon(negative_prices: dict[float, float]) -> list[SlotContext]:
    """08:00-16:00 in 30-minute slots: sunny morning, demand window at 15:00.

    ``negative_prices`` maps an hour-of-day to its sell price; every other
    pre-noon slot sells at a small positive price, as 2026-10-09 did.
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
                buy_price=0.12,
                sell_price=negative_prices.get(hour, 0.03),
                solar_kwh=0.0 if in_dw else 2.5,
                consumption_kwh=0.3,
                is_demand_window_slot=in_dw,
                is_demand_window_entry=hour == 15,
            )
        )
    return slots


def _inputs(slots: list[SlotContext], *, min_cycle_saving: float) -> OptimizerInputs:
    return OptimizerInputs(
        slots=slots,
        initial_soc_pct=60.0,
        config=OptimizerConfig(
            battery_capacity_kwh=13.5,
            min_soc_pct=5.0,
            max_soc_pct=100.0,
            demand_window_target_soc_pct=95.0,
            optimization_mode="self_consumption",
            export_price_margin=0.10,
            min_cycle_saving=min_cycle_saving,
        ),
        cycle_id="issue-1103",
    )


# Two half-hour slots a fraction of a cent below zero: 2 x 2.2 kWh of surplus
# at -0.04c and -0.13c is under half a cent.
TRIVIAL = {13.5: -0.0004, 14.0: -0.0013}
# Four hours at -5c with the battery filling before it starts: dollars.
MATERIAL = {h / 2: -0.05 for h in range(22, 30)}  # 11:00 through 14:30


def _avoidance_exports(result) -> list[str]:
    return [
        d.timestamp_iso
        for d in result.decisions
        if d.reason_code == ReasonCode.NEGATIVE_FIT_AVOIDANCE
        or d.action == PlannerAction.EXPORT_PROACTIVE
    ]


def test_trivial_negative_window_produces_no_context_and_no_exports():
    inputs = _inputs(_horizon(TRIVIAL), min_cycle_saving=LIVE_MIN_CYCLE_SAVING)

    assert derive_negative_fit_avoidance_context(inputs) is None

    result = DPPlanner().plan(inputs)
    assert result.success
    assert _avoidance_exports(result) == []
    # The suppression is explainable from the result alone.
    assert result.negative_fit_suppressed is True
    assert result.negative_fit_spill_threshold == LIVE_MIN_CYCLE_SAVING
    assert 0.0 < result.negative_fit_spill_cost < 0.01


def test_trivial_window_is_the_only_thing_the_gate_removes():
    """With the gate off (0 disables, as everywhere else), the same horizon
    still opens the window — so the test above is measuring the gate and not
    some other early return."""
    inputs = _inputs(_horizon(TRIVIAL), min_cycle_saving=0.0)

    assert derive_negative_fit_avoidance_context(inputs) is not None

    result = DPPlanner().plan(inputs)
    assert _avoidance_exports(result) != []
    assert result.negative_fit_suppressed is False


def test_material_negative_window_still_pre_discharges():
    inputs = _inputs(_horizon(MATERIAL), min_cycle_saving=LIVE_MIN_CYCLE_SAVING)

    context = derive_negative_fit_avoidance_context(inputs)
    assert context is not None
    assert context.required_headroom_kwh > 0

    result = DPPlanner().plan(inputs)
    assert result.success
    assert any(
        d.reason_code == ReasonCode.NEGATIVE_FIT_AVOIDANCE
        and d.action == PlannerAction.EXPORT_PROACTIVE
        for d in result.decisions
    )
    assert result.negative_fit_suppressed is False
    assert result.negative_fit_spill_cost >= LIVE_MIN_CYCLE_SAVING


def test_material_window_plan_is_unchanged_by_the_gate():
    """A window the gate lets through must plan exactly as it did before."""
    gated = DPPlanner().plan(
        _inputs(_horizon(MATERIAL), min_cycle_saving=LIVE_MIN_CYCLE_SAVING)
    )
    ungated_inputs = _inputs(_horizon(MATERIAL), min_cycle_saving=LIVE_MIN_CYCLE_SAVING)
    with patch(
        "custom_components.localshift.engine.negative_fit._spill_cost_is_immaterial",
        return_value=False,
    ):
        ungated = DPPlanner().plan(ungated_inputs)

    assert [d.action for d in gated.decisions] == [d.action for d in ungated.decisions]


def test_no_negative_window_reports_nothing():
    inputs = _inputs(_horizon({}), min_cycle_saving=LIVE_MIN_CYCLE_SAVING)

    result = DPPlanner().plan(inputs)

    assert result.negative_fit_spill_cost is None
    assert result.negative_fit_suppressed is False


def test_spill_that_the_battery_can_absorb_costs_nothing():
    """A negative slot is only a cost if the surplus has nowhere to go."""
    slots = _horizon({8.0: -0.50})  # deeply negative, but the battery is at 60%
    inputs = _inputs(slots, min_cycle_saving=LIVE_MIN_CYCLE_SAVING)

    result = DPPlanner().plan(inputs)

    assert result.negative_fit_spill_cost == 0.0
    assert _avoidance_exports(result) == []


# ---------------------------------------------------------------------------
# Replay of the 2026-10-09 08:07 inputs through the production cycle
# ---------------------------------------------------------------------------


def _replay(config_overrides: dict | None = None):
    """Run the captured cycle through ComputationEngine.compute_derived_values.

    Same harness shape as ``scripts/replay_no_dw.py::run_arm``: seed the price
    horizon, then two passes so cycle-lagged coordinator fields are warm.
    """
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

    overrides = dict(scenario["config_overrides"])
    overrides.update(config_overrides or {})
    engine = ComputationEngine(
        hass,
        create_mock_entry(overrides),
        create_mock_get_entity_id(),
        create_mock_get_switch_state(scenario["switch_states"]),
    )
    fetcher = engine._history_fetcher
    with ExitStack() as stack:
        for p in (
            patch("homeassistant.util.dt.now", return_value=test_time),
            patch.object(engine, "_get_historical_hourly_averages", return_value={}),
            patch.object(fetcher, "_historical_load_cache", {}),
            patch.object(fetcher, "_historical_load_sample_counts", {}),
            patch.object(fetcher, "_historical_load_source", "none"),
            patch.object(fetcher, "_recent_load_1hr_kw", payload["load_power_kw"]),
        ):
            stack.enter_context(p)
        engine.compute_derived_values(data)
        engine.compute_derived_values(data)
    return data


@pytest.fixture(scope="module")
def replayed():
    return _replay()


def test_replay_fixture_is_the_incident():
    """Guard the fixture: the inputs are the ones the issue describes."""
    scenario = json.loads(REPLAY.read_text())
    payload = scenario["input"]
    assert payload["test_time"] == "2026-10-09T08:07:00+11:00"
    assert payload["soc"] == pytest.approx(9.67)
    assert scenario["config_overrides"]["min_cycle_saving"] == LIVE_MIN_CYCLE_SAVING
    non_positive = {
        s["start_time"][11:16]: s["per_kwh"]
        for s in payload["feed_in_forecast"]
        if s["per_kwh"] <= 0 and s["start_time"] < "2026-10-09T15:00"
    }
    assert non_positive == {"13:30": -0.0004, "14:00": -0.0013}


def test_replay_without_the_gate_reproduces_the_incident_plan():
    """min_cycle_saving=0 disables the gate: the morning exports come back.

    This is the 'before'. It keeps the replay honest — if the harness stopped
    reaching the avoidance branch, the assertions below would pass vacuously.
    """
    data = _replay({"min_cycle_saving": 0})
    exports = [
        d
        for d in data.optimizer_decisions
        if d["action"] == "export_proactive"
        and d["reason_code"] == "NEGATIVE_FIT_AVOIDANCE"
    ]
    assert len(exports) >= 5
    assert exports[0]["timestamp_iso"][11:16] in {"08:20", "08:25"}


def test_replay_yields_no_export_proactive_slots(replayed):
    assert replayed.optimizer_summary["success"] is True
    actions = {d["action"] for d in replayed.optimizer_decisions}
    reasons = {d["reason_code"] for d in replayed.optimizer_decisions}
    assert "export_proactive" not in actions
    assert "NEGATIVE_FIT_AVOIDANCE" not in reasons


def test_replay_does_not_fire_the_runway_backstop(replayed):
    assert replayed.optimizer_precharge_backstop_active is False
    assert not any(d.get("grid_charge") for d in replayed.optimizer_decisions)
    assert replayed.optimizer_summary["terminal_shortfall_pct"] == 0.0


def test_replay_publishes_threshold_and_spill_cost(replayed):
    summary = replayed.optimizer_summary
    assert summary["negative_fit_spill_threshold"] == LIVE_MIN_CYCLE_SAVING
    assert summary["negative_fit_suppressed"] is True
    # The issue's hand calculation was about $0.004.
    assert 0.0 < summary["negative_fit_spill_cost"] < 0.02

    coordinator = MagicMock()
    coordinator.data = replayed
    sensor = OptimizerSummarySensor.__new__(OptimizerSummarySensor)
    sensor.coordinator = coordinator
    attrs = sensor.extra_state_attributes
    assert attrs["negative_fit_spill_threshold"] == LIVE_MIN_CYCLE_SAVING
    assert attrs["negative_fit_spill_cost"] == summary["negative_fit_spill_cost"]
    assert attrs["negative_fit_suppressed"] is True
