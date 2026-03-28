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


def _mode_payload(
    *,
    grid_rows: list[dict[str, float | int]] | None = None,
    boost_rows: list[dict[str, float | int]] | None = None,
    export_rows: list[dict[str, float | int]] | None = None,
) -> dict[str, object]:
    return {
        "generated_at": "2026-03-27T00:00:00+00:00",
        "method": {"soc_bin_pct": 1, "resample": "1m"},
        "window": {"history_window_days": 14},
        "soc_bins_1pct_by_mode": {
            "self_consumption": [],
            "grid_charging": grid_rows or [],
            "boost_charging": boost_rows or [],
            "proactive_export": export_rows or [],
        },
    }


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


def test_selects_exact_mode_soc_bin_rate() -> None:
    class DummyData:
        def __init__(self) -> None:
            self.effective_cheap_price = 0.1
            self.general_price = 0.2
            self.adaptive_params = None
            self.forecast_horizon_hours = 24.0
            self.charge_rate_curves = None
            self.learning_enabled = False
            self.charge_rate_mode_analysis = _mode_payload(
                grid_rows=[
                    {"soc": 20, "n": 8, "charge_kw": 4.0, "discharge_kw": 0.0},
                    {"soc": 50, "n": 8, "charge_kw": 4.6, "discharge_kw": 0.0},
                    {"soc": 70, "n": 8, "charge_kw": 3.2, "discharge_kw": 0.0},
                ]
            )

    config = _build_optimizer_config(DummyData(), {})
    config.battery_capacity_kwh = 20.0
    config.charge_efficiency = 1.0

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.9, PlannerAction.CHARGE_GRID_NORMAL, _slot(), config
    )

    assert grid_import_kwh == pytest.approx(2.3)
    assert grid_export_kwh == 0.0
    assert next_soc == pytest.approx(62.4)


def test_fallback_chain_mode_soc_defaults() -> None:
    slot = _slot()
    base = OptimizerConfig(
        battery_capacity_kwh=20.0,
        charge_efficiency=1.0,
        charge_rate_kw=1.5,
        mode_action_soc_bin_rates={
            PlannerAction.CHARGE_GRID_NORMAL: {29: 4.0, 31: 2.0}
        },
    )

    interpolated_soc, interpolated_import, _ = transition(
        30.7,
        PlannerAction.CHARGE_GRID_NORMAL,
        slot,
        base,
    )
    assert interpolated_import == pytest.approx(1.5)
    assert interpolated_soc == pytest.approx(38.2)

    nearest_tie_config = OptimizerConfig(
        battery_capacity_kwh=20.0,
        charge_efficiency=1.0,
        charge_rate_kw=1.5,
        mode_action_soc_bin_rates={
            PlannerAction.CHARGE_GRID_NORMAL: {20: 4.0, 40: 2.0}
        },
    )

    nearest_soc, nearest_import, _ = transition(
        30.0,
        PlannerAction.CHARGE_GRID_NORMAL,
        slot,
        nearest_tie_config,
    )
    assert nearest_import == pytest.approx(2.0)
    assert nearest_soc == pytest.approx(40.0)

    mode_average_config = OptimizerConfig(
        battery_capacity_kwh=20.0,
        charge_efficiency=1.0,
        charge_rate_kw=1.5,
        mode_action_soc_bin_rates={PlannerAction.CHARGE_GRID_NORMAL: {}},
        mode_action_average_rates={PlannerAction.CHARGE_GRID_NORMAL: 3.0},
    )
    average_soc, average_import, _ = transition(
        30.0,
        PlannerAction.CHARGE_GRID_NORMAL,
        slot,
        mode_average_config,
    )
    assert average_import == pytest.approx(1.5)
    assert average_soc == pytest.approx(37.5)

    default_soc, default_import, _ = transition(
        30.0,
        PlannerAction.CHARGE_GRID_NORMAL,
        slot,
        OptimizerConfig(
            battery_capacity_kwh=20.0,
            charge_efficiency=1.0,
            charge_rate_kw=1.5,
            mode_action_soc_bin_rates={PlannerAction.CHARGE_GRID_NORMAL: {}},
            mode_action_average_rates={},
        ),
    )
    assert default_import == pytest.approx(0.75)
    assert default_soc == pytest.approx(33.75)


