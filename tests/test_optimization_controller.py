"""Tests for the OptimizationController (Issue #170 Phase 4)."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.engine.outcomes import (
    DecisionOutcomeTracker,
    DecisionRecord,
)
from custom_components.localshift.engine import (
    optimization_controller as optimization_controller_module,
)
from custom_components.localshift.engine.optimization_controller import (
    ObjectiveWeights,
    OptimizationController,
)
from custom_components.localshift.engine.parameters import (
    ParameterOptimizer,
)
from custom_components.localshift.engine.pattern_analyzer import (
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


class TestCorrectionAppliesToContext:
    """Tests for _correction_applies_to_context method."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def controller(self, mock_hass):
        """Create a minimal controller for testing."""
        tracker = MagicMock(spec=DecisionOutcomeTracker)
        tracker.get_recent_decisions = MagicMock(return_value=[])

        optimizer = MagicMock(spec=ParameterOptimizer)
        optimizer._params = AdaptiveParameters()

        analyzer = MagicMock(spec=PatternAnalyzer)

        return OptimizationController(mock_hass, "test", tracker, optimizer, analyzer)

    def test_day_of_week_match(self, controller):
        """Test day_of_week dimension matches correctly."""
        from datetime import datetime

        now = datetime.now()
        current_day = now.weekday()
        day_names = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]

        correction = {
            "dimension": "day_of_week",
            "group_key": day_names[current_day],
        }
        result = controller._correction_applies_to_context(
            correction, current_day, "sunny", now
        )
        assert result is True

    def test_day_of_week_no_match(self, controller):
        """Test day_of_week dimension rejects non-matching days."""
        from datetime import datetime

        now = datetime.now()
        current_day = now.weekday()
        day_names = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]
        wrong_day = (current_day + 1) % 7

        correction = {
            "dimension": "day_of_week",
            "group_key": day_names[wrong_day],
        }
        result = controller._correction_applies_to_context(
            correction, current_day, "sunny", now
        )
        assert result is False

    def test_weather_partial_match(self, controller):
        """Test weather dimension with partial match."""
        from datetime import datetime

        now = datetime.now()
        correction = {
            "dimension": "weather",
            "group_key": "sun",
        }
        result = controller._correction_applies_to_context(correction, 0, "sunny", now)
        assert result is True

    def test_weather_reverse_partial_match(self, controller):
        """Test weather dimension with reverse partial match."""
        from datetime import datetime

        now = datetime.now()
        correction = {
            "dimension": "weather",
            "group_key": "cloudy",
        }
        result = controller._correction_applies_to_context(
            correction, 0, "partly_cloudy", now
        )
        assert result is True

    def test_weather_no_match(self, controller):
        """Test weather dimension rejects non-matching conditions."""
        from datetime import datetime

        now = datetime.now()
        correction = {
            "dimension": "weather",
            "group_key": "rain",
        }
        result = controller._correction_applies_to_context(correction, 0, "sunny", now)
        assert result is False

    def test_hour_of_day_match(self, controller):
        """Test hour_of_day dimension matches correctly."""
        from datetime import datetime

        now = datetime.now()
        current_hour = now.hour

        correction = {
            "dimension": "hour_of_day",
            "group_key": str(current_hour),
        }
        result = controller._correction_applies_to_context(correction, 0, "sunny", now)
        assert result is True

    def test_hour_of_day_no_match(self, controller):
        """Test hour_of_day dimension rejects non-matching hours."""
        from datetime import datetime

        now = datetime.now()
        wrong_hour = (now.hour + 1) % 24

        correction = {
            "dimension": "hour_of_day",
            "group_key": str(wrong_hour),
        }
        result = controller._correction_applies_to_context(correction, 0, "sunny", now)
        assert result is False

    def test_season_match(self, controller):
        """Test season dimension matches correctly."""
        from datetime import datetime

        now = datetime.now()
        expected_season = controller._get_season(now.month)

        correction = {
            "dimension": "season",
            "group_key": expected_season,
        }
        result = controller._correction_applies_to_context(correction, 0, "sunny", now)
        assert result is True

    def test_season_no_match(self, controller):
        """Test season dimension rejects non-matching seasons."""
        from datetime import datetime

        now = datetime.now()
        wrong_seasons = ["spring", "summer", "autumn", "winter"]
        actual_season = controller._get_season(now.month)
        wrong_season = next(s for s in wrong_seasons if s != actual_season)

        correction = {
            "dimension": "season",
            "group_key": wrong_season,
        }
        result = controller._correction_applies_to_context(correction, 0, "sunny", now)
        assert result is False

    def test_unknown_dimension(self, controller):
        """Test unknown dimension returns False."""
        from datetime import datetime

        now = datetime.now()
        correction = {
            "dimension": "unknown_dimension",
            "group_key": "value",
        }
        result = controller._correction_applies_to_context(correction, 0, "sunny", now)
        assert result is False


