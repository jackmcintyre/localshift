"""Tests for the ParameterOptimizer (Issue #170 Phase 2)."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.engine.outcomes import (
    DecisionRecord,
)
from custom_components.localshift.engine.parameters import (
    ParameterOptimizer,
)
from custom_components.localshift.engine.optimizer_dp import PlannerAction
from custom_components.localshift.engine.pattern_types import BiasCorrection
from custom_components.localshift.const import (
    LEARNING_MIN_OBSERVATIONS,
    OPTIMIZABLE_PARAMS,
    BatteryMode,
)
from custom_components.localshift.coordinator import AdaptiveParameters


def _make_decisions(count: int = 60) -> list[DecisionRecord]:
    return [
        DecisionRecord(
            timestamp=datetime.now() - timedelta(hours=i),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.10,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=10.0,
            forecast_consumption_remaining_kwh=8.0,
            cheap_price_threshold=0.12,
            battery_target_soc=80.0,
            weather_condition="sunny",
            day_of_week=0,
            hour_of_day=12,
            is_demand_window=False,
            outcome_score=0.7,
        )
        for i in range(count)
    ]


class TestParameterOptimizer:
    """Tests for ParameterOptimizer."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def optimizer(self, mock_hass):
        """Create a ParameterOptimizer instance."""
        return ParameterOptimizer(mock_hass, "test_entry_id")

    def test_initialization(self, optimizer):
        """Test optimizer initializes correctly."""
        # Uses constants from const.py
        assert LEARNING_MIN_OBSERVATIONS == 50
        assert optimizer._current_params is not None
        assert isinstance(optimizer._current_params, AdaptiveParameters)

    def test_should_update_insufficient_decisions(self, optimizer):
        """Test that update is blocked with insufficient decisions."""
        # Under minimum observations
        assert optimizer.should_update(10) is False
        assert optimizer.should_update(49) is False

    def test_should_update_sufficient_decisions(self, optimizer):
        """Test that update proceeds with sufficient decisions."""
        # Set last update to more than 24 hours ago
        optimizer._last_update = datetime.now() - timedelta(hours=25)
        assert optimizer.should_update(50) is True
        assert optimizer.should_update(100) is True

    def test_should_update_too_recent(self, optimizer):
        """Test that update is blocked if last update was recent."""
        optimizer._last_update = datetime.now() - timedelta(hours=12)
        assert optimizer.should_update(100) is False

    def test_optimize_returns_adaptive_params(self, optimizer):
        """Test that optimize returns AdaptiveParameters."""
        # Create mock decisions
        decisions = [
            DecisionRecord(
                timestamp=datetime.now() - timedelta(hours=i),
                mode_chosen=PlannerAction.HOLD,
                previous_mode=PlannerAction.HOLD,
                soc_at_decision=30.0 + i,
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

        # optimize requires current_7d_score
        result = optimizer.optimize(decisions, current_7d_score=0.75)
        assert isinstance(result, AdaptiveParameters)

    def test_bounds_clamping(self, optimizer):
        """Test that parameters stay within defined bounds."""
        decisions = [
            DecisionRecord(
                timestamp=datetime.now() - timedelta(hours=i),
                mode_chosen=PlannerAction.HOLD,
                previous_mode=PlannerAction.HOLD,
                soc_at_decision=50.0,
                general_price_at_decision=0.10,
                feed_in_price_at_decision=0.05,
                forecast_solar_remaining_kwh=10.0,
                forecast_consumption_remaining_kwh=8.0,
                cheap_price_threshold=0.12,
                battery_target_soc=80.0,
                weather_condition="sunny",
                day_of_week=0,
                hour_of_day=12,
                is_demand_window=False,
                outcome_score=0.8,
            )
            for i in range(60)
        ]

        result = optimizer.optimize(decisions, current_7d_score=0.75)

        # Check all params are within bounds
        for param_name, value in result.values.items():
            if param_name in OPTIMIZABLE_PARAMS:
                param_def = OPTIMIZABLE_PARAMS[param_name]
                assert param_def.min_val <= value <= param_def.max_val, (
                    f"{param_name}={value} outside [{param_def.min_val}, {param_def.max_val}]"
                )


class TestParameterOptimizerPersistence:
    """Tests for ParameterOptimizer persistence."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def optimizer(self, mock_hass):
        """Create a ParameterOptimizer instance."""
        return ParameterOptimizer(mock_hass, "test_entry_id")

    @pytest.mark.asyncio
    async def test_async_save(self, optimizer, mock_hass):
        """Test saving optimizer state."""
        # Set some state
        optimizer._current_params.values["cheap_price_bias"] = 0.5
        optimizer._current_params.confidence["cheap_price_bias"] = 0.8

        await optimizer.async_save()
        # Should not raise

    @pytest.mark.asyncio
    async def test_async_load_with_data(self, optimizer, mock_hass):
        """Test loading with saved state."""
        # Mock the store to return saved data
        saved_data = {
            "params": {"cheap_price_bias": 0.3},
            "confidence": {"cheap_price_bias": 0.7},
        }

        with patch.object(
            optimizer._store, "async_load", new_callable=AsyncMock
        ) as mock_load:
            mock_load.return_value = saved_data
            await optimizer.async_load()
            assert optimizer._current_params is not None


class TestWeatherWeightedRollback:
    @pytest.fixture
    def mock_hass(self):
        hass = MagicMock()
        hass.loop = MagicMock()
        hass.loop.run_in_executor = AsyncMock(return_value=None)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        return hass

    @pytest.fixture
    def optimizer(self, mock_hass):
        return ParameterOptimizer(mock_hass, "test_entry_id")

    @pytest.fixture
    def decisions(self):
        return _make_decisions(60)

    def test_weather_weight_parameter_accepted(self, optimizer, decisions):
        optimizer.optimize(decisions, current_7d_score=0.75, weather_weight=1.0)
        optimizer.optimize(decisions, current_7d_score=0.75, weather_weight=0.3)

    def test_default_weather_weight_is_1(self, optimizer, decisions):
        optimizer.optimize(decisions, current_7d_score=0.75)
        diag = optimizer.get_diagnostics()
        assert diag["last_weather_anomaly_weight"] == 1.0

    def test_weather_anomaly_weight_in_diagnostics(self, optimizer, decisions):
        optimizer.optimize(decisions, current_7d_score=0.75, weather_weight=0.3)
        diag = optimizer.get_diagnostics()
        assert "last_weather_anomaly_weight" in diag
        assert diag["last_weather_anomaly_weight"] == 0.3

    def test_normal_days_rollback_triggers_at_3_consecutive(self, optimizer, decisions):
        optimizer.optimize(decisions, current_7d_score=0.75, weather_weight=1.0)
        optimizer.optimize(decisions, current_7d_score=0.60, weather_weight=1.0)
        optimizer.optimize(decisions, current_7d_score=0.50, weather_weight=1.0)
        optimizer.optimize(decisions, current_7d_score=0.40, weather_weight=1.0)
        optimizer.optimize(decisions, current_7d_score=0.30, weather_weight=1.0)
        assert optimizer._consecutive_degrading_days == 0

    def test_heatwave_scenario_does_not_trigger_rollback(self, optimizer, decisions):
        optimizer.optimize(decisions, current_7d_score=0.75, weather_weight=1.0)
        optimizer.optimize(decisions, current_7d_score=0.55, weather_weight=0.3)
        optimizer.optimize(decisions, current_7d_score=0.50, weather_weight=0.3)
        optimizer.optimize(decisions, current_7d_score=0.48, weather_weight=0.3)
        assert optimizer._consecutive_degrading_days == 0

    def test_heatwave_resets_consecutive_on_day4(self, optimizer, decisions):
        optimizer.optimize(decisions, current_7d_score=0.75, weather_weight=1.0)
        optimizer.optimize(decisions, current_7d_score=0.55, weather_weight=0.3)
        optimizer.optimize(decisions, current_7d_score=0.50, weather_weight=0.3)
        assert optimizer._consecutive_degrading_days == 2
        optimizer.optimize(decisions, current_7d_score=0.48, weather_weight=0.3)
        assert optimizer._consecutive_degrading_days == 0

    def test_last_weighted_7d_score_attribute_exists(self, optimizer):
        assert hasattr(optimizer, "_last_weighted_7d_score")
        assert optimizer._last_weighted_7d_score == 0.0

    @pytest.mark.asyncio
    async def test_weighted_score_persisted_in_save(self, optimizer, mock_hass):
        optimizer._last_weighted_7d_score = 0.65
        saved_data: dict | None = None

        async def capture_save(data):
            nonlocal saved_data
            saved_data = data

        optimizer._store.async_save = capture_save
        await optimizer.async_save()
        assert saved_data is not None
        assert "last_weighted_7d_score" in saved_data
        assert saved_data["last_weighted_7d_score"] == pytest.approx(0.65)

    @pytest.mark.asyncio
    async def test_weighted_score_loaded_from_persistence(self, optimizer):
        stored = {
            "consecutive_degrading_days": 0,
            "last_7d_score": 0.70,
            "last_weighted_7d_score": 0.68,
            "stable_checkpoint": {},
            "recently_rolled_back_params": [],
        }
        with patch.object(
            optimizer._store, "async_load", new_callable=AsyncMock, return_value=stored
        ):
            await optimizer.async_load()
        assert optimizer._last_weighted_7d_score == pytest.approx(0.68)

    @pytest.mark.asyncio
    async def test_weighted_score_fallback_to_last_7d_score(self, optimizer):
        stored = {
            "consecutive_degrading_days": 0,
            "last_7d_score": 0.72,
            "stable_checkpoint": {},
            "recently_rolled_back_params": [],
        }
        with patch.object(
            optimizer._store, "async_load", new_callable=AsyncMock, return_value=stored
        ):
            await optimizer.async_load()
        assert optimizer._last_weighted_7d_score == pytest.approx(0.72)


class TestParameterOptimizerBiasCorrections:
    """Tests for bias correction functionality."""

    @pytest.fixture
    def mock_hass(self):
        hass = MagicMock()
        hass.data = {}
        hass.config_entries = MagicMock()
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {}
        hass.config_entries.async_get_entry.return_value = entry
        store = AsyncMock()
        store.async_load = AsyncMock(return_value=None)
        store.async_save = AsyncMock()
        with patch(
            "custom_components.localshift.engine.parameters.Store", return_value=store
        ):
            yield hass

    @pytest.fixture
    def optimizer(self, mock_hass):
        return ParameterOptimizer(mock_hass, "test_entry_id")

    @pytest.fixture
    def decisions(self):
        return _make_decisions(60)

    def test_set_bias_corrections_stores_list(self, optimizer):
        corrections = [
            BiasCorrection(
                condition="high_forecast_error",
                dimension="hour",
                group_key="14",
                param_name="cheap_price_bias",
                adjustment=0.1,
                confidence=0.9,
                sample_count=50,
                weeks_observed=8,
            )
        ]
        optimizer.set_bias_corrections(corrections)
        assert optimizer._pending_bias_corrections == corrections

    def test_optimize_with_high_confidence_bias_correction(self, optimizer, decisions):
        corrections = [
            BiasCorrection(
                condition="high_forecast_error",
                dimension="hour",
                group_key="14",
                param_name="cheap_price_bias",
                adjustment=0.1,
                confidence=0.9,
                sample_count=50,
                weeks_observed=8,
            )
        ]
        optimizer.optimize(
            decisions, current_7d_score=0.75, bias_corrections=corrections
        )
        diag = optimizer.get_diagnostics()
        assert "adjustment_count" in diag

    def test_optimize_with_medium_confidence_bias_correction(
        self, optimizer, decisions
    ):
        corrections = [
            BiasCorrection(
                condition="medium_forecast_error",
                dimension="hour",
                group_key="14",
                param_name="cheap_price_bias",
                adjustment=0.05,
                confidence=0.6,
                sample_count=30,
                weeks_observed=4,
            )
        ]
        optimizer.optimize(
            decisions, current_7d_score=0.75, bias_corrections=corrections
        )
        diag = optimizer.get_diagnostics()
        assert "adjustment_count" in diag

    def test_optimize_with_unknown_param_correction(self, optimizer, decisions):
        corrections = [
            BiasCorrection(
                condition="some_condition",
                dimension="hour",
                group_key="14",
                param_name="nonexistent_param_xyz",
                adjustment=0.1,
                confidence=0.9,
                sample_count=50,
                weeks_observed=8,
            )
        ]
        optimizer.optimize(
            decisions, current_7d_score=0.75, bias_corrections=corrections
        )

    def test_optimize_with_empty_decisions(self, optimizer):
        optimizer.optimize([], current_7d_score=0.75)
        diag = optimizer.get_diagnostics()
        assert diag is not None


class TestParameterOptimizerRollback:
    """Tests for rollback functionality."""

    @pytest.fixture
    def mock_hass(self):
        hass = MagicMock()
        hass.data = {}
        hass.config_entries = MagicMock()
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {}
        hass.config_entries.async_get_entry.return_value = entry
        store = AsyncMock()
        store.async_load = AsyncMock(return_value=None)
        store.async_save = AsyncMock()
        with patch(
            "custom_components.localshift.engine.parameters.Store", return_value=store
        ):
            yield hass

    @pytest.fixture
    def optimizer(self, mock_hass):
        return ParameterOptimizer(mock_hass, "test_entry_id")

    @pytest.fixture
    def decisions(self):
        return _make_decisions(60)

    def test_rollback_with_stable_checkpoint(self, optimizer, decisions):
        optimizer._stable_checkpoint = {"cheap_price_bias": 0.5}
        optimizer._current_params.values["cheap_price_bias"] = 2.0
        optimizer._consecutive_degrading_days = 3
        optimizer._rollback_parameters()
        assert optimizer._current_params.values["cheap_price_bias"] == 0.5

    def test_rollback_resets_consecutive_days(self, optimizer, decisions):
        optimizer.optimize(decisions, current_7d_score=0.75, weather_weight=1.0)
        optimizer.optimize(decisions, current_7d_score=0.60, weather_weight=1.0)
        optimizer.optimize(decisions, current_7d_score=0.50, weather_weight=1.0)
        optimizer.optimize(decisions, current_7d_score=0.40, weather_weight=1.0)
        optimizer.optimize(decisions, current_7d_score=0.30, weather_weight=1.0)
        assert optimizer._consecutive_degrading_days == 0


class TestParameterOptimizerPersistenceEdgeCases:
    @pytest.fixture
    def mock_hass(self):
        hass = MagicMock()
        hass.data = {}
        hass.config_entries = MagicMock()
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {}
        hass.config_entries.async_get_entry.return_value = entry
        store = AsyncMock()
        store.async_load = AsyncMock(return_value=None)
        store.async_save = AsyncMock()
        with patch(
            "custom_components.localshift.engine.parameters.Store", return_value=store
        ):
            yield hass

    @pytest.fixture
    def optimizer(self, mock_hass):
        return ParameterOptimizer(mock_hass, "test_entry_id")

    @pytest.mark.asyncio
    async def test_async_load_with_no_data(self, optimizer):
        with patch.object(
            optimizer._store, "async_load", new_callable=AsyncMock, return_value=None
        ):
            await optimizer.async_load()
        # Should start fresh without errors
        assert optimizer._consecutive_degrading_days == 0

    @pytest.mark.asyncio
    async def test_async_load_with_invalid_date(self, optimizer):
        stored = {
            "consecutive_degrading_days": 0,
            "last_7d_score": 0.72,
            "last_update": "invalid-date-format",
            "stable_checkpoint": {},
            "recently_rolled_back_params": [],
        }
        with patch.object(
            optimizer._store, "async_load", new_callable=AsyncMock, return_value=stored
        ):
            await optimizer.async_load()
        # Should handle invalid date gracefully
        assert optimizer._last_update is None


class TestParameterOptimizerEdgeCases:
    """Tests for edge cases in gamma variate and other methods."""

    @pytest.fixture
    def mock_hass(self):
        hass = MagicMock()
        hass.data = {}
        hass.config_entries = MagicMock()
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.data = {}
        hass.config_entries.async_get_entry.return_value = entry
        store = AsyncMock()
        store.async_load = AsyncMock(return_value=None)
        store.async_save = AsyncMock()
        with patch(
            "custom_components.localshift.engine.parameters.Store", return_value=store
        ):
            yield hass

    @pytest.fixture
    def optimizer(self, mock_hass):
        return ParameterOptimizer(mock_hass, "test_entry_id")

    def test_gamma_variate_with_alpha_less_than_one(self, optimizer):
        import random

        random.seed(42)
        result = optimizer._gamma_variate(0.5, 1.0)
        assert result > 0
        assert isinstance(result, float)

    def test_should_update_when_last_update_is_none(self, optimizer):
        optimizer._last_update = None
        assert optimizer.should_update(60) is True

    def test_apply_high_confidence_correction_clamped_to_same_value(self, optimizer):
        from custom_components.localshift.const import OPTIMIZABLE_PARAMS

        values = {"cheap_price_bias": 5.0}
        confidence = {}
        param_def = OPTIMIZABLE_PARAMS["cheap_price_bias"]
        corrections = [
            BiasCorrection(
                condition="test",
                dimension="hour",
                group_key="14",
                param_name="cheap_price_bias",
                adjustment=10.0,
                confidence=0.9,
                sample_count=50,
                weeks_observed=8,
            )
        ]
        result = optimizer._apply_high_confidence_correction(
            "cheap_price_bias", param_def, 5.0, corrections, values, confidence
        )
        assert result is False

    def test_apply_medium_confidence_correction_too_small(self, optimizer):
        from custom_components.localshift.const import OPTIMIZABLE_PARAMS

        values = {"cheap_price_bias": 0.0}
        param_def = OPTIMIZABLE_PARAMS["cheap_price_bias"]
        corrections = [
            BiasCorrection(
                condition="test",
                dimension="hour",
                group_key="14",
                param_name="cheap_price_bias",
                adjustment=0.001,
                confidence=0.6,
                sample_count=30,
                weeks_observed=4,
            )
        ]
        result = optimizer._apply_medium_confidence_correction(
            "cheap_price_bias", param_def, 0.0, corrections, values
        )
        assert result is False

    def test_compute_bin_confidence_empty_list(self, optimizer):
        result = optimizer._compute_bin_confidence([])
        assert result == 0.0

    def test_apply_step_limit_exceeds_positive(self, optimizer):
        result = optimizer._apply_step_limit(5.0, 0.0, 1.0)
        assert result == 1.0

    def test_apply_step_limit_exceeds_negative(self, optimizer):
        result = optimizer._apply_step_limit(0.0, 5.0, 1.0)
        assert result == 4.0
