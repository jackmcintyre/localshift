from __future__ import annotations

import pytest

from custom_components.localshift.engine.optimizer_runner import _build_optimizer_config
from custom_components.localshift.engine.transitions import transition
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)
from custom_components.localshift.learning.charge_rate import ChargeRateCurve


def _slot() -> SlotContext:
    return SlotContext(
        slot_index=0,
        timestamp_iso="2024-01-01T00:00:00+00:00",
        slot_interval_minutes=30,
        buy_price=0.0,
        sell_price=0.0,
        solar_kwh=0.0,
        consumption_kwh=0.0,
    )


def test_transition_uses_curve_rate_at_soc() -> None:
    curve = ChargeRateCurve.from_bins({0: 4.0, 100: 2.0})
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        charge_rate_kw=1.0,
        charge_efficiency=1.0,
        charge_rate_curve=curve,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.0, PlannerAction.CHARGE_GRID_NORMAL, _slot(), config
    )

    expected_rate = curve.rate_at_soc(50.0)
    expected_import = expected_rate * 0.5
    expected_soc = 50.0 + (expected_import / config.battery_capacity_kwh) * 100.0

    assert grid_import_kwh == pytest.approx(expected_import)
    assert grid_export_kwh == 0.0
    assert next_soc == pytest.approx(expected_soc)


def test_transition_uses_boost_curve() -> None:
    curve = ChargeRateCurve.from_bins({0: 6.0, 100: 2.0})
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        boost_charge_rate_kw=2.0,
        charge_efficiency=1.0,
        boost_charge_rate_curve=curve,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.0, PlannerAction.CHARGE_GRID_BOOST, _slot(), config
    )

    expected_rate = curve.rate_at_soc(50.0)
    expected_import = expected_rate * 0.5
    expected_soc = 50.0 + (expected_import / config.battery_capacity_kwh) * 100.0

    assert grid_import_kwh == pytest.approx(expected_import)
    assert grid_export_kwh == 0.0
    assert next_soc == pytest.approx(expected_soc)


def test_transition_applies_charge_efficiency() -> None:
    curve = ChargeRateCurve.from_bins({0: 5.0})
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        charge_efficiency=0.8,
        charge_rate_curve=curve,
    )

    next_soc, grid_import_kwh, _grid_export_kwh = transition(
        40.0, PlannerAction.CHARGE_GRID_NORMAL, _slot(), config
    )

    expected_import = 5.0 * 0.5
    expected_stored = expected_import * config.charge_efficiency
    expected_soc = 40.0 + (expected_stored / config.battery_capacity_kwh) * 100.0

    assert grid_import_kwh == pytest.approx(expected_import)
    assert next_soc == pytest.approx(expected_soc)


def test_transition_fallbacks_to_default_rate() -> None:
    config = OptimizerConfig(
        battery_capacity_kwh=10.0,
        charge_rate_kw=4.0,
        charge_efficiency=1.0,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.0, PlannerAction.CHARGE_GRID_NORMAL, _slot(), config
    )

    expected_import = 4.0 * 0.5
    expected_soc = 50.0 + (expected_import / config.battery_capacity_kwh) * 100.0

    assert grid_import_kwh == pytest.approx(expected_import)
    assert grid_export_kwh == 0.0
    assert next_soc == pytest.approx(expected_soc)


def test_optimizer_runner_ignores_curves_when_learning_disabled() -> None:
    curve_normal = ChargeRateCurve.from_bins({0: 4.0})
    curve_boost = ChargeRateCurve.from_bins({0: 6.0})

    class DummyData:
        def __init__(self) -> None:
            self.effective_cheap_price = 0.1
            self.general_price = 0.2
            self.adaptive_params = None
            self.forecast_horizon_hours = 24.0
            self.charge_rate_curves = {
                "normal": curve_normal,
                "boost": curve_boost,
            }
            self.learning_enabled = False

    config = _build_optimizer_config(DummyData(), {})

    assert config.charge_rate_curve is None
    assert config.boost_charge_rate_curve is None


def test_optimizer_runner_applies_curves_when_learning_enabled() -> None:
    curve_normal = ChargeRateCurve.from_bins({0: 4.0})
    curve_boost = ChargeRateCurve.from_bins({0: 6.0})

    class DummyData:
        def __init__(self) -> None:
            self.effective_cheap_price = 0.1
            self.general_price = 0.2
            self.adaptive_params = None
            self.forecast_horizon_hours = 24.0
            self.charge_rate_curves = {
                "normal": curve_normal,
                "boost": curve_boost,
            }
            self.learning_enabled = True

    config = _build_optimizer_config(DummyData(), {})

    assert config.charge_rate_curve is curve_normal
    assert config.boost_charge_rate_curve is curve_boost