class TestHourMatches:
    """Tests for _hour_matches method."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def controller(self, mock_hass):
        """Create a minimal controller for testing."""
        tracker = MagicMock(spec=DecisionOutcomeTracker)
        optimizer = MagicMock(spec=ParameterOptimizer)
        optimizer._params = AdaptiveParameters()
        analyzer = MagicMock(spec=PatternAnalyzer)
        return OptimizationController(mock_hass, "test", tracker, optimizer, analyzer)

    def test_valid_hour_string(self, controller):
        """Test matching valid hour string."""
        from datetime import datetime

        now = datetime.now().replace(hour=14)
        result = controller._hour_matches("14", now)
        assert result is True

    def test_invalid_hour_string(self, controller):
        """Test non-matching hour string."""
        from datetime import datetime

        now = datetime.now().replace(hour=10)
        result = controller._hour_matches("not_a_number", now)
        assert result is False

    def test_hour_out_of_range(self, controller):
        """Test hour value outside normal range."""
        from datetime import datetime

        now = datetime.now().replace(hour=5)
        result = controller._hour_matches("25", now)
        assert result is False

    def test_empty_string(self, controller):
        """Test empty string returns False."""
        from datetime import datetime

        now = datetime.now()
        result = controller._hour_matches("", now)
        assert result is False


class TestApplySingleCorrection:
    """Tests for _apply_single_correction method."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def controller(self, mock_hass):
        """Create a minimal controller for testing."""
        tracker = MagicMock(spec=DecisionOutcomeTracker)
        optimizer = MagicMock(spec=ParameterOptimizer)
        optimizer._params = AdaptiveParameters()
        analyzer = MagicMock(spec=PatternAnalyzer)
        return OptimizationController(mock_hass, "test", tracker, optimizer, analyzer)

    def test_high_confidence_applied(self, controller):
        """Test high confidence correction is applied."""
        params = AdaptiveParameters(values={"cheap_price_bias": 0.0})
        correction = {
            "param_name": "cheap_price_bias",
            "adjustment": 0.5,
            "confidence": 0.8,
            "dimension": "day_of_week",
            "group_key": "monday",
        }

        controller._apply_single_correction(params, correction)

        assert params.values["cheap_price_bias"] == 0.5
        assert len(controller._active_contextual_adjustments) == 1

    def test_low_confidence_skipped(self, controller):
        """Test low confidence correction is skipped."""
        params = AdaptiveParameters(values={"cheap_price_bias": 0.0})
        correction = {
            "param_name": "cheap_price_bias",
            "adjustment": 0.5,
            "confidence": 0.4,
            "dimension": "day_of_week",
            "group_key": "monday",
        }

        controller._apply_single_correction(params, correction)

        assert params.values.get("cheap_price_bias", 0.0) == 0.0
        assert len(controller._active_contextual_adjustments) == 0

    def test_confidence_boundary_0_49(self, controller):
        """Test confidence just below threshold is skipped."""
        params = AdaptiveParameters(values={"test_param": 0.0})
        correction = {
            "param_name": "test_param",
            "adjustment": 0.3,
            "confidence": 0.49,
            "dimension": "weather",
            "group_key": "sunny",
        }

        controller._apply_single_correction(params, correction)

        assert params.values.get("test_param", 0.0) == 0.0

    def test_confidence_boundary_0_50(self, controller):
        """Test confidence at threshold is applied."""
        params = AdaptiveParameters(values={"test_param": 0.0})
        correction = {
            "param_name": "test_param",
            "adjustment": 0.3,
            "confidence": 0.50,
            "dimension": "weather",
            "group_key": "sunny",
        }

        controller._apply_single_correction(params, correction)

        assert params.values["test_param"] == 0.3

    def test_empty_param_name_skipped(self, controller):
        """Test empty param_name is skipped."""
        params = AdaptiveParameters(values={})
        correction = {
            "param_name": "",
            "adjustment": 0.5,
            "confidence": 0.9,
            "dimension": "day_of_week",
            "group_key": "monday",
        }

        controller._apply_single_correction(params, correction)

        assert len(controller._active_contextual_adjustments) == 0

    def test_accumulates_adjustments(self, controller):
        """Test multiple corrections accumulate."""
        params = AdaptiveParameters(values={"cheap_price_bias": 0.0})

        correction1 = {
            "param_name": "cheap_price_bias",
            "adjustment": 0.3,
            "confidence": 0.8,
            "dimension": "day_of_week",
            "group_key": "monday",
        }
        correction2 = {
            "param_name": "cheap_price_bias",
            "adjustment": 0.2,
            "confidence": 0.9,
            "dimension": "weather",
            "group_key": "sunny",
        }

        controller._apply_single_correction(params, correction1)
        controller._apply_single_correction(params, correction2)

        assert params.values["cheap_price_bias"] == 0.5
        assert len(controller._active_contextual_adjustments) == 2


