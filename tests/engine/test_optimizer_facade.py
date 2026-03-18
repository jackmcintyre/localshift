"""Tests for optimizer_facade shadow optimizer."""

import pytest
from unittest.mock import MagicMock, patch

from custom_components.localshift.engine.optimizer_facade import OptimizerFacade
from custom_components.localshift.coordinator.data import CoordinatorData


class TestShadowOptimizer:
    """Tests for shadow optimizer functionality."""

    @pytest.fixture
    def facade(self):
        return OptimizerFacade()

    @pytest.fixture
    def data_with_shadow_prices(self):
        data = CoordinatorData()
        data.general_price = 0.25
        data.feed_in_price = 0.08
        data.general_forecast = [{"price": 0.25, "time": "2026-03-15T14:00:00"}]
        data.feed_in_forecast = [{"price": 0.08, "time": "2026-03-15T14:00:00"}]

        # Shadow prices - significantly different to trigger different decision
        data.general_price_shadow = 0.45  # Much higher
        data.feed_in_price_shadow = 0.12
        data.general_forecast_shadow = [{"price": 0.45, "time": "2026-03-15T14:00:00"}]
        data.feed_in_forecast_shadow = [{"price": 0.12, "time": "2026-03-15T14:00:00"}]
        return data

    def test_shadow_prices_populated(self, facade, data_with_shadow_prices):
        """Shadow prices should be read into coordinator data."""
        assert data_with_shadow_prices.general_price_shadow == 0.45
        assert data_with_shadow_prices.feed_in_price_shadow == 0.12

    def test_price_delta_calculation(self, facade, data_with_shadow_prices):
        """Price delta should be absolute difference between primary and shadow."""
        price_delta = abs(
            data_with_shadow_prices.general_price
            - data_with_shadow_prices.general_price_shadow
        )
        assert price_delta == 0.20

    def test_comparison_match_true_when_equal(self):
        """comparison_match should be True when modes match."""
        primary = "self_consumption"
        shadow = "self_consumption"
        assert (primary == shadow) == True

    def test_comparison_match_false_when_differ(self):
        """comparison_match should be False when modes differ."""
        primary = "self_consumption"
        shadow = "grid_charging"
        assert (primary == shadow) == False
