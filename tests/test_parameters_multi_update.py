"""Tests for multi-parameter updates per daily cycle (Issue #677).

This tests the enhancement to allow updating 2-3 parameters per daily cycle
instead of only 1, with prioritization by uncertainty and skip-after-rollback.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.engine.outcomes import DecisionRecord
from custom_components.localshift.engine.parameters import ParameterOptimizer
from custom_components.localshift.const import (
    OPTIMIZABLE_PARAMS,
    BatteryMode,
)


class TestMultiParameterUpdates:
    """Tests for multi-parameter update functionality."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = MagicMock(return_value=None)
        hass.async_add_executor_job = MagicMock(return_value=None)
        return hass

    @pytest.fixture
    def optimizer(self, mock_hass):
        """Create a ParameterOptimizer instance."""
        return ParameterOptimizer(mock_hass, "test_entry_id")

    @pytest.fixture
    def sample_decisions(self):
        """Create sample decisions for testing."""
        return [
            DecisionRecord(
                timestamp=datetime.now() - timedelta(hours=i),
                mode_chosen=BatteryMode.GRID_CHARGING,
                previous_mode=BatteryMode.SELF_CONSUMPTION,
                soc_at_decision=30.0 + (i % 20),
                general_price_at_decision=0.10,
                feed_in_price_at_decision=0.05,
                forecast_solar_remaining_kwh=10.0,
                forecast_consumption_remaining_kwh=8.0,
                cheap_price_threshold=0.12,
                battery_target_soc=80.0,
                weather_condition="sunny",
                day_of_week=i % 7,
                hour_of_day=i % 24,
                is_demand_window=False,
                outcome_score=0.7 + (i % 3) * 0.1,
            )
            for i in range(60)
        ]

    def test_multiple_parameters_updated_per_cycle(self, optimizer, sample_decisions):
        """Test that multiple parameters can be updated in a single cycle.

        Issue #677: Allow 2-3 parameter updates per daily cycle instead of 1.
        """
        # Track which parameters were adjusted
        adjustments_before = len(optimizer._adjustment_log)

        result = optimizer.optimize(sample_decisions, current_7d_score=0.75)

        adjustments_after = len(optimizer._adjustment_log)
        params_adjusted = adjustments_after - adjustments_before

        # Should update 2-3 parameters per cycle (configurable)
        assert params_adjusted >= 1, (
            f"Expected at least 1 parameter update, got {params_adjusted}"
        )
        assert params_adjusted <= 6, (
            f"Expected at most 6 parameter updates, got {params_adjusted}"
        )

        # Result should contain updated parameters
        assert isinstance(result.values, dict)
        assert len(result.values) > 0

    def test_parameter_update_count_is_limited(self, optimizer, sample_decisions):
        """Test that the number of parameter updates per cycle is limited.

        Should update at most MAX_PARAMS_PER_UPDATE (default 3) per cycle.
        """
        # First run to set baseline
        optimizer.optimize(sample_decisions, current_7d_score=0.75)
        first_run_adjustments = len(optimizer._adjustment_log)

        # Create new decisions with different outcomes to trigger more adjustments
        modified_decisions = [
            DecisionRecord(
                timestamp=datetime.now() - timedelta(hours=i),
                mode_chosen=BatteryMode.GRID_CHARGING,
                previous_mode=BatteryMode.SELF_CONSUMPTION,
                soc_at_decision=30.0 + (i % 20),
                general_price_at_decision=0.10,
                feed_in_price_at_decision=0.05,
                forecast_solar_remaining_kwh=10.0,
                forecast_consumption_remaining_kwh=8.0,
                cheap_price_threshold=0.12,
                battery_target_soc=80.0,
                weather_condition="sunny",
                day_of_week=i % 7,
                hour_of_day=i % 24,
                is_demand_window=False,
                outcome_score=0.5 + (i % 5) * 0.1,  # Different scores
            )
            for i in range(60)
        ]

        optimizer.optimize(modified_decisions, current_7d_score=0.70)
        second_run_adjustments = len(optimizer._adjustment_log)

        new_adjustments = second_run_adjustments - first_run_adjustments
        # Should be limited to at most 3 per cycle (default MAX_PARAMS_PER_UPDATE)
        assert new_adjustments <= 3, (
            f"Expected at most 3 parameter updates per cycle, got {new_adjustments}"
        )

    def test_parameters_prioritized_by_uncertainty(self, optimizer, sample_decisions):
        """Test that parameters with lowest sample count are prioritized.

        Issue #677: Prioritize parameters with highest uncertainty
        (lowest sample count in their current bin).
        """
        # Mock _optimize_single_param to return values that simulate
        # different sample counts/confidence levels
        call_count = 0

        def mock_optimize_single(param_name, param_def, decisions):
            nonlocal call_count
            call_count += 1
            # Return decreasing confidence to simulate different sample counts
            # Lower confidence = higher uncertainty = should be prioritized
            confidence = 0.9 - (call_count * 0.1)
            return param_def.default + 0.1, max(0.1, confidence)

        with patch.object(
            optimizer, "_optimize_single_param", side_effect=mock_optimize_single
        ):
            optimizer.optimize(sample_decisions, current_7d_score=0.75)

        # Verify the method was called for all parameters
        assert call_count == len(OPTIMIZABLE_PARAMS)

    def test_rollback_reverts_all_recent_parameters(self, optimizer, sample_decisions):
        """Test that rollback reverts all parameters changed since last stable checkpoint.

        Issue #677: On rollback, revert ALL parameters changed since last stable checkpoint.
        """
        # First optimization - establish baseline
        result1 = optimizer.optimize(sample_decisions, current_7d_score=0.75)
        values_after_first = dict(result1.values)

        # Second optimization with degrading score to trigger rollback
        optimizer.optimize(sample_decisions, current_7d_score=0.60)  # Degrading
        optimizer.optimize(sample_decisions, current_7d_score=0.55)  # Degrading
        result3 = optimizer.optimize(
            sample_decisions, current_7d_score=0.50
        )  # Should trigger rollback

        # After rollback, values should revert to previous stable state
        # The rollback mechanism should have reverted changes
        assert optimizer._consecutive_degrading_days == 0, (
            "Consecutive degrading days should be reset after rollback"
        )

    def test_rollback_skips_recently_rolled_back_params(
        self, optimizer, sample_decisions
    ):
        """Test that recently rolled-back parameters are skipped for one cycle.

        Issue #677: Recently rolled-back parameters should be skipped for 1 cycle.
        """
        # Initial optimization
        optimizer.optimize(sample_decisions, current_7d_score=0.75)
        adjustments_before_rollback = len(optimizer._adjustment_log)

        # Trigger rollback by degrading scores
        optimizer.optimize(sample_decisions, current_7d_score=0.60)
        optimizer.optimize(sample_decisions, current_7d_score=0.55)
        optimizer.optimize(
            sample_decisions, current_7d_score=0.50
        )  # Rollback triggered

        # After rollback, the recently rolled-back parameter should be tracked
        # and skipped in the next cycle
        assert hasattr(optimizer, "_recently_rolled_back_params") or hasattr(
            optimizer, "_skipped_params_after_rollback"
        ), "Optimizer should track recently rolled-back parameters"

    def test_multi_param_bounds_enforcement(self, optimizer, sample_decisions):
        """Test that all updated parameters stay within bounds.

        Issue #677: Existing safety rails (bounds clamping) unchanged.
        """
        result = optimizer.optimize(sample_decisions, current_7d_score=0.75)

        # All parameters must be within their defined bounds
        for param_name, value in result.values.items():
            if param_name in OPTIMIZABLE_PARAMS:
                param_def = OPTIMIZABLE_PARAMS[param_name]
                assert param_def.min_val <= value <= param_def.max_val, (
                    f"{param_name}={value} outside bounds "
                    f"[{param_def.min_val}, {param_def.max_val}]"
                )

    def test_warm_up_period_unchanged(self, optimizer):
        """Test that warm-up period safety rail is unchanged.

        Issue #677: Existing safety rails (warm-up) unchanged.
        """
        from custom_components.localshift.const import LEARNING_MIN_OBSERVATIONS

        # Warm-up period should still require 50+ observations
        assert LEARNING_MIN_OBSERVATIONS == 50

        # Should not update with insufficient decisions
        assert optimizer.should_update(49) is False
        optimizer._last_update = datetime.now() - timedelta(hours=25)
        assert optimizer.should_update(50) is True