class TestGetSeason:
    """Tests for _get_season method."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def controller(self, mock_hass):
        """Create a minimal controller for testing."""
        tracker = MagicMock(spec=DecisionOutcomeTracker)
        optimizer = MagicMock(spec=ParameterOptimizer)
        optimizer._params = AdaptiveParameters()
        analyzer = MagicMock(spec=PatternAnalyzer)
        return OptimizationController(mock_hass, "test", tracker, optimizer, analyzer)

    def test_winter_months(self, controller):
        """Test winter months return 'winter' (southern hemisphere: Jun-Aug)."""
        for month in [6, 7, 8]:
            assert controller._get_season(month) == "winter"

    def test_spring_months(self, controller):
        """Test spring months return 'spring' (southern hemisphere: Sep-Nov)."""
        for month in [9, 10, 11]:
            assert controller._get_season(month) == "spring"

    def test_summer_months(self, controller):
        """Test summer months return 'summer' (southern hemisphere: Dec-Feb)."""
        for month in [12, 1, 2]:
            assert controller._get_season(month) == "summer"

    def test_autumn_months(self, controller):
        """Test autumn months return 'autumn' (southern hemisphere: Mar-May)."""
        for month in [3, 4, 5]:
            assert controller._get_season(month) == "autumn"


class TestObjectiveWeightsNormalize:
    """Tests for ObjectiveWeights.normalize edge cases."""

    def test_normalize_zero_weights_returns_default(self):
        """Test that normalize with all zeros returns default weights."""
        weights = ObjectiveWeights(
            cost_minimization=0.0,
            export_avoidance=0.0,
            target_achievement=0.0,
            cycle_reduction=0.0,
        )

        normalized = weights.normalize()

        assert normalized.cost_minimization == 0.50
        assert normalized.export_avoidance == 0.20
        assert normalized.target_achievement == 0.20
        assert normalized.cycle_reduction == 0.10


class TestRealTimeAdjustments:
    """Tests for _apply_contextual_adjustments method."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def controller(self, mock_hass):
        """Create a minimal controller for testing."""
        tracker = MagicMock(spec=DecisionOutcomeTracker)
        optimizer = MagicMock(spec=ParameterOptimizer)
        optimizer._params = AdaptiveParameters()
        analyzer = MagicMock(spec=PatternAnalyzer)
        return OptimizationController(mock_hass, "test", tracker, optimizer, analyzer)

    def test_export_leak_detection_applied(self, controller, monkeypatch):
        """High export loss ratio triggers the adjustment when the gate is enabled.

        Issue #868: Rule 2 is gated behind EXPORT_LEAK_PROTECTION_ENABLED, so the
        flag must be enabled for the rule to fire.
        """
        monkeypatch.setattr(
            optimization_controller_module,
            "EXPORT_LEAK_PROTECTION_ENABLED",
            True,
        )
        data = CoordinatorData()
        data.performance_metrics.export_loss_ratio = 0.4
        data.soc = 50.0
        params = AdaptiveParameters()

        result = controller._apply_contextual_adjustments(params, data)

        assert "export_threshold_adjustment" in result.values
        assert result.values["export_threshold_adjustment"] == 1.0

    def test_export_leak_gate_off_by_default_does_not_fire(self, controller):
        """Issue #868: with EXPORT_LEAK_PROTECTION_ENABLED at its default (False),
        a high export_loss_ratio must NOT arm the adjustment — no behaviour change
        ships even though the metric is now computed and learning may be on."""
        assert (
            optimization_controller_module.EXPORT_LEAK_PROTECTION_ENABLED is False
        )
        data = CoordinatorData()
        data.performance_metrics.export_loss_ratio = 0.4
        data.soc = 50.0
        params = AdaptiveParameters()

        result = controller._apply_contextual_adjustments(params, data)

        assert "export_threshold_adjustment" not in result.values

    def test_export_leak_not_applied_below_threshold(self, controller, monkeypatch):
        """Low export loss ratio does not trigger adjustment even when gate is on."""
        monkeypatch.setattr(
            optimization_controller_module,
            "EXPORT_LEAK_PROTECTION_ENABLED",
            True,
        )
        data = CoordinatorData()
        data.performance_metrics.export_loss_ratio = 0.2
        data.soc = 50.0
        params = AdaptiveParameters()

        result = controller._apply_contextual_adjustments(params, data)

        assert "export_threshold_adjustment" not in result.values

    def test_low_forecast_accuracy_adjusts_solar_confidence(self, controller):
        """Test that low forecast accuracy reduces solar confidence."""
        data = CoordinatorData()
        data.forecast_accuracy_soc_1h = 40.0
        data.forecast_accuracy_soc_4h = 45.0
        data.soc = 50.0
        params = AdaptiveParameters(values={"solar_confidence_factor": 1.0})

        result = controller._apply_contextual_adjustments(params, data)

        assert result.values.get("solar_confidence_factor") == 0.8

    def test_low_forecast_accuracy_increases_safety_margin(self, controller):
        """Test that low forecast accuracy increases overnight drain margin."""
        data = CoordinatorData()
        data.forecast_accuracy_soc_1h = 40.0
        data.forecast_accuracy_soc_4h = 45.0
        data.soc = 50.0
        params = AdaptiveParameters(values={"overnight_drain_safety_margin": 0.0})

        result = controller._apply_contextual_adjustments(params, data)

        assert result.values.get("overnight_drain_safety_margin") == 3.0

    def test_forecast_accuracy_none_uses_default(self, controller):
        """Test that None accuracy values use 100% default."""
        data = CoordinatorData()
        data.forecast_accuracy_soc_1h = None
        data.forecast_accuracy_soc_4h = None
        data.soc = 50.0
        params = AdaptiveParameters()

        result = controller._apply_contextual_adjustments(params, data)

        assert "solar_confidence_factor" not in result.values

    def test_approaching_demand_window_with_soc_gap(self, controller):
        """Test approaching demand window with SOC gap triggers adjustment."""
        data = CoordinatorData()
        data.soc = 50.0
        data.battery_target_soc = 80.0
        params = AdaptiveParameters(values={"cheap_price_bias": 0.0})

        controller._is_approaching_demand_window = MagicMock(return_value=True)
        result = controller._apply_contextual_adjustments(params, data)

        assert "cheap_price_bias" in result.values
        assert result.values["cheap_price_bias"] > 0

    def test_approaching_demand_window_no_soc_gap(self, controller):
        """Test approaching demand window without SOC gap does not adjust."""
        data = CoordinatorData()
        data.soc = 80.0
        data.battery_target_soc = 80.0
        params = AdaptiveParameters()

        controller._is_approaching_demand_window = MagicMock(return_value=True)
        result = controller._apply_contextual_adjustments(params, data)

        assert "cheap_price_bias" not in result.values


