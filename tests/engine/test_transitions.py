from __future__ import annotations

import pytest

from custom_components.localshift.engine.transitions import transition
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
    assert grid_import_kwh == 0.0
    assert grid_export_kwh == 0.0