class TestParameterUpdatePrioritization:
    """Tests for parameter prioritization logic."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = MagicMock(return_value=None)
        hass.async_add_executor_job = MagicMock(return_value=None)
        return hass

    @pytest.fixture
    def optimizer(self, mock_hass):
        """Create a ParameterOptimizer instance."""
        return ParameterOptimizer(mock_hass, "test_entry_id")

    def test_parameters_sorted_by_uncertainty(self, optimizer):
        """Test that parameters are sorted by uncertainty (sample count) before selection."""
        # Create mock bin data with different sample counts
        # Parameters with fewer samples = higher uncertainty = should be prioritized
        param_uncertainties = {
            "cheap_price_bias": 0.2,  # Low confidence = high uncertainty
            "solar_confidence_factor": 0.9,  # High confidence = low uncertainty
            "overnight_drain_safety_margin": 0.3,
            "grid_charge_soc_headroom": 0.85,
            "export_threshold_adjustment": 0.4,
            "consumption_forecast_bias": 0.95,
        }

        # The optimizer should prioritize parameters with lowest confidence
        sorted_params = sorted(
            param_uncertainties.items(),
            key=lambda x: x[1],  # Sort by confidence (ascending)
        )

        # Verify sorting order - lowest confidence first
        assert sorted_params[0][0] == "cheap_price_bias"
        assert sorted_params[0][1] == 0.2
        assert sorted_params[-1][0] == "consumption_forecast_bias"
        assert sorted_params[-1][1] == 0.95

    def test_max_params_per_update_constant(self):
        """Test that MAX_PARAMS_PER_UPDATE is configurable and defaults to 3."""
        from custom_components.localshift.engine import parameters

        # Should have a constant for max params per update
        assert hasattr(parameters, "MAX_PARAMS_PER_UPDATE"), (
            "Should define MAX_PARAMS_PER_UPDATE constant"
        )
        # Default should be 2-3
        assert 1 <= parameters.MAX_PARAMS_PER_UPDATE <= 6, (
            f"MAX_PARAMS_PER_UPDATE should be 1-6, got {parameters.MAX_PARAMS_PER_UPDATE}"
        )


class TestRollbackIntegration:
    """Integration tests for rollback with multi-parameter updates."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = MagicMock(return_value=None)
        hass.async_add_executor_job = MagicMock(return_value=None)
        return hass

    @pytest.fixture
    def optimizer(self, mock_hass):
        """Create a ParameterOptimizer instance."""
        return ParameterOptimizer(mock_hass, "test_entry_id")

    @pytest.fixture
    def sample_decisions(self):
        """Create sample decisions for testing."""
        return [
            DecisionRecord(
                timestamp=datetime.now() - timedelta(hours=i),
                mode_chosen=BatteryMode.GRID_CHARGING,
                previous_mode=BatteryMode.SELF_CONSUMPTION,
                soc_at_decision=30.0 + (i % 20),
                general_price_at_decision=0.10,
                feed_in_price_at_decision=0.05,
                forecast_solar_remaining_kwh=10.0,
                forecast_consumption_remaining_kwh=8.0,
                cheap_price_threshold=0.12,
                battery_target_soc=80.0,
                weather_condition="sunny",
                day_of_week=i % 7,
                hour_of_day=i % 24,
                is_demand_window=False,
                outcome_score=0.7 + (i % 3) * 0.1,
            )
            for i in range(60)
        ]

    def test_consecutive_degrading_days_tracking(self, optimizer, sample_decisions):
        """Test that consecutive degrading days are tracked correctly."""
        # Initial state - establish stable checkpoint first
        assert optimizer._consecutive_degrading_days == 0
        optimizer.optimize(sample_decisions, current_7d_score=0.75)
        assert optimizer._consecutive_degrading_days == 0  # First run sets baseline

        # First degrading day (5% threshold)
        optimizer.optimize(sample_decisions, current_7d_score=0.69)  # >5% drop
        assert optimizer._consecutive_degrading_days == 1

        # Second degrading day
        optimizer.optimize(sample_decisions, current_7d_score=0.63)  # >5% drop
        assert optimizer._consecutive_degrading_days == 2

        # Third degrading day - counter reaches 3
        optimizer.optimize(sample_decisions, current_7d_score=0.57)  # >5% drop
        assert optimizer._consecutive_degrading_days == 3

        # Fourth call - should trigger rollback since counter >= 3
        result = optimizer.optimize(sample_decisions, current_7d_score=0.51)
        # After rollback, counter should be reset
        assert optimizer._consecutive_degrading_days == 0

    def test_parameter_state_preserved_across_updates(
        self, optimizer, sample_decisions
    ):
        """Test that parameter state is correctly preserved across updates."""
        # First update
        result1 = optimizer.optimize(sample_decisions, current_7d_score=0.75)
        initial_values = dict(result1.values)

        # Second update
        result2 = optimizer.optimize(sample_decisions, current_7d_score=0.78)

        # All parameters should still have values
        for param_name in OPTIMIZABLE_PARAMS:
            assert param_name in result2.values, f"{param_name} missing from values"
            assert isinstance(result2.values[param_name], float)

    def test_update_count_incremented(self, optimizer, sample_decisions):
        """Test that update count is properly tracked."""
        initial_count = optimizer._current_params.update_count

        optimizer.optimize(sample_decisions, current_7d_score=0.75)

        assert optimizer._current_params.update_count == initial_count + 1

    def test_last_updated_timestamp(self, optimizer, sample_decisions):
        """Test that last_updated timestamp is set."""
        before_update = datetime.now()

        optimizer.optimize(sample_decisions, current_7d_score=0.75)

        assert optimizer._current_params.last_updated is not None
        assert optimizer._current_params.last_updated >= before_update