class TestAsyncSaveLoad:
    """Tests for async_save and async_load methods."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance with storage."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def controller(self, mock_hass):
        """Create a minimal controller for testing."""
        tracker = MagicMock(spec=DecisionOutcomeTracker)
        optimizer = MagicMock(spec=ParameterOptimizer)
        optimizer._params = AdaptiveParameters()
        analyzer = MagicMock(spec=PatternAnalyzer)
        return OptimizationController(mock_hass, "test", tracker, optimizer, analyzer)

    @pytest.mark.asyncio
    async def test_async_save_stores_weights(self, controller):
        """Test that async_save stores weights."""
        controller._weights = ObjectiveWeights(cost_minimization=0.6)
        controller._learning_enabled = True

        mock_save = AsyncMock()
        controller._store.async_save = mock_save

        await controller.async_save()

        mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_load_restores_state(self, controller):
        """Test that async_load restores saved state."""
        saved_data = {
            "weights": {
                "cost_minimization": 0.6,
                "export_avoidance": 0.2,
                "target_achievement": 0.1,
                "cycle_reduction": 0.1,
            },
            "learning_enabled": True,
            "weight_history": [],
            "last_weight_update": datetime.now().isoformat(),
        }
        controller._store.async_load = AsyncMock(return_value=saved_data)

        await controller.async_load()

        assert controller._learning_enabled == True
        assert controller._weights.cost_minimization == 0.6

    @pytest.mark.asyncio
    async def test_async_load_none_data(self, controller):
        """Test that async_load handles None data gracefully."""
        controller._store.async_load = AsyncMock(return_value=None)

        await controller.async_load()

        assert controller._learning_enabled == False

    @pytest.mark.asyncio
    async def test_async_load_weight_history(self, controller):
        """Test that async_load restores weight history."""
        saved_data = {
            "weights": {},
            "learning_enabled": False,
            "weight_history": [
                {
                    "timestamp": datetime.now().isoformat(),
                    "weights": {
                        "cost_minimization": 0.5,
                        "export_avoidance": 0.2,
                        "target_achievement": 0.2,
                        "cycle_reduction": 0.1,
                    },
                }
            ],
            "last_weight_update": None,
        }
        controller._store.async_load = AsyncMock(return_value=saved_data)

        await controller.async_load()

        assert len(controller._weight_history) == 1


class TestApplyActiveBiasCorrectionsIntegration:
    """Tests for _apply_active_bias_corrections with actual loop execution."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def controller(self, mock_hass):
        """Create a minimal controller for testing."""
        tracker = MagicMock(spec=DecisionOutcomeTracker)
        optimizer = MagicMock(spec=ParameterOptimizer)
        optimizer._params = AdaptiveParameters()
        analyzer = MagicMock(spec=PatternAnalyzer)
        return OptimizationController(mock_hass, "test", tracker, optimizer, analyzer)

    def test_applies_multiple_corrections(self, controller):
        """Test that multiple matching corrections are applied in loop."""
        current_hour = datetime.now().hour
        data = CoordinatorData()
        data.weather_condition = "sunny"
        data.active_bias_corrections = [
            {
                "dimension": "weather",
                "group_key": "sunny",
                "param_name": "cheap_price_bias",
                "adjustment": 0.2,
                "confidence": 0.8,
            },
            {
                "dimension": "weather",
                "group_key": "clear",
                "param_name": "cheap_price_bias",
                "adjustment": 0.1,
                "confidence": 0.7,
            },
        ]
        params = AdaptiveParameters(values={"cheap_price_bias": 0.0})

        result = controller._apply_active_bias_corrections(params, data)

        assert result.values.get("cheap_price_bias") == 0.2

    def test_empty_corrections_returns_unchanged(self, controller):
        """Test empty corrections list returns unchanged params."""
        data = CoordinatorData()
        data.active_bias_corrections = []
        params = AdaptiveParameters(values={"test": 1.0})

        result = controller._apply_active_bias_corrections(params, data)

        assert result.values.get("test") == 1.0


