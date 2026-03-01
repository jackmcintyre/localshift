"""Tests for shadow runner adapter parity (Phase B #403).

Validates that the adapter layer correctly converts legacy coordinator data
into OptimizerInputs with proper field mapping:

1. Config mapping: all OptimizerConfig fields are correctly populated
2. Slot context parity: legacy slots map 1:1 to SlotContext
3. Alignment validation: issues are detected and reported
4. Completeness tracking: parity_completeness_pct is accurate
"""

import pytest

from custom_components.localshift.computation_engine_lib.optimizer_shadow_runner import (
    _build_optimizer_config,
    _build_slot_contexts,
    _build_summary,
    _normalize_initial_soc,
    _validate_slot_alignment,
    run_shadow_optimizer,
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
            self.optimizer_shadow_summary = None
            self.optimizer_shadow_result = None
            self.optimizer_shadow_decisions = None
            self.optimizer_comparison = None

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
        """Verify objective weight defaults."""
        config = _build_optimizer_config(mock_coordinator_data, config_options)
        assert config.target_shortfall_penalty_per_pct == 1.0
        assert config.cycle_penalty_per_kwh == 0.005

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
# Test: Slot context parity
# ---------------------------------------------------------------------------


class TestBuildSlotContexts:
    """Tests for _build_slot_contexts adapter parity."""

    def test_returns_tuple_with_parity_info(self, mock_coordinator_data):
        """Verify function returns (contexts, parity_info) tuple."""
        result = _build_slot_contexts(mock_coordinator_data)
        assert isinstance(result, tuple)
        assert len(result) == 2
        contexts, parity_info = result
        assert isinstance(contexts, list)
        assert isinstance(parity_info, dict)

    def test_slot_count_matches_legacy(self, mock_coordinator_data):
        """Verify 1:1 slot mapping."""
        contexts, _ = _build_slot_contexts(mock_coordinator_data)
        assert len(contexts) == len(mock_coordinator_data.daily_forecast)

    def test_slot_index_sequential(self, mock_coordinator_data):
        """Verify slot_index is sequential starting from 0."""
        contexts, _ = _build_slot_contexts(mock_coordinator_data)
        for idx, ctx in enumerate(contexts):
            assert ctx.slot_index == idx

    def test_buy_price_mapped(self, mock_coordinator_data):
        """Verify buy_price is correctly mapped."""
        contexts, _ = _build_slot_contexts(mock_coordinator_data)
        assert contexts[0].buy_price == 0.25
        assert contexts[1].buy_price == 0.22
        assert contexts[2].buy_price == 0.35

    def test_sell_price_mapped(self, mock_coordinator_data):
        """Verify sell_price is correctly mapped."""
        contexts, _ = _build_slot_contexts(mock_coordinator_data)
        assert contexts[0].sell_price == 0.08

    def test_solar_kwh_mapped(self, mock_coordinator_data):
        """Verify solar_kwh is correctly mapped."""
        contexts, _ = _build_slot_contexts(mock_coordinator_data)
        assert contexts[0].solar_kwh == 0.5
        assert contexts[1].solar_kwh == 1.5
        assert contexts[2].solar_kwh == 3.0

    def test_consumption_kwh_mapped(self, mock_coordinator_data):
        """Verify consumption_kwh is correctly mapped."""
        contexts, _ = _build_slot_contexts(mock_coordinator_data)
        assert contexts[0].consumption_kwh == 1.2
        assert contexts[1].consumption_kwh == 0.8
        assert contexts[2].consumption_kwh == 0.6

    def test_demand_window_flags_mapped(self, mock_coordinator_data):
        """Verify demand window flags are correctly mapped."""
        contexts, _ = _build_slot_contexts(mock_coordinator_data)
        assert contexts[0].is_demand_window_entry is False
        assert contexts[0].is_demand_window_slot is False
        assert contexts[2].is_demand_window_entry is True
        assert contexts[2].is_demand_window_slot is True

    def test_fallback_to_general_price(self, mock_coordinator_data):
        """Verify buy_price falls back to general_price."""
        mock_coordinator_data.daily_forecast[0]["general_price"] = 0.30
        del mock_coordinator_data.daily_forecast[0]["buy_price"]
        contexts, _ = _build_slot_contexts(mock_coordinator_data)
        assert contexts[0].buy_price == 0.30

    def test_fallback_to_feed_in_price(self, mock_coordinator_data):
        """Verify sell_price falls back to feed_in_price."""
        mock_coordinator_data.daily_forecast[0]["feed_in_price"] = 0.05
        del mock_coordinator_data.daily_forecast[0]["sell_price"]
        contexts, _ = _build_slot_contexts(mock_coordinator_data)
        assert contexts[0].sell_price == 0.05

    def test_default_values_when_missing(self, mock_coordinator_data):
        """Verify safe defaults when all keys missing."""
        mock_coordinator_data.daily_forecast[0] = {}
        contexts, _ = _build_slot_contexts(mock_coordinator_data)
        assert contexts[0].buy_price == 0.0
        assert contexts[0].sell_price == 0.0
        assert contexts[0].solar_kwh == 0.0
        assert contexts[0].consumption_kwh == 0.0


# ---------------------------------------------------------------------------
# Test: Completeness tracking
# ---------------------------------------------------------------------------


class TestParityCompleteness:
    """Tests for parity completeness tracking."""

    def test_completeness_100_when_all_fields_present(self, mock_coordinator_data):
        """Verify 100% completeness when all fields populated."""
        _, parity_info = _build_slot_contexts(mock_coordinator_data)
        assert parity_info["completeness_pct"] == 100.0
        assert parity_info["defaulted_fields"] == {}

    def test_completeness_reduced_when_defaults_used(self, mock_coordinator_data):
        """Verify completeness reduced when defaults used."""
        # Remove buy_price from first slot (and fallback if present)
        mock_coordinator_data.daily_forecast[0].pop("buy_price", None)
        mock_coordinator_data.daily_forecast[0].pop("general_price", None)

        _, parity_info = _build_slot_contexts(mock_coordinator_data)
        assert parity_info["completeness_pct"] < 100.0
        assert "buy_price" in parity_info["defaulted_fields"]
        assert parity_info["defaulted_fields"]["buy_price"] == 1

    def test_total_fields_checked(self, mock_coordinator_data):
        """Verify total_fields_checked is correct."""
        _, parity_info = _build_slot_contexts(mock_coordinator_data)
        # 4 fields checked per slot: buy_price, sell_price, solar_kwh, consumption_kwh
        expected = 4 * len(mock_coordinator_data.daily_forecast)
        assert parity_info["total_fields_checked"] == expected


# ---------------------------------------------------------------------------
# Test: Alignment validation
# ---------------------------------------------------------------------------


class TestValidateSlotAlignment:
    """Tests for _validate_slot_alignment."""

    def test_valid_when_aligned(self, mock_coordinator_data):
        """Verify validation passes for properly aligned data."""
        contexts, _ = _build_slot_contexts(mock_coordinator_data)
        result = _validate_slot_alignment(
            mock_coordinator_data.daily_forecast, contexts
        )
        assert result["valid"] is True
        assert result["issues"] == []

    def test_detects_count_mismatch(self, mock_coordinator_data):
        """Verify detection of slot count mismatch."""
        contexts, _ = _build_slot_contexts(mock_coordinator_data)
        # Remove one context to create mismatch
        contexts = contexts[:-1]
        result = _validate_slot_alignment(
            mock_coordinator_data.daily_forecast, contexts
        )
        assert result["valid"] is False
        assert any("slot_count_mismatch" in issue for issue in result["issues"])

    def test_detects_index_mismatch(self, mock_coordinator_data):
        """Verify detection of slot index mismatch."""
        contexts, _ = _build_slot_contexts(mock_coordinator_data)
        # Corrupt slot_index
        contexts[0].slot_index = 99
        result = _validate_slot_alignment(
            mock_coordinator_data.daily_forecast, contexts
        )
        assert result["valid"] is False
        assert any("index_mismatch" in issue for issue in result["issues"])

    def test_warns_on_missing_timestamp(self, mock_coordinator_data):
        """Verify warning for missing timestamp."""
        mock_coordinator_data.daily_forecast[0]["timestamp_iso"] = ""
        contexts, _ = _build_slot_contexts(mock_coordinator_data)
        result = _validate_slot_alignment(
            mock_coordinator_data.daily_forecast, contexts
        )
        assert any("missing_timestamp" in w for w in result["warnings"])

    def test_warns_on_negative_price(self, mock_coordinator_data):
        """Verify warning for negative buy price."""
        mock_coordinator_data.daily_forecast[0]["buy_price"] = -0.10
        contexts, _ = _build_slot_contexts(mock_coordinator_data)
        result = _validate_slot_alignment(
            mock_coordinator_data.daily_forecast, contexts
        )
        assert any("negative_buy_price" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# Test: Summary building
# ---------------------------------------------------------------------------


class TestBuildSummary:
    """Tests for _build_summary with parity info."""

    def test_includes_parity_completeness(self, mock_coordinator_data):
        """Verify parity_completeness_pct included in summary."""
        from custom_components.localshift.computation_engine_lib.optimizer_dp import (
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
        from custom_components.localshift.computation_engine_lib.optimizer_dp import (
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
        from custom_components.localshift.computation_engine_lib.optimizer_dp import (
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
        from custom_components.localshift.computation_engine_lib.optimizer_dp import (
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


class TestNormalizeInitialSoc:
    """Tests for initial SOC normalization and validation guard."""

    def test_fraction_is_converted_to_percent(self, mock_coordinator_data):
        """SOC values in 0..1 range should be treated as fractional."""
        config = _build_optimizer_config(mock_coordinator_data, {})
        normalized, info = _normalize_initial_soc(0.65, config)

        assert normalized == pytest.approx(65.0)
        assert info["normalization"] == "fraction_to_percent"
        assert info["normalized_soc_pct"] == pytest.approx(65.0)

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


# ---------------------------------------------------------------------------
# Test: Full shadow run integration
# ---------------------------------------------------------------------------


class TestRunShadowOptimizer:
    """Integration tests for run_shadow_optimizer entry point."""

    def test_disabled_optimizer_writes_summary(self, mock_coordinator_data):
        """Verify disabled optimizer writes minimal summary."""
        config = {"optimizer_enabled": False}
        run_shadow_optimizer(mock_coordinator_data, config)

        assert mock_coordinator_data.optimizer_shadow_summary is not None
        assert mock_coordinator_data.optimizer_shadow_summary["enabled"] is False

    def test_enabled_optimizer_writes_results(
        self, mock_coordinator_data, config_options
    ):
        """Verify enabled optimizer writes full results."""
        config_options["optimizer_enabled"] = True
        run_shadow_optimizer(mock_coordinator_data, config_options)

        assert mock_coordinator_data.optimizer_shadow_summary is not None
        assert mock_coordinator_data.optimizer_shadow_result is not None
        assert mock_coordinator_data.optimizer_shadow_decisions is not None

    def test_summary_includes_parity_info(self, mock_coordinator_data, config_options):
        """Verify parity info in summary after run."""
        config_options["optimizer_enabled"] = True
        run_shadow_optimizer(mock_coordinator_data, config_options)

        summary = mock_coordinator_data.optimizer_shadow_summary
        assert "parity_completeness_pct" in summary
        assert "alignment_valid" in summary

    def test_handles_empty_forecast(self, config_options):
        """Verify graceful handling of empty forecast."""

        class EmptyData:
            def __init__(self):
                self.soc = 50.0
                self.daily_forecast = []

        empty_data = EmptyData()
        config_options["optimizer_enabled"] = True
        run_shadow_optimizer(empty_data, config_options)

        assert empty_data.optimizer_shadow_summary is not None
        assert empty_data.optimizer_shadow_summary["success"] is False
        assert (
            "no_slots_available" in empty_data.optimizer_shadow_summary["error_message"]
        )

    def test_invalid_initial_soc_sets_error_summary(self, config_options):
        """Invalid initial SOC should surface a deterministic error summary."""

        class InvalidSocData:
            def __init__(self):
                self.soc = 0.0
                self.daily_forecast = [
                    {
                        "timestamp_iso": "2025-01-15T06:00:00Z",
                        "slot_interval_minutes": 30,
                        "buy_price": 0.25,
                        "sell_price": 0.08,
                        "solar_kwh": 0.5,
                        "consumption_kwh": 1.2,
                    }
                ]

        data = InvalidSocData()
        config_options["optimizer_enabled"] = True
        run_shadow_optimizer(data, config_options)

        assert data.optimizer_shadow_summary is not None
        assert data.optimizer_shadow_summary["success"] is False
        assert data.optimizer_shadow_summary["error_message"] == "invalid_initial_soc"
        assert "initial_soc_info" in data.optimizer_shadow_summary
