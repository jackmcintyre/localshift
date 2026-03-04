"""Tests for the ParameterOptimizer (Issue #170 Phase 2)."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.computation_engine_lib.decision_outcome_tracker import (
    DecisionRecord,
)
from custom_components.localshift.computation_engine_lib.parameter_optimizer import (
    ParameterOptimizer,
)
from custom_components.localshift.const import (
    LEARNING_MIN_OBSERVATIONS,
    OPTIMIZABLE_PARAMS,
    BatteryMode,
)
from custom_components.localshift.coordinator_data import AdaptiveParameters


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
                mode_chosen=BatteryMode.GRID_CHARGING,
                previous_mode=BatteryMode.SELF_CONSUMPTION,
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
                mode_chosen=BatteryMode.SELF_CONSUMPTION,
                previous_mode=BatteryMode.SELF_CONSUMPTION,
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
            # Should have loaded params
            assert optimizer._current_params is not None