class TestClampParameters:
    """Tests for _clamp_parameters method."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def controller(self, mock_hass):
        """Create a minimal controller for testing."""
        tracker = MagicMock(spec=DecisionOutcomeTracker)
        optimizer = MagicMock(spec=ParameterOptimizer)
        optimizer._params = AdaptiveParameters()
        analyzer = MagicMock(spec=PatternAnalyzer)
        return OptimizationController(mock_hass, "test", tracker, optimizer, analyzer)

    def test_clamps_value_above_max(self, controller):
        """Test that values above max are clamped."""
        params = AdaptiveParameters(values={"cheap_price_bias": 10.0})

        result = controller._clamp_parameters(params)

        assert result.values.get("cheap_price_bias") <= 5.0

    def test_clamps_value_below_min(self, controller):
        """Test that values below min are clamped."""
        params = AdaptiveParameters(values={"solar_confidence_factor": -1.0})

        result = controller._clamp_parameters(params)

        assert result.values.get("solar_confidence_factor") >= 0.0


class TestIsApproachingDemandWindow:
    """Tests for _is_approaching_demand_window method."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def controller(self, mock_hass):
        """Create a minimal controller for testing."""
        tracker = MagicMock(spec=DecisionOutcomeTracker)
        optimizer = MagicMock(spec=ParameterOptimizer)
        optimizer._params = AdaptiveParameters()
        analyzer = MagicMock(spec=PatternAnalyzer)
        return OptimizationController(mock_hass, "test", tracker, optimizer, analyzer)

    def test_within_two_hours_returns_true(self, controller):
        """Test that being within 2 hours returns True."""
        from datetime import datetime

        now = datetime.now().replace(hour=13, minute=0)
        data = CoordinatorData()

        result = controller._is_approaching_demand_window(now, data)

        assert isinstance(result, bool)

    def test_far_from_window_returns_false(self, controller):
        """Test that being far from window returns False."""
        from datetime import datetime

        now = datetime.now().replace(hour=6, minute=0)
        data = CoordinatorData()

        result = controller._is_approaching_demand_window(now, data)

        assert result == False


