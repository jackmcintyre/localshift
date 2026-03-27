"""Tests for optimizer runner adapter parity (Phase 6 #448 rename from shadow_runner).

Validates that the adapter layer correctly converts legacy coordinator data
into OptimizerInputs with proper field mapping:

1. Config mapping: all OptimizerConfig fields are correctly populated
2. Slot context parity: legacy slots map 1:1 to SlotContext
3. Alignment validation: issues are detected and reported
4. Completeness tracking: parity_completeness_pct is accurate
"""

import pytest

from custom_components.localshift.engine.optimizer_runner import (
    _build_optimizer_config,
    _build_summary,
    _normalize_initial_soc,
    _validate_slot_alignment,
    run_optimizer,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_coordinator_data():
    """Create mock coordinator data with typical daily_forecast slots."""

    class MockData:
        def __init__(self):
            self.soc = 65.0
            self.general_price = 0.26
            self.effective_cheap_price = 0.12
            self.general_forecast = [
                {"start_time": "2026-03-02T22:12:00", "per_kwh": 0.1},
                {"start_time": "2026-03-02T22:27:00", "per_kwh": 0.12000000000000001},
                {"start_time": "2026-03-02T22:42:00", "per_kwh": 0.14},
                {"start_time": "2026-03-02T22:57:00", "per_kwh": 0.16},
                {"start_time": "2026-03-02T23:12:00", "per_kwh": 0.18},
                {"start_time": "2026-03-02T23:27:00", "per_kwh": 0.2},
                {"start_time": "2026-03-02T23:42:00", "per_kwh": 0.22},
                {"start_time": "2026-03-02T23:57:00", "per_kwh": 0.24000000000000002},
                {"start_time": "2026-03-03T00:12:00", "per_kwh": 0.26},
                {"start_time": "2026-03-03T00:27:00", "per_kwh": 0.28},
                {"start_time": "2026-03-03T00:42:00", "per_kwh": 0.1},
                {"start_time": "2026-03-03T00:57:00", "per_kwh": 0.12000000000000001},
                {"start_time": "2026-03-03T01:12:00", "per_kwh": 0.14},
                {"start_time": "2026-03-03T01:27:00", "per_kwh": 0.16},
                {"start_time": "2026-03-03T01:42:00", "per_kwh": 0.18},
                {"start_time": "2026-03-03T01:57:00", "per_kwh": 0.2},
                {"start_time": "2026-03-03T02:12:00", "per_kwh": 0.22},
                {"start_time": "2026-03-03T02:27:00", "per_kwh": 0.24000000000000002},
                {"start_time": "2026-03-03T02:42:00", "per_kwh": 0.26},
                {"start_time": "2026-03-03T02:57:00", "per_kwh": 0.28},
                {"start_time": "2026-03-03T03:12:00", "per_kwh": 0.1},
                {"start_time": "2026-03-03T03:27:00", "per_kwh": 0.12000000000000001},
                {"start_time": "2026-03-03T03:42:00", "per_kwh": 0.14},
                {"start_time": "2026-03-03T03:57:00", "per_kwh": 0.16},
                {"start_time": "2026-03-03T04:12:00", "per_kwh": 0.18},
                {"start_time": "2026-03-03T04:27:00", "per_kwh": 0.2},
                {"start_time": "2026-03-03T04:42:00", "per_kwh": 0.22},
                {"start_time": "2026-03-03T04:57:00", "per_kwh": 0.24000000000000002},
                {"start_time": "2026-03-03T05:12:00", "per_kwh": 0.26},
                {"start_time": "2026-03-03T05:27:00", "per_kwh": 0.28},
                {"start_time": "2026-03-03T05:42:00", "per_kwh": 0.1},
                {"start_time": "2026-03-03T05:57:00", "per_kwh": 0.12000000000000001},
                {"start_time": "2026-03-03T06:12:00", "per_kwh": 0.14},
            ]
            self.feed_in_forecast = [
                {"start_time": "2026-03-02T22:12:00", "per_kwh": 0.05}
            ]
            self.solcast_today = [
                {"period_end": "2026-03-02T22:12:00", "pv_estimate": 0},
                {"period_end": "2026-03-02T22:42:00", "pv_estimate": 0},
                {"period_end": "2026-03-02T23:12:00", "pv_estimate": 0},
                {"period_end": "2026-03-02T23:42:00", "pv_estimate": 0},
                {"period_end": "2026-03-03T00:12:00", "pv_estimate": 0},
                {"period_end": "2026-03-03T00:42:00", "pv_estimate": 0},
                {"period_end": "2026-03-03T01:12:00", "pv_estimate": 0},
                {"period_end": "2026-03-03T01:42:00", "pv_estimate": 0.2},
                {"period_end": "2026-03-03T02:12:00", "pv_estimate": 0.4},
                {"period_end": "2026-03-03T02:42:00", "pv_estimate": 0.6},
                {"period_end": "2026-03-03T03:12:00", "pv_estimate": 0.8},
                {"period_end": "2026-03-03T03:42:00", "pv_estimate": 1.0},
                {"period_end": "2026-03-03T04:12:00", "pv_estimate": 1.2},
                {"period_end": "2026-03-03T04:42:00", "pv_estimate": 1.4},
                {"period_end": "2026-03-03T05:12:00", "pv_estimate": 1.6},
                {"period_end": "2026-03-03T05:42:00", "pv_estimate": 1.8},
                {"period_end": "2026-03-03T06:12:00", "pv_estimate": 2.0},
                {"period_end": "2026-03-03T06:42:00", "pv_estimate": 1.8},
            ]
            self.solcast_tomorrow = []
            self.load_forecast_slots = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
            self.adaptive_params = None
            self.daily_forecast = [
                {
                    "timestamp_iso": "2025-01-15T06:00:00Z",
                    "slot_interval_minutes": 30,
                    "buy_price": 0.25,
                    "sell_price": 0.08,
                    "solar_kwh": 0.5,
                    "consumption_kwh": 1.2,
                    "is_demand_window_entry": False,
                    "is_demand_window": False,
                    "price_source": "5min",
                },
                {
                    "timestamp_iso": "2025-01-15T06:30:00Z",
                    "slot_interval_minutes": 30,
                    "buy_price": 0.22,
                    "sell_price": 0.08,
                    "solar_kwh": 1.5,
                    "consumption_kwh": 0.8,
                    "is_demand_window_entry": False,
                    "is_demand_window": False,
                    "price_source": "5min",
                },
                {
                    "timestamp_iso": "2025-01-15T07:00:00Z",
                    "slot_interval_minutes": 30,
                    "buy_price": 0.35,
                    "sell_price": 0.08,
                    "solar_kwh": 3.0,
                    "consumption_kwh": 0.6,
                    "is_demand_window_entry": True,
                    "is_demand_window": True,
                    "price_source": "5min",
                },
            ]
            self.optimizer_summary = None
            self.optimizer_result = None
            self.optimizer_decisions = None

    return MockData()


@pytest.fixture
def config_options():
    """Standard config options for testing."""
    return {
        "battery_target": 80.0,
        "minimum_target_soc": 15.0,
        "export_price_margin": 0.10,
    }


# ---------------------------------------------------------------------------
# Test: Config mapping parity
# ---------------------------------------------------------------------------


class TestBuildOptimizerConfig:
    """Tests for _build_optimizer_config mapping completeness."""

    def test_config_maps_battery_capacity(self, mock_coordinator_data, config_options):
        """Verify battery_capacity_kwh is mapped from const."""
        config = _build_optimizer_config(mock_coordinator_data, config_options)
        assert config.battery_capacity_kwh == 13.5  # BATTERY_CAPACITY_KWH from const

    def test_config_maps_charge_rates(self, mock_coordinator_data, config_options):
        """Verify charge rates are mapped from const."""
        config = _build_optimizer_config(mock_coordinator_data, config_options)
        assert config.charge_rate_kw == 3.3  # CHARGE_RATE_GRID_KW
        assert config.boost_charge_rate_kw == 5.0  # CHARGE_RATE_BOOST_KW
        assert config.solar_charge_rate_kw == 5.0  # CHARGE_RATE_SOLAR_KW

    def test_config_maps_user_target_soc(self, mock_coordinator_data, config_options):
        """Verify user-configured target SOC is mapped."""
        config = _build_optimizer_config(mock_coordinator_data, config_options)
        assert config.demand_window_target_soc_pct == 80.0

    def test_config_maps_user_min_soc(self, mock_coordinator_data, config_options):
        """Verify user-configured minimum SOC is mapped."""
        config = _build_optimizer_config(mock_coordinator_data, config_options)
        assert config.min_soc_pct == 15.0

    def test_config_uses_default_target_soc(self, mock_coordinator_data):
        """Verify default target SOC when not in config."""
        config = _build_optimizer_config(mock_coordinator_data, {})
        # DEFAULT_BATTERY_TARGET is 100.0 in const.py
        assert config.demand_window_target_soc_pct == 100.0

    def test_config_efficiency_defaults(self, mock_coordinator_data, config_options):
        """Verify efficiency defaults are set."""
        config = _build_optimizer_config(mock_coordinator_data, config_options)
        assert config.charge_efficiency == 0.92
        assert config.discharge_efficiency == 0.95

    def test_config_objective_weights_default(
        self, mock_coordinator_data, config_options
    ):
        """Verify objective weight defaults (calibrated formula, not hardcoded 1.0)."""
        config = _build_optimizer_config(mock_coordinator_data, config_options)
        # Issue #779: Penalties are now user-configurable, not auto-computed
        # Values come from config_options or fall back to defaults
        assert config.target_shortfall_penalty_per_pct != 1.0
        assert 0.000 <= config.target_shortfall_penalty_per_pct <= 0.100

    def test_config_penalty_from_options(self, mock_coordinator_data, config_options):
        """target_shortfall_penalty_per_pct is read from config options."""
        custom_options = {**config_options, "target_penalty": 0.050}
        config = _build_optimizer_config(mock_coordinator_data, custom_options)
        assert config.target_shortfall_penalty_per_pct == 0.050

    def test_config_penalty_uses_default_when_not_in_options(
        self, mock_coordinator_data
    ):
        """target_shortfall_penalty_per_pct defaults when not in options."""
        config = _build_optimizer_config(mock_coordinator_data, {})
        # Default is 0.015 (DEFAULT_TARGET_PENALTY)
        assert config.target_shortfall_penalty_per_pct == 0.015

    def test_config_uses_default_allow_dw_entry_under_target(
        self, mock_coordinator_data
    ):
        """Verify allow_dw_entry_under_target defaults to False."""
        config = _build_optimizer_config(mock_coordinator_data, {})
        assert config.allow_dw_entry_under_target is False

    def test_config_maps_allow_dw_entry_under_target_override(
        self, mock_coordinator_data
    ):
        """Verify allow_dw_entry_under_target is mapped from options."""
        config = _build_optimizer_config(
            mock_coordinator_data,
            {"allow_dw_entry_under_target": True},
        )
        assert config.allow_dw_entry_under_target is True

    def test_config_maps_optimization_mode(self, mock_coordinator_data, config_options):
        """Verify optimization mode is mapped from options."""
        config_options["optimization_mode"] = "arbitrage"
        config = _build_optimizer_config(mock_coordinator_data, config_options)
        assert config.optimization_mode == "arbitrage"

    def test_config_maps_self_consumption_inputs(
        self, mock_coordinator_data, config_options
    ):
        """Verify self-consumption economic inputs are mapped."""
        config = _build_optimizer_config(mock_coordinator_data, config_options)
        assert config.effective_cheap_price == pytest.approx(0.12)
        assert config.self_consumption_value_per_kwh == pytest.approx(0.26)
        assert config.export_price_margin == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Test: Summary building
# ---------------------------------------------------------------------------


class TestBuildSummary:
    """Tests for _build_summary with parity info."""

    def test_includes_parity_completeness(self, mock_coordinator_data):
        """Verify parity_completeness_pct included in summary."""
        from custom_components.localshift.engine.optimizer_dp import (
            OptimizerResult,
        )

        result = OptimizerResult(success=True, total_slots=3)
        parity_info = {"completeness_pct": 95.0, "defaulted_fields": {}}
        summary = _build_summary(
            result, "cycle123", "2025-01-15T06:00:00Z", parity_info
        )

        assert "parity_completeness_pct" in summary
        assert summary["parity_completeness_pct"] == 95.0

    def test_includes_alignment_results(self, mock_coordinator_data):
        """Verify alignment results included in summary."""
        from custom_components.localshift.engine.optimizer_dp import (
            OptimizerResult,
        )

        result = OptimizerResult(success=True, total_slots=3)
        alignment = {"valid": True, "issues": [], "warnings": ["test_warning"]}
        summary = _build_summary(
            result, "cycle123", "2025-01-15T06:00:00Z", None, alignment
        )

        assert "alignment_valid" in summary
        assert summary["alignment_valid"] is True
        assert "alignment_warnings" in summary

    def test_includes_alignment_issues(self, mock_coordinator_data):
        """Verify alignment issues included when present."""
        from custom_components.localshift.engine.optimizer_dp import (
            OptimizerResult,
        )

        result = OptimizerResult(success=True, total_slots=3)
        alignment = {"valid": False, "issues": ["test_issue"], "warnings": []}
        summary = _build_summary(
            result, "cycle123", "2025-01-15T06:00:00Z", None, alignment
        )

        assert "alignment_issues" in summary
        assert "test_issue" in summary["alignment_issues"]

    def test_summary_includes_cycle_timestamp_as_computed_at(
        self, mock_coordinator_data
    ):
        """Verify summary exposes cycle timestamp under computed_at for sensors."""
        from custom_components.localshift.engine.optimizer_dp import (
            OptimizerResult,
        )

        result = OptimizerResult(success=True, total_slots=3)
        summary = _build_summary(
            result,
            "cycle123",
            "2025-01-15T06:00:00Z",
        )

        assert summary["cycle_timestamp_iso"] == "2025-01-15T06:00:00Z"
        assert summary["computed_at"] == "2025-01-15T06:00:00Z"

    def test_includes_terminal_diagnostics(self, mock_coordinator_data):
        """Verify terminal diagnostic fields included in summary."""
        from custom_components.localshift.engine.optimizer_dp import OptimizerResult

        result = OptimizerResult(
            success=True,
            total_slots=3,
            forecast_accuracy=0.75,
            accuracy_discount_factor=0.75,
            peak_soc_pct=92.0,
            dw_entry_soc_pct=88.0,
        )
        summary = _build_summary(result, "cycle123", "2025-01-15T06:00:00Z")

        assert summary["forecast_accuracy"] == 0.75
        assert summary["accuracy_discount_factor"] == 0.75
        assert summary["peak_soc_pct"] == 92.0
        assert summary["dw_entry_soc_pct"] == 88.0

    def test_terminal_diagnostics_none_when_not_set(self, mock_coordinator_data):
        """Verify None diagnostics pass through as None."""
        from custom_components.localshift.engine.optimizer_dp import OptimizerResult

        result = OptimizerResult(success=True, total_slots=3)
        summary = _build_summary(result, "cycle123", "2025-01-15T06:00:00Z")

        assert summary["forecast_accuracy"] is None
        assert summary["peak_soc_pct"] is None


class TestNormalizeInitialSoc:
    """Tests for initial SOC normalization and validation guard."""

    def test_percentage_values_not_converted(self, mock_coordinator_data):
        """SOC values 0 < value <= 1.0 should NOT be multiplied by 100 (Issue #424).

        Teslemetry always provides percentage scale (0-100). Values like
        0.5 or 1.0 represent actual low battery percentages, not fractions.
        They will be clamped to min_soc_pct but NOT converted.
        """
        config = _build_optimizer_config(mock_coordinator_data, {})

        normalized, info = _normalize_initial_soc(0.5, config)
        assert normalized == pytest.approx(config.min_soc_pct)
        assert info["normalization"] == "clamped_to_bounds"
        assert info["pre_clamp_soc"] == pytest.approx(0.5)

        normalized, info = _normalize_initial_soc(1.0, config)
        assert normalized == pytest.approx(config.min_soc_pct)
        assert info["pre_clamp_soc"] == pytest.approx(1.0)

    def test_normal_percentage_values_unchanged(self, mock_coordinator_data):
        """Normal percentage values (e.g., 50%, 75%) should pass through unchanged."""
        config = _build_optimizer_config(mock_coordinator_data, {})

        normalized, info = _normalize_initial_soc(50.0, config)
        assert normalized == pytest.approx(50.0)
        assert info["normalization"] == "none"

        normalized, info = _normalize_initial_soc(75.5, config)
        assert normalized == pytest.approx(75.5)
        assert info["normalization"] == "none"

    def test_low_soc_warns_about_misconfiguration(self, mock_coordinator_data, caplog):
        """Values 0 < SOC <= 1.0 should trigger a warning about potential misconfiguration."""
        import logging

        config = _build_optimizer_config(mock_coordinator_data, {})

        with caplog.at_level(logging.WARNING):
            normalized, info = _normalize_initial_soc(0.65, config)

        assert "unusually low" in caplog.text
        assert "percentage scale" in caplog.text

    def test_non_positive_soc_is_rejected(self, mock_coordinator_data):
        """SOC <= 0 should be rejected to avoid invalid optimizer runs."""
        config = _build_optimizer_config(mock_coordinator_data, {})
        normalized, info = _normalize_initial_soc(0.0, config)

        assert normalized is None
        assert info["error"] == "non_positive"

    def test_soc_is_clamped_to_bounds(self, mock_coordinator_data):
        """Out-of-range SOC should be clamped to configured min/max bounds."""
        config = _build_optimizer_config(
            mock_coordinator_data,
            {"minimum_target_soc": 20.0},
        )
        normalized, info = _normalize_initial_soc(110.0, config)

        assert normalized == pytest.approx(config.max_soc_pct)
        assert info["normalization"] == "clamped_to_bounds"
