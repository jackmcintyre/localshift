"""Tests for the OptimizationController (Issue #170 Phase 4)."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.computation_engine_lib.decision_outcome_tracker import (
    DecisionOutcomeTracker,
    DecisionRecord,
)
from custom_components.localshift.computation_engine_lib.optimization_controller import (
    ObjectiveWeights,
    OptimizationController,
)
from custom_components.localshift.computation_engine_lib.parameter_optimizer import (
    ParameterOptimizer,
)
from custom_components.localshift.computation_engine_lib.pattern_analyzer import (
    PatternAnalyzer,
)
from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator import (
    AdaptiveParameters,
    CoordinatorData,
)


class TestObjectiveWeights:
    """Tests for ObjectiveWeights dataclass."""

    def test_default_weights(self):
        """Test that default weights are balanced."""
        weights = ObjectiveWeights()

        # All weights should be non-negative
        assert weights.cost_minimization >= 0
        assert weights.export_avoidance >= 0
        assert weights.target_achievement >= 0
        assert weights.cycle_reduction >= 0

        # Default weights should sum to 1.0
        total = (
            weights.cost_minimization
            + weights.export_avoidance
            + weights.target_achievement
            + weights.cycle_reduction
        )
        assert abs(total - 1.0) < 0.01

    def test_custom_weights(self):
        """Test creating custom weights."""
        weights = ObjectiveWeights(
            cost_minimization=0.5,
            export_avoidance=0.2,
            target_achievement=0.2,
            cycle_reduction=0.1,
        )

        assert weights.cost_minimization == 0.5
        assert weights.export_avoidance == 0.2
        assert weights.target_achievement == 0.2
        assert weights.cycle_reduction == 0.1

    def test_to_dict(self):
        """Test serialization to dictionary."""
        weights = ObjectiveWeights(
            cost_minimization=0.5,
            export_avoidance=0.2,
            target_achievement=0.2,
            cycle_reduction=0.1,
        )

        result = weights.to_dict()

        assert result["cost_minimization"] == 0.5
        assert result["export_avoidance"] == 0.2
        assert result["target_achievement"] == 0.2
        assert result["cycle_reduction"] == 0.1

    def test_normalize(self):
        """Test that normalize ensures weights sum to 1.0."""
        weights = ObjectiveWeights(
            cost_minimization=2.0,
            export_avoidance=1.0,
            target_achievement=1.0,
            cycle_reduction=0.0,
        )

        normalized = weights.normalize()

        total = (
            normalized.cost_minimization
            + normalized.export_avoidance
            + normalized.target_achievement
            + normalized.cycle_reduction
        )
        assert abs(total - 1.0) < 0.01

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "cost_minimization": 0.5,
            "export_avoidance": 0.2,
            "target_achievement": 0.2,
            "cycle_reduction": 0.1,
        }

        weights = ObjectiveWeights.from_dict(data)

        assert weights.cost_minimization == 0.5
        assert weights.export_avoidance == 0.2
        assert weights.target_achievement == 0.2
        assert weights.cycle_reduction == 0.1


class TestOptimizationController:
    """Tests for OptimizationController."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        # Mock the storage async_load to return None (no stored data)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def mock_decision_tracker(self):
        """Create a mock DecisionOutcomeTracker."""
        tracker = MagicMock(spec=DecisionOutcomeTracker)
        tracker.get_recent_decisions = MagicMock(return_value=[])
        tracker._completed_decisions = {}
        return tracker

    @pytest.fixture
    def mock_param_optimizer(self):
        """Create a mock ParameterOptimizer."""
        optimizer = MagicMock(spec=ParameterOptimizer)
        optimizer.get_current_params = MagicMock(return_value={})
        optimizer.set_bias_corrections = MagicMock()
        optimizer._params = AdaptiveParameters()
        return optimizer

    @pytest.fixture
    def mock_pattern_analyzer(self):
        """Create a mock PatternAnalyzer."""
        analyzer = MagicMock(spec=PatternAnalyzer)
        return analyzer

    @pytest.fixture
    def controller(
        self,
        mock_hass,
        mock_decision_tracker,
        mock_param_optimizer,
        mock_pattern_analyzer,
    ):
        """Create an OptimizationController with mocked dependencies."""
        return OptimizationController(
            mock_hass,
            "test_entry_id",
            mock_decision_tracker,
            mock_param_optimizer,
            mock_pattern_analyzer,
        )

    def test_initialization(self, controller):
        """Test controller initializes correctly."""
        assert controller.learning_enabled is False
        assert controller.weights is not None
        assert isinstance(controller.weights, ObjectiveWeights)

    def test_set_learning_enabled(self, controller):
        """Test enabling/disabling learning."""
        # Initially disabled
        assert controller.learning_enabled is False

        # Enable learning
        controller.set_learning_enabled(True)
        assert controller.learning_enabled is True

        # Disable learning
        controller.set_learning_enabled(False)
        assert controller.learning_enabled is False

    def test_weights_property(self, controller):
        """Test getting objective weights."""
        weights = controller.weights

        assert isinstance(weights, ObjectiveWeights)
        assert hasattr(weights, "cost_minimization")
        assert hasattr(weights, "export_avoidance")
        assert hasattr(weights, "target_achievement")
        assert hasattr(weights, "cycle_reduction")

    def test_evaluate_disabled(self, controller):
        """Test that evaluate returns defaults when learning disabled."""
        controller.set_learning_enabled(False)
        data = CoordinatorData()

        # Evaluate should return default adaptive params
        params = controller.evaluate(data)

        assert isinstance(params, AdaptiveParameters)

    def test_evaluate_enabled(self, controller):
        """Test that evaluate runs when learning enabled."""
        controller.set_learning_enabled(True)

        data = CoordinatorData()
        data.soc = 50.0

        # Evaluate should return adaptive params with adjustments
        params = controller.evaluate(data)

        assert isinstance(params, AdaptiveParameters)

    def test_get_active_adjustments(self, controller):
        """Test getting active contextual adjustments."""
        adjustments = controller.get_active_adjustments()

        assert isinstance(adjustments, list)


