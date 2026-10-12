"""Negative-FIT avoidance needs a materiality threshold (Issue #1103).

2026-10-09 08:07: the only non-positive feed-in slots before the 15:00 demand
window were 13:30 (-0.04c) and 14:00 (-0.13c) — well under a cent of exposure.
That opened the avoidance window, which drops the normal export value test, and
the plan exported the battery to its floor through the morning at 1 to 7c. Live,
that became eight export episodes and the pre-charge runway backstop firing at
12:50.

The gate: no avoidance context unless the spill the window would actually cost
(surplus that cannot be stored, times the magnitude of the non-positive sell
price) reaches ``NEGATIVE_FIT_MIN_SPILL_COST`` dollars. Once a window has opened
it stays open until the forecast itself stops being material, so the battery's
own pre-discharge cannot talk the gate into abandoning the plan part-way.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.computation_engine import ComputationEngine
from custom_components.localshift.const import (
    NEGATIVE_FIT_MIN_SPILL_COST,
    NEGATIVE_FIT_SPILL_RELEASE_FRACTION,
)
from custom_components.localshift.engine.negative_fit import (
    assess_negative_fit_avoidance,
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
MIN_SPILL_COST = NEGATIVE_FIT_MIN_SPILL_COST
MIN_SPILL_COST_CONST = "custom_components.localshift.const.NEGATIVE_FIT_MIN_SPILL_COST"


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


def _inputs(
    slots: list[SlotContext],
    *,
    min_spill_cost: float,
    min_cycle_saving: float = LIVE_MIN_CYCLE_SAVING,
    initial_soc_pct: float = 60.0,
    window_open: bool = False,
) -> OptimizerInputs:
    return OptimizerInputs(
        slots=slots,
        initial_soc_pct=initial_soc_pct,
        negative_fit_window_open=window_open,
        config=OptimizerConfig(
            battery_capacity_kwh=13.5,
            min_soc_pct=5.0,
            max_soc_pct=100.0,
            demand_window_target_soc_pct=95.0,
            optimization_mode="self_consumption",
            export_price_margin=0.10,
            min_cycle_saving=min_cycle_saving,
            negative_fit_min_spill_cost=min_spill_cost,
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
    inputs = _inputs(_horizon(TRIVIAL), min_spill_cost=MIN_SPILL_COST)

    assert derive_negative_fit_avoidance_context(inputs) is None

    result = DPPlanner().plan(inputs)
    assert result.success
    assert _avoidance_exports(result) == []
    # The suppression is explainable from the result alone.
    assert result.negative_fit_suppressed is True
    assert result.negative_fit_spill_threshold == MIN_SPILL_COST
    assert 0.0 < result.negative_fit_spill_cost < 0.01


def test_trivial_window_is_the_only_thing_the_gate_removes():
    """With the gate off (a zero threshold disables it), the same horizon
    still opens the window — so the test above is measuring the gate and not
    some other early return."""
    inputs = _inputs(_horizon(TRIVIAL), min_spill_cost=0.0)

    assert derive_negative_fit_avoidance_context(inputs) is not None

    result = DPPlanner().plan(inputs)
    assert _avoidance_exports(result) != []
    assert result.negative_fit_suppressed is False


def test_material_negative_window_still_pre_discharges():
    inputs = _inputs(_horizon(MATERIAL), min_spill_cost=MIN_SPILL_COST)

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
    assert result.negative_fit_spill_cost >= MIN_SPILL_COST


def test_material_window_plan_is_unchanged_by_the_gate():
    """A window the gate lets through must plan exactly as it did before."""
    gated = DPPlanner().plan(_inputs(_horizon(MATERIAL), min_spill_cost=MIN_SPILL_COST))
    ungated = DPPlanner().plan(_inputs(_horizon(MATERIAL), min_spill_cost=0.0))

    assert [d.action for d in gated.decisions] == [d.action for d in ungated.decisions]


def test_no_negative_window_reports_nothing():
    inputs = _inputs(_horizon({}), min_spill_cost=MIN_SPILL_COST)

    result = DPPlanner().plan(inputs)

    assert result.negative_fit_spill_cost is None
    assert result.negative_fit_suppressed is False


def test_spill_that_the_battery_can_absorb_costs_nothing():
    """A negative slot is only a cost if the surplus has nowhere to go."""
    slots = _horizon({8.0: -0.50})  # deeply negative, but the battery is at 60%
    inputs = _inputs(slots, min_spill_cost=MIN_SPILL_COST)

    result = DPPlanner().plan(inputs)

    assert result.negative_fit_spill_cost == 0.0
    assert _avoidance_exports(result) == []


# ---------------------------------------------------------------------------
# The threshold is its own figure, not the grid-charge slider (review of #1121)
# ---------------------------------------------------------------------------


def test_grid_charge_slider_at_zero_does_not_disable_the_gate():
    """``min_cycle_saving`` is $/kWh for grid charging and has been run at 0 as
    a live knob. That must not quietly reopen the 2026-10-09 window."""
    inputs = _inputs(
        _horizon(TRIVIAL), min_spill_cost=MIN_SPILL_COST, min_cycle_saving=0.0
    )

    result = DPPlanner().plan(inputs)

    assert result.negative_fit_suppressed is True
    assert result.negative_fit_spill_threshold == MIN_SPILL_COST
    assert _avoidance_exports(result) == []


def test_raising_the_grid_charge_slider_does_not_close_a_material_window():
    inputs = _inputs(
        _horizon(MATERIAL), min_spill_cost=MIN_SPILL_COST, min_cycle_saving=5.0
    )

    result = DPPlanner().plan(inputs)

    assert result.negative_fit_suppressed is False
    assert result.negative_fit_window_open is True


# ---------------------------------------------------------------------------
# Once open, the window is judged on the forecast, not on its own exports
# ---------------------------------------------------------------------------


def _alternating_horizon() -> list[SlotContext]:
    """08:00-16:00, battery full at 08:00, -4c / +2c alternating 10:00-14:30."""
    prices = {h / 2: (-0.04 if h % 2 == 0 else 0.02) for h in range(20, 30)}
    return _horizon(prices)


def _roll(slots: list[SlotContext], *, min_spill_cost: float, carry_latch: bool = True):
    """Re-plan every slot from the SOC the previous slot's action produced,
    handing each cycle the previous cycle's open/closed state the way
    ``OptimizerFacade.run_inline`` does. Returns one (time, result) per cycle.
    """
    soc_pct = 100.0
    window_open = False
    cycles = []
    for start in range(14):  # every cycle up to the 15:00 demand window
        remaining = [replace(s, slot_index=i) for i, s in enumerate(slots[start:])]
        result = DPPlanner().plan(
            _inputs(
                remaining,
                min_spill_cost=min_spill_cost,
                initial_soc_pct=soc_pct,
                window_open=window_open and carry_latch,
            )
        )
        assert result.success
        cycles.append((result.decisions[0].timestamp_iso[11:16], result))
        soc_pct = result.decisions[0].predicted_soc_pct
        window_open = result.negative_fit_window_open
    return cycles


def test_open_window_is_not_abandoned_because_its_own_exports_shrank_the_spill():
    """The 08:00 plan exports four morning slots and two inside the window.
    After the morning exports the battery is at 22%, so the do-nothing spill
    reads under the threshold — because of the exports the gate approved. The
    forecast has not changed, so the window must stay open."""
    release = MIN_SPILL_COST * NEGATIVE_FIT_SPILL_RELEASE_FRACTION
    gated = _roll(_alternating_horizon(), min_spill_cost=MIN_SPILL_COST)
    ungated = _roll(_alternating_horizon(), min_spill_cost=0.0)

    first = gated[0][1]
    assert first.negative_fit_spill_cost >= MIN_SPILL_COST
    planned = [
        d.timestamp_iso[11:16]
        for d in first.decisions
        if d.action == PlannerAction.EXPORT_PROACTIVE
    ]
    assert "12:30" in planned

    # The scenario is only interesting if the spill cost really does fall
    # under the threshold mid-window while the forecast is still material.
    by_time = dict(gated)
    at_ten = by_time["10:00"]
    assert at_ten.negative_fit_spill_cost < MIN_SPILL_COST
    assert at_ten.negative_fit_spill_exposure >= release
    assert at_ten.negative_fit_suppressed is False
    assert at_ten.negative_fit_held_open is True

    # While the forecast is material, the day executes exactly as it would
    # with no gate at all.
    material = [
        when for when, r in gated if (r.negative_fit_spill_exposure or 0.0) >= release
    ]
    assert "12:30" in material
    executed = {when: r.decisions[0].action for when, r in gated}
    executed_ungated = {when: r.decisions[0].action for when, r in ungated}
    assert {t: executed[t] for t in material} == {
        t: executed_ungated[t] for t in material
    }
    assert executed["12:30"] == PlannerAction.EXPORT_PROACTIVE


def test_without_the_carried_state_the_window_is_abandoned():
    """Guard for the test above: drop the hand-off and the abandonment is back,
    so the roll is exercising the latch and not some other path."""
    gated = dict(
        _roll(_alternating_horizon(), min_spill_cost=MIN_SPILL_COST, carry_latch=False)
    )
    assert gated["10:00"].negative_fit_suppressed is True
    assert gated["12:30"].decisions[0].action != PlannerAction.EXPORT_PROACTIVE


def _uniform(price: float) -> dict[float, float]:
    return {h / 2: price for h in range(22, 30)}  # 11:00 through 14:30


def test_small_forecast_revision_does_not_close_an_open_window():
    """-1.43c opens the window ($0.2517); -1.40c reads $0.2464. A revision of
    three hundredths of a cent must not flip a plan that is already exporting."""
    opened = DPPlanner().plan(
        _inputs(_horizon(_uniform(-0.0143)), min_spill_cost=MIN_SPILL_COST)
    )
    assert opened.negative_fit_window_open is True
    assert _avoidance_exports(opened) != []

    revised = _horizon(_uniform(-0.0140))
    cold = DPPlanner().plan(_inputs(revised, min_spill_cost=MIN_SPILL_COST))
    assert cold.negative_fit_suppressed is True  # the entry bar is unchanged

    held = DPPlanner().plan(
        _inputs(revised, min_spill_cost=MIN_SPILL_COST, window_open=True)
    )
    assert held.negative_fit_window_open is True
    assert held.negative_fit_held_open is True
    assert _avoidance_exports(held) == _avoidance_exports(opened)


def test_open_window_closes_when_the_forecast_stops_being_material():
    """Staying open is hysteresis, not a commitment: if the negative prices
    are revised away to a fraction of a cent the window closes again."""
    inputs = _inputs(_horizon(TRIVIAL), min_spill_cost=MIN_SPILL_COST, window_open=True)

    assessment = assess_negative_fit_avoidance(inputs)
    assert assessment.context is None
    assert assessment.suppressed is True
    assert assessment.held_open is False

    result = DPPlanner().plan(inputs)
    assert result.negative_fit_window_open is False
    assert _avoidance_exports(result) == []


# ---------------------------------------------------------------------------
# Replay of the 2026-10-09 08:07 inputs through the production cycle
# ---------------------------------------------------------------------------


def _replay(
    config_overrides: dict | None = None,
    *,
    min_spill_cost: float | None = None,
    window_open: bool = False,
    passes: int = 2,
):
    """Run the captured cycle through ComputationEngine.compute_derived_values.

    Same harness shape as ``scripts/replay_no_dw.py::run_arm``: seed the price
    horizon, then two passes so cycle-lagged coordinator fields are warm.

    ``min_spill_cost`` replaces the production constant; ``window_open`` seeds
    the coordinator's carried state as the previous cycle would have left it.
    """
    scenario = json.loads(REPLAY.read_text())
    payload = dict(scenario["input"])
    payload["adaptive_params"] = {}
    test_time = datetime.fromisoformat(payload["test_time"])

    data = setup_coordinator_data(payload)
    data.negative_fit_window_open = window_open
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
        if min_spill_cost is not None:
            stack.enter_context(patch(MIN_SPILL_COST_CONST, min_spill_cost))
        for _ in range(passes):
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
    """A zero threshold disables the gate: the morning exports come back.

    This is the 'before'. It keeps the replay honest — if the harness stopped
    reaching the avoidance branch, the assertions below would pass vacuously.
    """
    data = _replay(min_spill_cost=0.0)
    exports = _replay_avoidance_exports(data)
    assert len(exports) >= 5
    assert exports[0]["timestamp_iso"][11:16] in {"08:20", "08:25"}
    # The open state is carried on the coordinator for the next cycle.
    assert data.negative_fit_window_open is True


def _replay_avoidance_exports(data) -> list[dict]:
    return [
        d
        for d in data.optimizer_decisions
        if d["action"] == "export_proactive"
        and d["reason_code"] == "NEGATIVE_FIT_AVOIDANCE"
    ]


def test_replay_with_the_grid_charge_slider_at_zero_is_still_suppressed():
    """Production wiring of the dedicated threshold: the options-flow value of
    ``min_cycle_saving`` no longer reaches the negative-FIT gate."""
    data = _replay({"min_cycle_saving": 0})

    assert _replay_avoidance_exports(data) == []
    assert data.optimizer_summary["negative_fit_suppressed"] is True
    assert data.optimizer_summary["negative_fit_spill_threshold"] == MIN_SPILL_COST
    assert data.negative_fit_window_open is False


def test_replay_carries_the_open_state_into_the_next_cycle():
    """Production wiring of the latch, end to end. With a threshold between
    this horizon's spill cost and twice its exposure, a cold cycle is
    suppressed and a cycle that follows an open one keeps the window."""
    probe = _replay(min_spill_cost=0.0, passes=1).optimizer_summary
    spill_cost = probe["negative_fit_spill_cost"]
    exposure = probe["negative_fit_spill_exposure"]
    assert 0.0 < spill_cost <= exposure
    threshold = exposure * 1.5  # above the cost, release level below the exposure
    assert spill_cost < threshold
    assert exposure >= threshold * NEGATIVE_FIT_SPILL_RELEASE_FRACTION

    cold = _replay(min_spill_cost=threshold, passes=1)
    assert cold.optimizer_summary["negative_fit_suppressed"] is True
    assert _replay_avoidance_exports(cold) == []
    assert cold.negative_fit_window_open is False

    warm = _replay(min_spill_cost=threshold, window_open=True, passes=2)
    assert warm.optimizer_summary["negative_fit_suppressed"] is False
    assert warm.optimizer_summary["negative_fit_held_open"] is True
    assert _replay_avoidance_exports(warm) != []
    # Two passes: the second only stays open because the first wrote it back.
    assert warm.negative_fit_window_open is True


def test_replay_stale_open_state_does_not_reopen_the_incident_window():
    """At the real threshold the 10-09 exposure is far below the release level,
    so a leftover open flag from an earlier cycle changes nothing."""
    data = _replay(window_open=True)

    assert _replay_avoidance_exports(data) == []
    assert data.optimizer_summary["negative_fit_suppressed"] is True
    assert data.negative_fit_window_open is False


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
    assert summary["negative_fit_spill_threshold"] == MIN_SPILL_COST
    assert summary["negative_fit_suppressed"] is True
    # The issue's hand calculation was about $0.004.
    assert 0.0 < summary["negative_fit_spill_cost"] < 0.02

    coordinator = MagicMock()
    coordinator.data = replayed
    sensor = OptimizerSummarySensor.__new__(OptimizerSummarySensor)
    sensor.coordinator = coordinator
    attrs = sensor.extra_state_attributes
    assert attrs["negative_fit_spill_threshold"] == MIN_SPILL_COST
    assert attrs["negative_fit_spill_cost"] == summary["negative_fit_spill_cost"]
    assert attrs["negative_fit_suppressed"] is True
