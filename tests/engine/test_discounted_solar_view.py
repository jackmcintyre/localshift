"""Display-only discounted solar view (#1271, #1273).

The values published for the dashboard must match what the planner decides on
(accuracy-discounted solar), and must differ from the raw view when forecast
accuracy is below 1.
"""

from __future__ import annotations

import pytest

from custom_components.localshift.engine.constraints import (
    _required_stored_kwh_to_target,
    solar_only_dw_entry_soc,
)
from custom_components.localshift.engine.solar import (
    can_solar_reach_target,
    discounted_in_window_solar_soc,
)
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    SlotContext,
)


def _slot(i: int, hhmm: str, solar: float, dw: bool) -> SlotContext:
    return SlotContext(
        slot_index=i,
        timestamp_iso=f"2024-01-01T{hhmm}:00+00:00",
        slot_interval_minutes=30,
        buy_price=0.12,
        sell_price=0.10,
        solar_kwh=solar,
        consumption_kwh=0.5,
        is_demand_window_slot=dw,
    )


def _case(accuracy: float, switch: bool = True):
    config = OptimizerConfig(
        battery_capacity_kwh=13.5,
        demand_window_target_soc_pct=90.0,
        allow_dw_entry_under_target=switch,
        solar_charge_rate_kw=5.0,
        charge_efficiency=0.92,
        discharge_efficiency=0.95,
        min_soc_pct=0.0,
        solar_forecast_accuracy=accuracy,
    )
    slots = [
        _slot(0, "11:00", 3.0, False),
        _slot(1, "11:30", 3.0, False),
        _slot(2, "12:00", 3.0, False),
        _slot(3, "12:30", 3.0, False),
        _slot(4, "13:00", 3.0, True),
        _slot(5, "13:30", 3.0, True),
    ]
    inputs = OptimizerInputs(
        cycle_id="t", config=config, slots=slots, initial_soc_pct=40.0
    )
    bounds = {"entry_idx": 4, "end_idx": 5}
    return inputs, slots, config, bounds


def test_low_accuracy_discounted_entry_soc_is_below_raw():
    _, slots, config, _ = _case(0.5)
    raw, discounted = solar_only_dw_entry_soc(slots, config, 4, 40.0)
    assert raw > 40.0
    assert discounted == pytest.approx(40.0 + (raw - 40.0) * 0.5)
    assert discounted < raw


def test_full_accuracy_views_agree():
    _, slots, config, _ = _case(1.0)
    raw, discounted = solar_only_dw_entry_soc(slots, config, 4, 40.0)
    assert discounted == pytest.approx(raw)


def test_discounted_entry_soc_matches_the_required_charge_maths():
    """The published value is what _required_stored_kwh_to_target plans on."""
    _, slots, config, _ = _case(0.4)
    _, discounted = solar_only_dw_entry_soc(slots, config, 4, 40.0)
    expected_kwh = max(
        0.0,
        (config.demand_window_target_soc_pct - discounted)
        / 100.0
        * config.battery_capacity_kwh,
    )
    assert _required_stored_kwh_to_target(slots, config, 4, 40.0) == pytest.approx(
        expected_kwh
    )


def test_gate_soc_differs_from_raw_and_explains_the_gate_result():
    """Raw solar reaches target; the discounted gate value does not (#1271)."""
    inputs, slots, config, bounds = _case(0.4)
    gate_soc = discounted_in_window_solar_soc(inputs, slots, config, bounds)
    assert gate_soc is not None
    assert gate_soc < config.demand_window_target_soc_pct
    assert can_solar_reach_target(inputs, slots, config, bounds) is False

    full_inputs, full_slots, full_config, full_bounds = _case(1.0)
    raw_gate_soc = discounted_in_window_solar_soc(
        full_inputs, full_slots, full_config, full_bounds
    )
    assert raw_gate_soc is not None
    assert raw_gate_soc >= full_config.demand_window_target_soc_pct
    assert can_solar_reach_target(full_inputs, full_slots, full_config, full_bounds)
    assert gate_soc < raw_gate_soc


def test_gate_soc_is_published_even_with_switch_off():
    inputs, slots, config, bounds = _case(0.4, switch=False)
    assert can_solar_reach_target(inputs, slots, config, bounds) is False
    assert discounted_in_window_solar_soc(inputs, slots, config, bounds) is not None


def test_no_demand_window_gives_no_gate_soc():
    inputs, slots, config, _ = _case(0.4)
    assert (
        discounted_in_window_solar_soc(
            inputs, slots, config, {"entry_idx": None, "end_idx": None}
        )
        is None
    )
