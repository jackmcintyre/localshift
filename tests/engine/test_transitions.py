from __future__ import annotations

from typing import cast

import pytest

from custom_components.localshift.engine.transitions import (
    _nearest_soc_bin,
    _select_legacy_curve_rate,
    _select_mode_soc_bin_rate,
    _select_static_default_rate,
    _select_transition_rate_kw,
    transition,
)
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)


def _slot(solar_kwh: float = 0.0, consumption_kwh: float = 0.0) -> SlotContext:
    return SlotContext(
        slot_index=0,
        timestamp_iso="2024-01-01T00:00:00+00:00",
        slot_interval_minutes=30,
        buy_price=0.0,
        sell_price=0.0,
        solar_kwh=solar_kwh,
        consumption_kwh=consumption_kwh,
    )


def test_transition_hold_no_net_energy() -> None:
    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.0, PlannerAction.HOLD, _slot(), OptimizerConfig()
    )

    assert next_soc == pytest.approx(50.0)
    assert grid_import_kwh == 0.0
    assert grid_export_kwh == 0.0


def test_transition_hold_surplus_charges_and_exports() -> None:
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        solar_charge_rate_kw=2.0,
        charge_efficiency=1.0,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.0, PlannerAction.HOLD, _slot(solar_kwh=2.0), config
    )

    assert next_soc == pytest.approx(60.0)
    assert grid_import_kwh == 0.0
    assert grid_export_kwh == pytest.approx(1.0)


def test_transition_hold_surplus_with_zero_efficiency() -> None:
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        solar_charge_rate_kw=5.0,
        charge_efficiency=0.0,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.0, PlannerAction.HOLD, _slot(solar_kwh=1.0), config
    )

    assert next_soc == pytest.approx(50.0)
    assert grid_import_kwh == 0.0
    assert grid_export_kwh == pytest.approx(1.0)


def test_transition_hold_deficit_respects_hold_soc() -> None:
    config = OptimizerConfig(hold_soc=True)

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.0, PlannerAction.HOLD, _slot(consumption_kwh=2.0), config
    )

    assert next_soc == pytest.approx(50.0)
    assert grid_import_kwh == pytest.approx(2.0)
    assert grid_export_kwh == 0.0


def test_transition_hold_deficit_with_zero_discharge_efficiency() -> None:
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        discharge_rate_kw=5.0,
        discharge_efficiency=0.0,
        min_soc_pct=10.0,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.0, PlannerAction.HOLD, _slot(consumption_kwh=2.0), config
    )

    assert next_soc == pytest.approx(50.0)
    assert grid_import_kwh == pytest.approx(2.0)
    assert grid_export_kwh == 0.0


def test_transition_charge_grid_deficit() -> None:
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        charge_rate_kw=4.0,
        charge_efficiency=0.8,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        40.0, PlannerAction.CHARGE_GRID_NORMAL, _slot(consumption_kwh=1.0), config
    )

    assert grid_import_kwh == pytest.approx(3.0)
    assert next_soc == pytest.approx(56.0)
    assert grid_export_kwh == 0.0


def test_transition_charge_grid_surplus_clips_to_max_soc() -> None:
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        charge_rate_kw=5.0,
        charge_efficiency=1.0,
        max_soc_pct=100.0,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        95.0, PlannerAction.CHARGE_GRID_NORMAL, _slot(solar_kwh=2.0), config
    )

    assert next_soc == pytest.approx(100.0)
    assert grid_import_kwh == pytest.approx(0.0)
    assert grid_export_kwh == 0.0


def test_transition_charge_grid_deficit_clips_to_max_soc() -> None:
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        charge_rate_kw=5.0,
        charge_efficiency=1.0,
        max_soc_pct=100.0,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        99.0, PlannerAction.CHARGE_GRID_NORMAL, _slot(consumption_kwh=1.0), config
    )

    assert next_soc == pytest.approx(100.0)
    assert grid_import_kwh == pytest.approx(1.1)
    assert grid_export_kwh == 0.0


def test_transition_charge_grid_with_solar_headroom() -> None:
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        charge_rate_kw=1.0,
        charge_efficiency=1.0,
        max_soc_pct=100.0,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.0, PlannerAction.CHARGE_GRID_NORMAL, _slot(solar_kwh=1.0), config
    )

    assert grid_import_kwh == pytest.approx(0.5)
    assert next_soc == pytest.approx(65.0)
    assert grid_export_kwh == 0.0


def test_transition_export_with_solar_surplus() -> None:
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        discharge_rate_kw=2.0,
        discharge_efficiency=1.0,
        min_soc_pct=10.0,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.0, PlannerAction.EXPORT_PROACTIVE, _slot(solar_kwh=1.0), config
    )

    assert next_soc == pytest.approx(40.0)
    assert grid_import_kwh == 0.0
    assert grid_export_kwh == pytest.approx(2.0)


def test_transition_export_with_deficit_zero_efficiency() -> None:
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        discharge_rate_kw=2.0,
        discharge_efficiency=0.0,
        min_soc_pct=10.0,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.0, PlannerAction.EXPORT_PROACTIVE, _slot(consumption_kwh=1.0), config
    )

    assert next_soc == pytest.approx(50.0)
    assert grid_import_kwh == pytest.approx(1.0)
    assert grid_export_kwh == 0.0


def test_transition_export_with_deficit_imports_remaining_load() -> None:
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        discharge_rate_kw=2.0,
        discharge_efficiency=1.0,
        min_soc_pct=10.0,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.0,
        PlannerAction.EXPORT_PROACTIVE,
        _slot(solar_kwh=0.0, consumption_kwh=2.0),
        config,
    )

    assert next_soc == pytest.approx(40.0)
    assert grid_import_kwh == pytest.approx(1.0)
    assert grid_export_kwh == pytest.approx(0.0)