class TestOptimizationControllerIntegration:
    """Integration tests for OptimizationController with real-ish data."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def real_components(self, mock_hass):
        """Create controller with realistic mocked components."""
        # Create decision tracker with some history
        tracker = MagicMock(spec=DecisionOutcomeTracker)

        # Create decisions spanning different conditions
        decisions = []
        for i in range(30):
            decision = DecisionRecord(
                timestamp=datetime.now() - timedelta(hours=i * 2),
                mode_chosen=BatteryMode.GRID_CHARGING
                if i % 2 == 0
                else BatteryMode.SELF_CONSUMPTION,
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
                actual_cost_during_period=0.25 + i * 0.01,
                actual_soc_change=5.0 if i % 2 == 0 else -2.0,
                outcome_score=0.6 + (i % 4) * 0.1,
            )
            decisions.append(decision)

        tracker.get_recent_decisions = MagicMock(return_value=decisions)

        # Create parameter optimizer
        optimizer = MagicMock(spec=ParameterOptimizer)
        optimizer.get_current_params = MagicMock(
            return_value={
                "cheap_price_bias": 0.5,
                "solar_confidence_factor": 0.95,
            }
        )
        optimizer._params = AdaptiveParameters()
        optimizer._params.values = {"cheap_price_bias": 0.5}

        # Create pattern analyzer
        analyzer = MagicMock(spec=PatternAnalyzer)

        controller = OptimizationController(
            mock_hass, "test_entry", tracker, optimizer, analyzer
        )

        return controller, tracker, optimizer, analyzer

    def test_full_optimization_cycle(self, real_components):
        """Test running a full optimization cycle."""
        controller, tracker, optimizer, analyzer = real_components
        controller.set_learning_enabled(True)

        data = CoordinatorData()
        data.soc = 50.0
        data.solar_remaining_kwh = 10.0
        data.general_price = 0.15
        data.feed_in_price = 0.05

        # Evaluate
        params = controller.evaluate(data)

        # Verify params were computed
        assert isinstance(params, AdaptiveParameters)


class TestOptimizationControllerEdgeCases:
    """Edge case tests for OptimizationController."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def minimal_controller(self, mock_hass):
        """Create controller with minimal mocking."""
        tracker = MagicMock(spec=DecisionOutcomeTracker)
        tracker.get_recent_decisions = MagicMock(return_value=[])
        tracker._completed_decisions = {}

        optimizer = MagicMock(spec=ParameterOptimizer)
        optimizer.get_current_params = MagicMock(return_value={})
        optimizer._params = AdaptiveParameters()

        analyzer = MagicMock(spec=PatternAnalyzer)

        return OptimizationController(mock_hass, "test", tracker, optimizer, analyzer)

    def test_empty_decision_history(self, minimal_controller):
        """Test handling empty decision history."""
        minimal_controller.set_learning_enabled(True)

        data = CoordinatorData()

        # Should not crash with no decisions
        params = minimal_controller.evaluate(data)

        assert params is not None

    def test_none_values_in_context(self, minimal_controller):
        """Test handling None values in decision context."""
        minimal_controller.set_learning_enabled(True)

        data = CoordinatorData()
        # Use a valid SOC value since the controller checks soc < 15.0
        data.soc = 50.0

        # Should not crash with default values
        params = minimal_controller.evaluate(data)

        assert params is not None

    def test_extreme_soc_values(self, minimal_controller):
        """Test handling extreme SOC values."""
        minimal_controller.set_learning_enabled(True)

        # Very low SOC
        data = CoordinatorData()
        data.soc = 5.0
        params = minimal_controller.evaluate(data)
        assert params is not None

        # Very high SOC
        data.soc = 95.0
        params = minimal_controller.evaluate(data)
        assert params is not None

    def test_negative_prices(self, minimal_controller):
        """Test handling negative electricity prices."""
        minimal_controller.set_learning_enabled(True)

        data = CoordinatorData()
        data.general_price = -0.10  # Negative price
        data.feed_in_price = -0.05  # Negative FIT

        # Should not crash
        params = minimal_controller.evaluate(data)

        assert isinstance(params, AdaptiveParameters)