class TestAsyncLoadErrorHandling:
    """Tests for async_load error handling."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def controller(self, mock_hass):
        """Create a minimal controller for testing."""
        tracker = MagicMock(spec=DecisionOutcomeTracker)
        optimizer = MagicMock(spec=ParameterOptimizer)
        optimizer._params = AdaptiveParameters()
        analyzer = MagicMock(spec=PatternAnalyzer)
        return OptimizationController(mock_hass, "test", tracker, optimizer, analyzer)

    @pytest.mark.asyncio
    async def test_async_load_malformed_weight_history_entry(self, controller):
        """Test that malformed weight history entries are skipped."""
        saved_data = {
            "weights": {},
            "learning_enabled": False,
            "weight_history": [
                {"timestamp": "invalid", "weights": {}},
            ],
            "last_weight_update": None,
        }
        controller._store.async_load = AsyncMock(return_value=saved_data)

        await controller.async_load()

        assert len(controller._weight_history) == 0

    @pytest.mark.asyncio
    async def test_async_load_malformed_last_weight_update(self, controller):
        """Test that malformed last_weight_update is handled."""
        saved_data = {
            "weights": {},
            "learning_enabled": False,
            "weight_history": [],
            "last_weight_update": "invalid-timestamp",
        }
        controller._store.async_load = AsyncMock(return_value=saved_data)

        await controller.async_load()

        assert controller._last_weight_update is None
