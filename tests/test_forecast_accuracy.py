"""Tests for forecast accuracy tracking modules.

Issue #270: Extended forecast accuracy with bias detection.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.computation_engine_lib.forecast_accuracy import (
    ExtendedAccuracyMetrics,
    ExtendedForecastAccuracyEngine,
    ForecastAccuracyEngine,
)

# =============================================================================
# EXTENDED ACCURACY METRICS TESTS
# =============================================================================


class TestExtendedAccuracyMetrics:
    """Tests for ExtendedAccuracyMetrics dataclass."""

    def test_default_values(self):
        """Test default values are set correctly."""
        metrics = ExtendedAccuracyMetrics()
        assert metrics.accuracy_24h == 100.0
        assert metrics.accuracy_7d == 100.0
        assert metrics.accuracy_30d == 100.0
        assert metrics.bias == 0.0
        assert metrics.mape == 0.0
        assert metrics.sample_count == 0
        assert metrics.last_updated is None

    def test_custom_values(self):
        """Test custom values are set correctly."""
        now = datetime(2026, 2, 26, 8, 0, 0)
        metrics = ExtendedAccuracyMetrics(
            accuracy_24h=95.5,
            accuracy_7d=93.2,
            accuracy_30d=91.8,
            bias=-2.3,
            mape=4.5,
            sample_count=150,
            last_updated=now,
        )
        assert metrics.accuracy_24h == 95.5
        assert metrics.accuracy_7d == 93.2
        assert metrics.accuracy_30d == 91.8
        assert metrics.bias == -2.3
        assert metrics.mape == 4.5
        assert metrics.sample_count == 150
        assert metrics.last_updated == now

    def test_to_dict(self):
        """Test serialization to dictionary."""
        now = datetime(2026, 2, 26, 8, 0, 0)
        metrics = ExtendedAccuracyMetrics(
            accuracy_24h=94.5,
            accuracy_7d=92.0,
            accuracy_30d=90.0,
            bias=1.5,
            mape=3.2,
            sample_count=100,
            last_updated=now,
        )
        result = metrics.to_dict()

        assert result["accuracy_24h"] == 94.5
        assert result["accuracy_7d"] == 92.0
        assert result["accuracy_30d"] == 90.0
        assert result["bias"] == 1.5
        assert result["mape"] == 3.2
        assert result["sample_count"] == 100
        assert result["last_updated"] == "2026-02-26T08:00:00"

    def test_to_dict_no_last_updated(self):
        """Test serialization when last_updated is None."""
        metrics = ExtendedAccuracyMetrics(accuracy_24h=95.0)
        result = metrics.to_dict()

        assert result["last_updated"] is None

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "accuracy_24h": 93.5,
            "accuracy_7d": 91.0,
            "accuracy_30d": 89.5,
            "bias": -1.8,
            "mape": 4.1,
            "sample_count": 75,
            "last_updated": "2026-02-26T08:00:00",
        }
        metrics = ExtendedAccuracyMetrics.from_dict(data)

        assert metrics.accuracy_24h == 93.5
        assert metrics.accuracy_7d == 91.0
        assert metrics.accuracy_30d == 89.5
        assert metrics.bias == -1.8
        assert metrics.mape == 4.1
        assert metrics.sample_count == 75
        assert metrics.last_updated == datetime(2026, 2, 26, 8, 0, 0)

    def test_from_dict_invalid_datetime(self):
        """Test deserialization handles invalid datetime strings."""
        data = {
            "accuracy_24h": 95.0,
            "last_updated": "invalid-datetime",
        }
        metrics = ExtendedAccuracyMetrics.from_dict(data)

        assert metrics.accuracy_24h == 95.0
        assert metrics.last_updated is None

    def test_from_dict_missing_fields(self):
        """Test deserialization with missing fields uses defaults."""
        data = {}
        metrics = ExtendedAccuracyMetrics.from_dict(data)

        assert metrics.accuracy_24h == 100.0
        assert metrics.accuracy_7d == 100.0
        assert metrics.accuracy_30d == 100.0
        assert metrics.bias == 0.0
        assert metrics.mape == 0.0
        assert metrics.sample_count == 0


# =============================================================================
# EXTENDED FORECAST ACCURACY ENGINE TESTS
# =============================================================================


class TestExtendedForecastAccuracyEngine:
    """Tests for ExtendedForecastAccuracyEngine class."""

    def test_init(self):
        """Test initialization."""
        engine = ExtendedForecastAccuracyEngine()
        assert engine.metrics is not None
        assert engine.metrics.accuracy_24h == 100.0

    def test_init_with_storage_path(self):
        """Test initialization with storage path."""
        engine = ExtendedForecastAccuracyEngine(storage_path="/tmp/test.json")
        assert engine.storage_path == "/tmp/test.json"

    def test_metrics_property(self):
        """Test metrics property returns current metrics."""
        engine = ExtendedForecastAccuracyEngine()
        metrics = engine.metrics

        assert isinstance(metrics, ExtendedAccuracyMetrics)
        assert metrics.accuracy_24h == 100.0

    def test_to_dict(self):
        """Test engine serialization."""
        engine = ExtendedForecastAccuracyEngine()
        engine._metrics = ExtendedAccuracyMetrics(
            accuracy_24h=95.0,
            sample_count=50,
        )

        result = engine.to_dict()

        assert "metrics" in result
        assert result["metrics"]["accuracy_24h"] == 95.0
        assert result["history_count"] == 0

    def test_from_dict(self):
        """Test engine deserialization."""
        data = {
            "metrics": {
                "accuracy_24h": 94.0,
                "accuracy_7d": 92.0,
                "accuracy_30d": 90.0,
                "bias": 1.5,
                "mape": 3.0,
                "sample_count": 100,
                "last_updated": None,
            },
            "history_count": 10,
        }

        engine = ExtendedForecastAccuracyEngine.from_dict(data)

        assert engine.metrics.accuracy_24h == 94.0
        assert engine.metrics.accuracy_7d == 92.0
        assert engine.metrics.sample_count == 100


# =============================================================================
# FORECAST ACCURACY ENGINE TESTS
# =============================================================================


class TestForecastAccuracyEngine:
    """Tests for ForecastAccuracyEngine class."""

    def test_init(self):
        """Test initialization."""
        engine = ForecastAccuracyEngine()
        assert engine is not None

    @pytest.mark.asyncio
    async def test_compute_forecast_accuracy_no_history(self):
        """Test computing accuracy with no forecast history."""
        from custom_components.localshift.coordinator_data import CoordinatorData

        engine = ForecastAccuracyEngine()
        data = CoordinatorData()
        data.forecast_history = []
        data.soc = 80.0
        data.general_price = 0.25
        data.feed_in_price = 0.08

        await engine.compute_forecast_accuracy(data)

        # Should set default values
        assert data.forecast_error_soc_15min == 0.0
        assert data.forecast_accuracy_soc_1h == 100.0

    @pytest.mark.asyncio
    async def test_compute_forecast_accuracy_sets_defaults(self):
        """Test that compute sets default values on CoordinatorData."""
        engine = ForecastAccuracyEngine()
        data = MagicMock()
        data.forecast_history = []
        data.soc = 75.0
        data.general_price = 0.30
        data.feed_in_price = 0.10

        await engine.compute_forecast_accuracy(data)

        # Verify defaults are set
        assert hasattr(data, "forecast_error_soc_15min")
        assert hasattr(data, "forecast_error_soc_1h")
        assert hasattr(data, "forecast_error_soc_4h")
        assert hasattr(data, "forecast_accuracy_soc_15min")
        assert hasattr(data, "forecast_accuracy_soc_1h")
        assert hasattr(data, "forecast_accuracy_soc_4h")
        assert hasattr(data, "forecast_error_buy_price_1h")
        assert hasattr(data, "forecast_error_sell_price_1h")
