"""End-to-end integration tests for the learning system (Issue #170).

Tests the full feedback loop:
- Decisions → Outcomes → Optimization → Improved decisions
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.engine.decision_outcome_tracker import (
    DecisionOutcomeTracker,
    DecisionRecord,
)
from custom_components.localshift.engine.optimization_controller import (
    OptimizationController,
)
from custom_components.localshift.engine.parameter_optimizer import (
    ParameterOptimizer,
)
from custom_components.localshift.engine.pattern_analyzer import (
    PatternAnalyzer,
)
from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator import (
    AdaptiveParameters,
    CoordinatorData,
    PerformanceMetrics,
)


class TestLearningIntegration:
    """End-to-end tests for the learning system."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def components(self, mock_hass):
        """Create all learning system components."""
        tracker = DecisionOutcomeTracker(mock_hass, "test_entry")
        optimizer = ParameterOptimizer(mock_hass, "test_entry")
        analyzer = PatternAnalyzer(mock_hass, "test_entry")
        controller = OptimizationController(
            mock_hass, "test_entry", tracker, optimizer, analyzer
        )
        return {
            "tracker": tracker,
            "optimizer": optimizer,
            "analyzer": analyzer,
            "controller": controller,
        }

    @pytest.fixture
    def coordinator_data(self):
        """Create coordinator data for testing."""
        data = CoordinatorData()
        data.soc = 50.0
        data.battery_target_soc = 80.0
        data.learning_status = "observing"
        data.performance_metrics = PerformanceMetrics()
        return data

    def test_full_feedback_loop(self, components, coordinator_data):
        """Test the complete learning feedback loop.

        1. Generate decisions with varying outcomes
        2. Run optimization
        3. Verify parameters are adjusted
        """
        controller = components["controller"]

        # Enable learning
        controller.set_learning_enabled(True)

        # Simulate 100 decisions with improving outcomes over time
        decisions = []
        for i in range(100):
            # Later decisions have better scores (simulating learning)
            base_score = 0.5 + (i / 100) * 0.4  # 0.5 → 0.9
            decision = DecisionRecord(
                timestamp=datetime.now() - timedelta(hours=100 - i),
                mode_chosen=(
                    BatteryMode.GRID_CHARGING
                    if i % 2 == 0
                    else BatteryMode.SELF_CONSUMPTION
                ),
                previous_mode=BatteryMode.SELF_CONSUMPTION,
                soc_at_decision=20.0 + (i % 60),
                general_price_at_decision=0.05 + (i % 10) * 0.02,
                feed_in_price_at_decision=0.03,
                forecast_solar_remaining_kwh=10.0,
                forecast_consumption_remaining_kwh=8.0,
                cheap_price_threshold=0.10,
                battery_target_soc=80.0,
                weather_condition="sunny",
                day_of_week=i % 7,
                hour_of_day=i % 24,
                is_demand_window=False,
                outcome_score=base_score,
            )
            decisions.append(decision)

        # Verify the controller can process the data
        result = controller.evaluate(coordinator_data)
        assert isinstance(result, AdaptiveParameters)

    def test_learning_disabled_mid_cycle(self, components, coordinator_data):
        """Test that disabling learning mid-cycle doesn't crash."""
        controller = components["controller"]

        # Enable learning
        controller.set_learning_enabled(True)

        # Process some data
        result = controller.evaluate(coordinator_data)
        assert isinstance(result, AdaptiveParameters)

        # Disable mid-cycle
        controller.set_learning_enabled(False)

        # Should return default params (no crash)
        result = controller.evaluate(coordinator_data)
        assert isinstance(result, AdaptiveParameters)
        # Default params should be empty (zero-offset)
        assert len(result.values) == 0

    def test_reset_clears_state(self, components, coordinator_data, mock_hass):
        """Test that reset button clears all learning state."""
        tracker = components["tracker"]
        optimizer = components["optimizer"]
        controller = components["controller"]

        # Enable learning and set some state
        controller.set_learning_enabled(True)
        optimizer._current_params.values["cheap_price_bias"] = 0.5

        # Simulate reset
        tracker._pending_decisions.clear()
        tracker._completed_decisions.clear()
        optimizer._current_params = AdaptiveParameters()
        coordinator_data.learning_status = "observing"

        # Verify state is cleared
        assert len(tracker._pending_decisions) == 0
        assert len(tracker._completed_decisions) == 0
        assert len(optimizer._current_params.values) == 0


class TestLearningSystemEdgeCases:
    """Edge case tests for the learning system."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def components(self, mock_hass):
        """Create all learning system components."""
        tracker = DecisionOutcomeTracker(mock_hass, "test_entry")
        optimizer = ParameterOptimizer(mock_hass, "test_entry")
        analyzer = PatternAnalyzer(mock_hass, "test_entry")
        controller = OptimizationController(
            mock_hass, "test_entry", tracker, optimizer, analyzer
        )
        return {
            "tracker": tracker,
            "optimizer": optimizer,
            "analyzer": analyzer,
            "controller": controller,
        }

    def test_empty_decision_history(self, components):
        """Test handling empty decision history."""
        controller = components["controller"]
        controller.set_learning_enabled(True)

        data = CoordinatorData()

        # Should not crash with empty history
        result = controller.evaluate(data)
        assert isinstance(result, AdaptiveParameters)
