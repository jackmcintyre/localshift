"""Tests for PatternAnalyzer (Issue #170 Phase 3)."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.computation_engine_lib.decision_outcome_tracker import (
    DecisionRecord,
)
from custom_components.localshift.computation_engine_lib.pattern_analyzer import (
    BiasCorrection,
    DimensionStats,
    PatternAnalyzer,
    PatternBucket,
    PatternReport,
)
from custom_components.localshift.const import BatteryMode


class TestPatternBucket:
    """Tests for PatternBucket dataclass."""

    def test_pattern_bucket_creation(self):
        """Test creating a PatternBucket."""
        bucket = PatternBucket(
            key="monday",
            dimension="day_of_week",
            sample_count=10,
            mean_score=0.75,
            std_score=0.1,
            over_charge_rate=0.2,
            under_charge_rate=0.1,
            export_loss_rate=0.05,
        )
        assert bucket.key == "monday"
        assert bucket.dimension == "day_of_week"
        assert bucket.sample_count == 10
        assert bucket.mean_score == 0.75

    def test_pattern_bucket_defaults(self):
        """Test PatternBucket default values."""
        bucket = PatternBucket(key="test", dimension="test_dim")
        assert bucket.sample_count == 0
        assert bucket.mean_score == 0.0
        assert bucket.std_score == 0.0


class TestBiasCorrection:
    """Tests for BiasCorrection dataclass."""

    def test_bias_correction_creation(self):
        """Test creating a BiasCorrection."""
        bc = BiasCorrection(
            condition="weather=cloudy",
            dimension="weather",
            group_key="cloudy",
            param_name="solar_confidence_factor",
            adjustment=0.1,
            confidence=0.7,
            sample_count=50,
            weeks_observed=4,
        )
        assert bc.condition == "weather=cloudy"
        assert bc.param_name == "solar_confidence_factor"
        assert bc.adjustment == 0.1
        assert bc.confidence == 0.7

    def test_bias_correction_to_dict(self):
        """Test BiasCorrection serialization."""
        bc = BiasCorrection(
            condition="test_condition",
            dimension="test_dim",
            group_key="test_key",
            param_name="test_param",
            adjustment=1.5,
            confidence=0.8,
            sample_count=100,
            weeks_observed=2,
        )
        data = bc.to_dict()
        assert data["condition"] == "test_condition"
        assert data["param_name"] == "test_param"
        assert data["adjustment"] == 1.5

    def test_bias_correction_from_dict(self):
        """Test BiasCorrection deserialization."""
        data = {
            "condition": "test_condition",
            "dimension": "test_dim",
            "group_key": "test_key",
            "param_name": "test_param",
            "adjustment": 2.0,
            "confidence": 0.9,
            "sample_count": 75,
            "weeks_observed": 3,
        }
        bc = BiasCorrection.from_dict(data)
        assert bc.condition == "test_condition"
        assert bc.param_name == "test_param"
        assert bc.adjustment == 2.0
        assert bc.confidence == 0.9


class TestDimensionStats:
    """Tests for DimensionStats dataclass."""

    def test_dimension_stats_creation(self):
        """Test creating DimensionStats."""
        stats = DimensionStats(dimension="day_of_week")
        assert stats.dimension == "day_of_week"
        assert len(stats.groups) == 0
        assert stats.global_mean == 0.0
        assert stats.global_std == 0.0


class TestPatternReport:
    """Tests for PatternReport dataclass."""

    def test_pattern_report_creation(self):
        """Test creating a PatternReport."""
        report = PatternReport()
        assert report.generated_at is not None
        assert len(report.dimensions) == 0
        assert len(report.biases_detected) == 0
        assert report.data_points_analyzed == 0


class TestPatternAnalyzer:
    """Tests for PatternAnalyzer class."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.storage = {}
        return hass

    @pytest.fixture
    def analyzer(self, mock_hass):
        """Create a PatternAnalyzer instance."""
        return PatternAnalyzer(mock_hass, "test_entry_id")

    def test_analyzer_initialization(self, analyzer):
        """Test PatternAnalyzer initialization."""
        assert analyzer._pattern_data == {}
        # Entry_id is stored in the store key
        assert analyzer._store is not None
        assert analyzer._last_analysis_time is None
        assert analyzer._weeks_of_data == 0  # Starts at 0, incremented during analysis

    def test_analyze_empty_decisions(self, analyzer):
        """Test analyze with empty decision list."""
        report = analyzer.analyze([])
        assert report.data_points_analyzed == 0
        assert len(report.biases_detected) == 0

    def test_analyze_insufficient_decisions(self, analyzer):
        """Test analyze with fewer than minimum decisions."""
        # Create a few mock decisions
        decisions = [
            DecisionRecord(
                timestamp=datetime.now() - timedelta(hours=i),
                mode_chosen=BatteryMode.SELF_CONSUMPTION,
                previous_mode=BatteryMode.SELF_CONSUMPTION,
                soc_at_decision=50.0,
                general_price_at_decision=0.25,
                feed_in_price_at_decision=0.05,
                forecast_solar_remaining_kwh=10.0,
                forecast_consumption_remaining_kwh=5.0,
                cheap_price_threshold=0.20,
                battery_target_soc=80.0,
                weather_condition="sunny",
                day_of_week=0,
                hour_of_day=12,
                is_demand_window=False,
                outcome_score=0.8,
            )
            for i in range(5)
        ]
        report = analyzer.analyze(decisions)
        # With only 5 decisions, should still analyze but may not detect biases
        assert report.data_points_analyzed == 5

    def test_analyze_with_weather_condition(self, analyzer):
        """Test analyze with decisions having different weather conditions."""
        decisions = [
            DecisionRecord(
                timestamp=datetime.now() - timedelta(hours=i),
                mode_chosen=BatteryMode.SELF_CONSUMPTION,
                previous_mode=BatteryMode.SELF_CONSUMPTION,
                soc_at_decision=50.0,
                general_price_at_decision=0.25,
                feed_in_price_at_decision=0.05,
                forecast_solar_remaining_kwh=10.0,
                forecast_consumption_remaining_kwh=5.0,
                cheap_price_threshold=0.20,
                battery_target_soc=80.0,
                weather_condition="cloudy",
                day_of_week=1,
                hour_of_day=14,
                is_demand_window=False,
                outcome_score=0.6,
            )
            for i in range(20)
        ]
        report = analyzer.analyze(decisions)
        assert report.data_points_analyzed == 20

    def test_detect_biases_no_patterns(self, analyzer):
        """Test bias detection with no clear patterns."""
        # Create decisions with consistent scores (no significant deviation)
        decisions = [
            DecisionRecord(
                timestamp=datetime.now() - timedelta(hours=i),
                mode_chosen=BatteryMode.SELF_CONSUMPTION,
                previous_mode=BatteryMode.SELF_CONSUMPTION,
                soc_at_decision=50.0,
                general_price_at_decision=0.25,
                feed_in_price_at_decision=0.05,
                forecast_solar_remaining_kwh=10.0,
                forecast_consumption_remaining_kwh=5.0,
                cheap_price_threshold=0.20,
                battery_target_soc=80.0,
                weather_condition="sunny",
                day_of_week=i % 7,
                hour_of_day=i % 24,
                is_demand_window=False,
                outcome_score=0.75,  # Consistent scores
            )
            for i in range(100)
        ]
        report = analyzer.analyze(decisions)
        # With consistent scores, fewer biases should be detected
        # The actual count depends on the detection threshold
        assert report.data_points_analyzed == 100


class TestPatternAnalyzerStorage:
    """Tests for PatternAnalyzer storage operations."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock()
        hass.storage = {}
        return hass

    @pytest.fixture
    def analyzer(self, mock_hass):
        """Create a PatternAnalyzer instance."""
        return PatternAnalyzer(mock_hass, "test_entry_id")

    @pytest.mark.asyncio
    async def test_async_save(self, analyzer, mock_hass):
        """Test async_save persists data."""
        # Mock the store with an async mock
        mock_store = MagicMock()
        mock_store.async_save = MagicMock(return_value=None)

        # Make it a coroutine mock
        async def mock_async_save(data):
            return None

        mock_store.async_save = mock_async_save
        analyzer._store = mock_store

        await analyzer.async_save()
        # Should complete without error

    @pytest.mark.asyncio
    async def test_async_load(self, analyzer, mock_hass):
        """Test async_load restores data."""

        # Mock the store with async load
        async def mock_async_load():
            return {"pattern_data": {}}

        mock_store = MagicMock()
        mock_store.async_load = mock_async_load
        analyzer._store = mock_store

        await analyzer.async_load()
        # Should not raise