def test_keeps_existing_charge_rate_curve_path_when_mode_payload_missing() -> None:
    curve = ChargeRateCurve.from_bins({0: 4.0, 100: 2.0})

    class DummyData:
        def __init__(self) -> None:
            self.effective_cheap_price = 0.1
            self.general_price = 0.2
            self.adaptive_params = None
            self.forecast_horizon_hours = 24.0
            self.charge_rate_curves = {"normal": curve, "boost": None}
            self.learning_enabled = True
            self.charge_rate_mode_analysis = {}

    config = _build_optimizer_config(DummyData(), {})
    config.battery_capacity_kwh = 20.0
    config.charge_efficiency = 1.0

    next_soc, grid_import_kwh, _ = transition(
        50.0,
        PlannerAction.CHARGE_GRID_NORMAL,
        _slot(),
        config,
    )

    expected_import = curve.rate_at_soc(50.0) * 0.5
    assert grid_import_kwh == pytest.approx(expected_import)
    assert next_soc == pytest.approx(50.0 + expected_import / 20.0 * 100.0)


def test_selects_discharge_rate_for_export_proactive_mode() -> None:
    class DummyData:
        def __init__(self) -> None:
            self.effective_cheap_price = 0.1
            self.general_price = 0.2
            self.adaptive_params = None
            self.forecast_horizon_hours = 24.0
            self.charge_rate_curves = None
            self.learning_enabled = False
            self.charge_rate_mode_analysis = _mode_payload(
                export_rows=[
                    {"soc": 20, "n": 8, "charge_kw": 0.0, "discharge_kw": 2.0},
                    {"soc": 50, "n": 8, "charge_kw": 0.0, "discharge_kw": 3.0},
                    {"soc": 80, "n": 8, "charge_kw": 0.0, "discharge_kw": 4.0},
                ]
            )

    config = _build_optimizer_config(DummyData(), {})
    config.battery_capacity_kwh = 20.0
    config.discharge_efficiency = 1.0
    slot = SlotContext(
        slot_index=0,
        timestamp_iso="2024-01-01T00:00:00+00:00",
        slot_interval_minutes=30,
        buy_price=0.0,
        sell_price=0.0,
        solar_kwh=0.3,
        consumption_kwh=0.8,
    )

    next_soc, grid_import_kwh, grid_export_kwh = transition(
        50.0,
        PlannerAction.EXPORT_PROACTIVE,
        slot,
        config,
    )

    assert grid_import_kwh == 0.0
    assert grid_export_kwh == pytest.approx(1.0)
    assert next_soc == pytest.approx(42.5)


def test_mode_payload_sparse_uses_legacy_curve_before_defaults() -> None:
    curve = ChargeRateCurve.from_bins({0: 3.6})

    class DummyData:
        def __init__(self) -> None:
            self.effective_cheap_price = 0.1
            self.general_price = 0.2
            self.adaptive_params = None
            self.forecast_horizon_hours = 24.0
            self.charge_rate_curves = {"normal": curve, "boost": None}
            self.learning_enabled = True
            self.charge_rate_mode_analysis = _mode_payload(
                grid_rows=[
                    {
                        "soc": 40,
                        "n": 8,
                        "charge_kw": float("nan"),
                        "discharge_kw": 0.0,
                    }
                ]
            )

    config = _build_optimizer_config(DummyData(), {})
    config.battery_capacity_kwh = 20.0
    config.charge_efficiency = 1.0
    config.charge_rate_kw = 1.0

    next_soc, grid_import_kwh, _ = transition(
        40.0,
        PlannerAction.CHARGE_GRID_NORMAL,
        _slot(),
        config,
    )

    assert grid_import_kwh == pytest.approx(1.8)
    assert next_soc == pytest.approx(49.0)


def test_mode_soc_bin_rate_tie_break_prefers_lower_bin() -> None:
    config = OptimizerConfig(
        battery_capacity_kwh=20.0,
        charge_efficiency=1.0,
        charge_rate_kw=1.0,
        mode_action_soc_bin_rates={
            PlannerAction.CHARGE_GRID_NORMAL: {40: 4.0, 44: 2.0}
        },
    )

    next_soc, grid_import_kwh, _ = transition(
        41.0,
        PlannerAction.CHARGE_GRID_NORMAL,
        _slot(),
        config,
    )

    assert grid_import_kwh == pytest.approx(2.0)
    assert next_soc == pytest.approx(51.0)