class _DummyCurve:
    def __init__(self, value: float) -> None:
        self.value = value

    def rate_at_soc(self, _soc: float) -> float:
        return self.value


def test_transition_charge_grid_boost_uses_boost_rate() -> None:
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        boost_charge_rate_kw=5.0,
        charge_efficiency=1.0,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        40.0, PlannerAction.CHARGE_GRID_BOOST, _slot(consumption_kwh=1.0), config
    )

    assert next_soc == pytest.approx(65.0)
    assert grid_import_kwh == pytest.approx(3.5)
    assert grid_export_kwh == 0.0


def test_transition_unknown_action_returns_noop() -> None:
    next_soc, grid_import_kwh, grid_export_kwh = transition(
        44.0,
        cast(PlannerAction, "unknown_action"),
        _slot(),
        OptimizerConfig(),
    )

    assert next_soc == pytest.approx(44.0)
    assert grid_import_kwh == 0.0
    assert grid_export_kwh == 0.0


def test_transition_charge_grid_with_zero_efficiency_and_surplus() -> None:
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        charge_rate_kw=5.0,
        charge_efficiency=0.0,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.0,
        PlannerAction.CHARGE_GRID_NORMAL,
        _slot(solar_kwh=1.0, consumption_kwh=0.0),
        config,
    )

    assert next_soc == pytest.approx(50.0)
    assert grid_import_kwh == pytest.approx(0.0)
    assert grid_export_kwh == pytest.approx(0.0)


def test_transition_charge_grid_with_zero_efficiency_and_deficit() -> None:
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        charge_rate_kw=5.0,
        charge_efficiency=0.0,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.0,
        PlannerAction.CHARGE_GRID_NORMAL,
        _slot(solar_kwh=0.0, consumption_kwh=2.0),
        config,
    )

    assert next_soc == pytest.approx(50.0)
    assert grid_import_kwh == pytest.approx(2.0)
    assert grid_export_kwh == pytest.approx(0.0)


def test_select_mode_soc_bin_rate_exact_neighbor_nearest_and_average() -> None:
    action = PlannerAction.CHARGE_GRID_NORMAL
    config = OptimizerConfig(
        mode_action_soc_bin_rates={action: {40: 3.0, 42: 5.0, 46: 9.0}},
        mode_action_average_rates={action: 2.2},
    )

    assert _select_mode_soc_bin_rate(40.9, action, config) == pytest.approx(3.0)
    assert _select_mode_soc_bin_rate(41.1, action, config) == pytest.approx(4.0)
    assert _select_mode_soc_bin_rate(44.2, action, config) == pytest.approx(5.0)

    empty_config = OptimizerConfig(
        mode_action_soc_bin_rates={},
        mode_action_average_rates={action: 2.2},
    )
    assert _select_mode_soc_bin_rate(60.0, action, empty_config) == pytest.approx(2.2)


def test_select_transition_rate_fallback_chain_mode_then_legacy_then_static() -> None:
    action = PlannerAction.CHARGE_GRID_NORMAL
    config_mode = OptimizerConfig(
        mode_action_soc_bin_rates={action: {50: 4.4}},
        charge_rate_curve=_DummyCurve(3.3),
        charge_rate_kw=2.2,
    )
    assert _select_transition_rate_kw(50.0, action, config_mode) == pytest.approx(4.4)

    config_legacy = OptimizerConfig(
        mode_action_soc_bin_rates={},
        charge_rate_curve=_DummyCurve(3.3),
        charge_rate_kw=2.2,
    )
    assert _select_transition_rate_kw(50.0, action, config_legacy) == pytest.approx(3.3)

    config_static = OptimizerConfig(
        mode_action_soc_bin_rates={},
        charge_rate_curve=None,
        charge_rate_kw=2.2,
    )
    assert _select_transition_rate_kw(50.0, action, config_static) == pytest.approx(2.2)


def test_nearest_soc_bin_tie_breaks_to_lower_bin() -> None:
    nearest = _nearest_soc_bin(sorted_bins=[(40, 1.0), (44, 2.0)], target_soc=42)
    assert nearest == (40, 1.0)


def test_select_legacy_curve_rate_paths() -> None:
    config = OptimizerConfig(
        charge_rate_curve=_DummyCurve(3.2),
        boost_charge_rate_curve=_DummyCurve(4.8),
    )
    assert (
        _select_legacy_curve_rate(70.0, PlannerAction.CHARGE_GRID_NORMAL, config) == 3.2
    )
    assert (
        _select_legacy_curve_rate(70.0, PlannerAction.CHARGE_GRID_BOOST, config) == 4.8
    )
    assert (
        _select_legacy_curve_rate(70.0, PlannerAction.EXPORT_PROACTIVE, config) is None
    )

    no_curve = OptimizerConfig()
    assert (
        _select_legacy_curve_rate(70.0, PlannerAction.CHARGE_GRID_NORMAL, no_curve)
        is None
    )
    assert (
        _select_legacy_curve_rate(70.0, PlannerAction.CHARGE_GRID_BOOST, no_curve)
        is None
    )


def test_select_static_default_rate_paths() -> None:
    config = OptimizerConfig(
        charge_rate_kw=3.3, boost_charge_rate_kw=5.0, discharge_rate_kw=4.0
    )
    assert _select_static_default_rate(PlannerAction.CHARGE_GRID_NORMAL, config) == 3.3
    assert _select_static_default_rate(PlannerAction.CHARGE_GRID_BOOST, config) == 5.0
    assert _select_static_default_rate(PlannerAction.EXPORT_PROACTIVE, config) == 4.0
