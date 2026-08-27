"""Tests for PatternAnalyzer (Issue #170 Phase 3)."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.engine.outcomes import (
    DecisionRecord,
)
from custom_components.localshift.engine.pattern_analyzer import (
    MIN_SAMPLES_FOR_BIAS,
    PatternAnalyzer,
)
from custom_components.localshift.engine.pattern_types import (
    BiasCorrection,
    DimensionStats,
    PatternBucket,
    PatternReport,
)
from custom_components.localshift.const import BatteryMode
from custom_components.localshift.engine.optimizer_dp import PlannerAction


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
                mode_chosen=PlannerAction.HOLD,
                previous_mode=PlannerAction.HOLD,
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
                mode_chosen=PlannerAction.HOLD,
                previous_mode=PlannerAction.HOLD,
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
                mode_chosen=PlannerAction.HOLD,
                previous_mode=PlannerAction.HOLD,
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

        async def mock_async_load():
            return {"pattern_data": {}}

        mock_store = MagicMock()
        mock_store.async_load = mock_async_load
        analyzer._store = mock_store

        await analyzer.async_load()

    @pytest.mark.asyncio
    async def test_async_load_none_data(self, analyzer):
        """Test async_load with None data."""

        async def mock_async_load():
            return None

        mock_store = MagicMock()
        mock_store.async_load = mock_async_load
        analyzer._store = mock_store

        await analyzer.async_load()
        assert analyzer._last_report is None

    @pytest.mark.asyncio
    async def test_async_load_with_report(self, analyzer):
        """Test async_load with saved report data."""

        async def mock_async_load():
            return {
                "last_report": {
                    "generated_at": "2024-01-15T12:00:00",
                    "data_points_analyzed": 100,
                    "dimensions": {},
                    "biases_detected": [],
                },
                "weeks_of_data": 2,
                "last_analysis_time": "2024-01-15T12:00:00",
            }

        mock_store = MagicMock()
        mock_store.async_load = mock_async_load
        analyzer._store = mock_store

        await analyzer.async_load()
        assert analyzer._weeks_of_data == 2

    @pytest.mark.asyncio
    async def test_async_load_invalid_timestamp(self, analyzer):
        """Test async_load handles invalid timestamp gracefully."""

        async def mock_async_load():
            return {
                "last_analysis_time": "invalid-timestamp",
            }

        mock_store = MagicMock()
        mock_store.async_load = mock_async_load
        analyzer._store = mock_store

        await analyzer.async_load()
        assert analyzer._last_analysis_time is None

    @pytest.mark.asyncio
    async def test_async_load_with_valid_timestamp(self, analyzer):
        """Test async_load with valid timestamp."""

        async def mock_async_load():
            return {
                "last_analysis_time": "2024-01-15T12:00:00",
            }

        mock_store = MagicMock()
        mock_store.async_load = mock_async_load
        analyzer._store = mock_store

        await analyzer.async_load()
        assert analyzer._last_analysis_time is not None

    @pytest.mark.asyncio
    async def test_last_analysis_time_property_reflects_persisted_value(
        self, analyzer
    ):
        """The read-only property exposes the persisted _last_analysis_time."""
        assert analyzer.last_analysis_time is None

        async def mock_async_load():
            return {"last_analysis_time": "2026-06-03T09:30:00+00:00"}

        mock_store = MagicMock()
        mock_store.async_load = mock_async_load
        analyzer._store = mock_store

        await analyzer.async_load()

        assert analyzer.last_analysis_time is analyzer._last_analysis_time
        assert analyzer.last_analysis_time is not None
        assert analyzer.last_analysis_time.isoformat() == "2026-06-03T09:30:00+00:00"


class TestPatternAnalyzerComputeBucketStats:
    """Tests for _compute_bucket_stats method."""

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

    def test_compute_bucket_stats_empty_decisions(self, analyzer):
        """Test _compute_bucket_stats with no decisions."""
        bucket = analyzer._compute_bucket_stats("test_key", "test_dim", [])
        assert bucket.key == "test_key"
        assert bucket.dimension == "test_dim"
        assert bucket.sample_count == 0
        assert bucket.mean_score == 0.0

    def test_compute_bucket_stats_single_decision(self, analyzer):
        """Test _compute_bucket_stats with one decision."""
        decision = DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.HOLD,
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
        bucket = analyzer._compute_bucket_stats("monday", "day_of_week", [decision])
        assert bucket.sample_count == 1
        assert bucket.mean_score == 0.8
        assert bucket.std_score == 0.0

    def test_compute_bucket_stats_grid_charge_with_export(self, analyzer):
        """Test over_charge_rate with grid charge and export."""
        decisions = [
            DecisionRecord(
                timestamp=datetime.now() - timedelta(hours=i),
                mode_chosen=PlannerAction.CHARGE_GRID_NORMAL,
                previous_mode=PlannerAction.HOLD,
                soc_at_decision=50.0,
                general_price_at_decision=0.10,
                feed_in_price_at_decision=0.05,
                forecast_solar_remaining_kwh=10.0,
                forecast_consumption_remaining_kwh=5.0,
                cheap_price_threshold=0.15,
                battery_target_soc=90.0,
                weather_condition="cloudy",
                day_of_week=0,
                hour_of_day=12,
                is_demand_window=False,
                outcome_score=0.5,
                actual_export_kwh=1.0,
                actual_import_kwh=0.5,
            )
            for i in range(15)
        ]
        bucket = analyzer._compute_bucket_stats("cloudy", "weather", decisions)
        assert bucket.over_charge_rate > 0

    def test_compute_bucket_stats_under_charge(self, analyzer):
        """Test under_charge_rate calculation."""
        decisions = [
            DecisionRecord(
                timestamp=datetime.now() - timedelta(hours=i),
                mode_chosen=PlannerAction.HOLD,
                previous_mode=PlannerAction.HOLD,
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
                outcome_score=0.6,
                actual_soc_change=-15.0,
            )
            for i in range(20)
        ]
        bucket = analyzer._compute_bucket_stats("sunny", "weather", decisions)
        assert bucket.under_charge_rate > 0


class TestPatternAnalyzerDetectBiases:
    """Tests for bias detection."""

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

    def test_detect_biases_with_significant_deviation(self, analyzer):
        """Test bias detection with significant score deviation spanning 2+ weeks."""
        base_time = datetime.now()
        decisions = []
        for i in range(50):
            decisions.append(
                DecisionRecord(
                    timestamp=base_time - timedelta(days=i),
                    mode_chosen=PlannerAction.HOLD,
                    previous_mode=PlannerAction.HOLD,
                    soc_at_decision=50.0,
                    general_price_at_decision=0.25,
                    feed_in_price_at_decision=0.05,
                    forecast_solar_remaining_kwh=10.0,
                    forecast_consumption_remaining_kwh=5.0,
                    cheap_price_threshold=0.20,
                    battery_target_soc=80.0,
                    weather_condition="cloudy",
                    day_of_week=0,
                    hour_of_day=12,
                    is_demand_window=False,
                    outcome_score=0.4,
                )
            )
        for i in range(50):
            decisions.append(
                DecisionRecord(
                    timestamp=base_time - timedelta(days=i + 20),
                    mode_chosen=PlannerAction.HOLD,
                    previous_mode=PlannerAction.HOLD,
                    soc_at_decision=50.0,
                    general_price_at_decision=0.25,
                    feed_in_price_at_decision=0.05,
                    forecast_solar_remaining_kwh=10.0,
                    forecast_consumption_remaining_kwh=5.0,
                    cheap_price_threshold=0.20,
                    battery_target_soc=80.0,
                    weather_condition="sunny",
                    day_of_week=1,
                    hour_of_day=14,
                    is_demand_window=False,
                    outcome_score=0.85,
                )
            )
        report = analyzer.analyze(decisions)
        biases = analyzer.detect_biases(report, decisions)
        assert isinstance(biases, list)

    def test_detect_biases_empty_report(self, analyzer):
        """Test detect_biases with empty report."""
        report = PatternReport()
        biases = analyzer.detect_biases(report, [])
        assert biases == []


class TestPatternAnalyzerAdjustments:
    """Tests for adjustment check methods."""

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

    def test_check_over_charge_adjustment_cloudy(self, analyzer):
        """Test over-charge adjustment for cloudy weather."""
        bucket = PatternBucket(
            key="cloudy",
            dimension="weather_condition",
            sample_count=20,
            mean_score=0.5,
            over_charge_rate=0.5,
        )
        stats = DimensionStats(
            dimension="weather_condition",
            global_mean=0.7,
            global_std=0.1,
        )
        result = analyzer._check_over_charge_adjustment(
            "weather_condition", "cloudy", bucket, stats, 0.8
        )
        assert result is not None
        assert (
            "cloud_confidence_factor" in result.param_name
            or "solar" in result.param_name
        )

    def test_check_over_charge_adjustment_low_solar(self, analyzer):
        """Test over-charge adjustment for low solar."""
        bucket = PatternBucket(
            key="low",
            dimension="solar_availability",
            sample_count=20,
            mean_score=0.5,
            over_charge_rate=0.5,
        )
        stats = DimensionStats(
            dimension="solar_availability",
            global_mean=0.7,
            global_std=0.1,
        )
        result = analyzer._check_over_charge_adjustment(
            "solar_availability", "low", bucket, stats, 0.8
        )
        assert result is not None

    def test_check_export_loss_adjustment(self, analyzer):
        """Test export loss adjustment."""
        bucket = PatternBucket(
            key="low",
            dimension="price_regime",
            sample_count=15,
            mean_score=0.5,
            export_loss_rate=0.4,
        )
        stats = DimensionStats(
            dimension="price_regime",
            global_mean=0.7,
            global_std=0.1,
        )
        result = analyzer._check_export_loss_adjustment(
            "price_regime", "low", bucket, stats, 0.7
        )
        assert result is not None

    def test_check_under_charge_adjustment_sunny(self, analyzer):
        """Test under-charge adjustment for sunny weather."""
        bucket = PatternBucket(
            key="sunny",
            dimension="weather_condition",
            sample_count=20,
            mean_score=0.5,
            under_charge_rate=0.4,
        )
        stats = DimensionStats(
            dimension="weather_condition",
            global_mean=0.7,
            global_std=0.1,
        )
        result = analyzer._check_under_charge_adjustment(
            "weather_condition", "sunny", bucket, stats, 0.8
        )
        assert result is not None

    def test_check_generic_low_score_hour_of_day(self, analyzer):
        """Test generic low score adjustment for hour_of_day."""
        bucket = PatternBucket(
            key="8",
            dimension="hour_of_day",
            sample_count=20,
            mean_score=0.3,
        )
        stats = DimensionStats(
            dimension="hour_of_day",
            global_mean=0.7,
            global_std=0.1,
        )
        result = analyzer._check_generic_low_score_adjustment(
            "hour_of_day", "8", bucket, stats, 0.8
        )
        assert result is not None

    def test_check_generic_low_score_winter(self, analyzer):
        """Test generic low score adjustment for winter."""
        bucket = PatternBucket(
            key="winter",
            dimension="season",
            sample_count=30,
            mean_score=0.3,
        )
        stats = DimensionStats(
            dimension="season",
            global_mean=0.7,
            global_std=0.1,
        )
        result = analyzer._check_generic_low_score_adjustment(
            "season", "winter", bucket, stats, 0.8
        )
        assert result is not None


class TestPatternAnalyzerKeyFunctions:
    """Tests for dimension key extraction functions."""

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

    def test_key_day_of_week(self, analyzer):
        """Test day of week key extraction."""
        decision = DecisionRecord(
            timestamp=datetime(2024, 1, 15, 12, 0),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.HOLD,
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
        key = analyzer._key_day_of_week(decision)
        assert key == "monday"

    def test_key_hour_of_day(self, analyzer):
        """Test hour of day key extraction."""
        decision = DecisionRecord(
            timestamp=datetime(2024, 1, 15, 14, 30),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.25,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=10.0,
            forecast_consumption_remaining_kwh=5.0,
            cheap_price_threshold=0.20,
            battery_target_soc=80.0,
            weather_condition="sunny",
            day_of_week=0,
            hour_of_day=14,
            is_demand_window=False,
            outcome_score=0.8,
        )
        key = analyzer._key_hour_of_day(decision)
        assert key == "14"

    def test_key_weather_condition(self, analyzer):
        """Test weather condition key extraction."""
        decision = DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.25,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=10.0,
            forecast_consumption_remaining_kwh=5.0,
            cheap_price_threshold=0.20,
            battery_target_soc=80.0,
            weather_condition="partly-cloudy",
            day_of_week=0,
            hour_of_day=12,
            is_demand_window=False,
            outcome_score=0.8,
        )
        key = analyzer._key_weather_condition(decision)
        assert key in ("partly-cloudy", "partly_cloudy", "cloudy")

    def test_key_season(self, analyzer):
        """Test season key extraction."""
        decision = DecisionRecord(
            timestamp=datetime(2024, 7, 15, 12, 0),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.HOLD,
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
        key = analyzer._key_season(decision)
        assert key in ("summer", "winter", "spring", "autumn", "fall")

    def test_key_price_regime(self, analyzer):
        """Test price regime key extraction."""
        decision = DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.10,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=10.0,
            forecast_consumption_remaining_kwh=5.0,
            cheap_price_threshold=0.15,
            battery_target_soc=80.0,
            weather_condition="sunny",
            day_of_week=0,
            hour_of_day=12,
            is_demand_window=False,
            outcome_score=0.8,
        )
        key = analyzer._key_price_regime(decision)
        assert key in ("low", "medium", "high")

    def test_key_solar_availability(self, analyzer):
        """Test solar availability key extraction."""
        decision = DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.25,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=25.0,
            forecast_consumption_remaining_kwh=5.0,
            cheap_price_threshold=0.20,
            battery_target_soc=80.0,
            weather_condition="sunny",
            day_of_week=0,
            hour_of_day=12,
            is_demand_window=False,
            outcome_score=0.8,
        )
        key = analyzer._key_solar_availability(decision)
        assert key in ("high", "medium", "low")


class TestPatternAnalyzerEdgeCases:
    """Tests for edge cases and missing branches."""

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

    def test_detect_biases_zero_variance(self, analyzer):
        """Test detect_biases skips dimension with zero variance."""
        report = PatternReport()
        stats = DimensionStats(dimension="test", global_mean=0.5, global_std=0.0)
        stats.groups["key"] = PatternBucket(
            key="key", dimension="test", sample_count=20, mean_score=0.3
        )
        report.dimensions["test"] = stats
        analyzer._weeks_of_data = 3
        biases = analyzer.detect_biases(report, [])
        assert biases == []

    def test_detect_biases_insufficient_samples(self, analyzer):
        """Test detect_biases skips bucket with insufficient samples."""
        report = PatternReport()
        stats = DimensionStats(dimension="test", global_mean=0.7, global_std=0.1)
        stats.groups["key"] = PatternBucket(
            key="key", dimension="test", sample_count=5, mean_score=0.3
        )
        report.dimensions["test"] = stats
        analyzer._weeks_of_data = 3
        biases = analyzer.detect_biases(report, [])
        assert biases == []

    def test_detect_biases_produces_correction(self, analyzer):
        """Test detect_biases produces a correction via _map_bias_to_correction."""
        report = PatternReport()
        stats = DimensionStats(
            dimension="weather_condition", global_mean=0.8, global_std=0.1
        )
        bucket = PatternBucket(
            key="cloudy",
            dimension="weather_condition",
            sample_count=30,
            mean_score=0.3,
            over_charge_rate=0.6,
        )
        stats.groups["cloudy"] = bucket
        report.dimensions["weather_condition"] = stats
        analyzer._weeks_of_data = 3
        biases = analyzer.detect_biases(report, [])
        assert len(biases) >= 1

    def test_map_bias_to_correction_returns_correction(self, analyzer):
        """Test _map_bias_to_correction returns a BiasCorrection."""
        bucket = PatternBucket(
            key="cloudy",
            dimension="weather_condition",
            sample_count=30,
            mean_score=0.3,
            over_charge_rate=0.6,
            std_score=0.1,
        )
        stats = DimensionStats(
            dimension="weather_condition", global_mean=0.8, global_std=0.1
        )
        correction = analyzer._map_bias_to_correction(
            "weather_condition", "cloudy", bucket, stats
        )
        assert correction is not None
        assert isinstance(correction, BiasCorrection)

    def test_calculate_bias_confidence_with_std(self, analyzer):
        """Test _calculate_bias_confidence with std_score > 0."""
        bucket = PatternBucket(
            key="test",
            dimension="test",
            sample_count=50,
            mean_score=0.3,
            std_score=0.2,
        )
        stats = DimensionStats(dimension="test", global_mean=0.8, global_std=0.1)
        confidence = analyzer._calculate_bias_confidence(bucket, stats)
        assert 0.0 <= confidence <= 1.0

    def test_check_over_charge_low_rate(self, analyzer):
        """Test _check_over_charge_adjustment returns None for low rate."""
        bucket = PatternBucket(
            key="test", dimension="test", sample_count=20, over_charge_rate=0.2
        )
        stats = DimensionStats(dimension="test", global_mean=0.7, global_std=0.1)
        result = analyzer._check_over_charge_adjustment(
            "test", "test", bucket, stats, 0.8
        )
        assert result is None

    def test_check_over_charge_day_of_week(self, analyzer):
        """Test _check_over_charge_adjustment for day_of_week dimension."""
        bucket = PatternBucket(
            key="monday", dimension="day_of_week", sample_count=20, over_charge_rate=0.5
        )
        stats = DimensionStats(dimension="day_of_week", global_mean=0.7, global_std=0.1)
        result = analyzer._check_over_charge_adjustment(
            "day_of_week", "monday", bucket, stats, 0.8
        )
        assert result is not None

    def test_check_over_charge_fallback_none(self, analyzer):
        """Test _check_over_charge_adjustment returns None for unmatched dimension."""
        bucket = PatternBucket(
            key="high", dimension="price_regime", sample_count=20, over_charge_rate=0.5
        )
        stats = DimensionStats(
            dimension="price_regime", global_mean=0.7, global_std=0.1
        )
        result = analyzer._check_over_charge_adjustment(
            "price_regime", "high", bucket, stats, 0.8
        )
        assert result is None

    def test_check_export_loss_low_rate(self, analyzer):
        """Test _check_export_loss_adjustment returns None for low rate."""
        bucket = PatternBucket(
            key="test", dimension="test", sample_count=20, export_loss_rate=0.1
        )
        stats = DimensionStats(dimension="test", global_mean=0.7, global_std=0.1)
        result = analyzer._check_export_loss_adjustment(
            "test", "test", bucket, stats, 0.8
        )
        assert result is None

    def test_check_export_loss_fallback(self, analyzer):
        """Test _check_export_loss_adjustment fallback correction."""
        bucket = PatternBucket(
            key="high", dimension="hour_of_day", sample_count=20, export_loss_rate=0.4
        )
        stats = DimensionStats(dimension="hour_of_day", global_mean=0.7, global_std=0.1)
        result = analyzer._check_export_loss_adjustment(
            "hour_of_day", "high", bucket, stats, 0.8
        )
        assert result is not None

    def test_check_under_charge_low_rate(self, analyzer):
        """Test _check_under_charge_adjustment returns None for low rate."""
        bucket = PatternBucket(
            key="test", dimension="test", sample_count=20, under_charge_rate=0.1
        )
        stats = DimensionStats(dimension="test", global_mean=0.7, global_std=0.1)
        result = analyzer._check_under_charge_adjustment(
            "test", "test", bucket, stats, 0.8
        )
        assert result is None

    def test_check_under_charge_solar_high(self, analyzer):
        """Test _check_under_charge_adjustment for high solar availability."""
        bucket = PatternBucket(
            key="high",
            dimension="solar_availability",
            sample_count=20,
            under_charge_rate=0.4,
        )
        stats = DimensionStats(
            dimension="solar_availability", global_mean=0.7, global_std=0.1
        )
        result = analyzer._check_under_charge_adjustment(
            "solar_availability", "high", bucket, stats, 0.8
        )
        assert result is not None

    def test_check_under_charge_fallback(self, analyzer):
        """Test _check_under_charge_adjustment fallback correction."""
        bucket = PatternBucket(
            key="medium",
            dimension="price_regime",
            sample_count=20,
            under_charge_rate=0.4,
        )
        stats = DimensionStats(
            dimension="price_regime", global_mean=0.7, global_std=0.1
        )
        result = analyzer._check_under_charge_adjustment(
            "price_regime", "medium", bucket, stats, 0.8
        )
        assert result is not None

    def test_check_generic_low_score_small_diff(self, analyzer):
        """Test _check_generic_low_score_adjustment returns None for small diff."""
        bucket = PatternBucket(
            key="test", dimension="test", sample_count=20, mean_score=0.65
        )
        stats = DimensionStats(dimension="test", global_mean=0.7, global_std=0.1)
        result = analyzer._check_generic_low_score_adjustment(
            "test", "test", bucket, stats, 0.8
        )
        assert result is None

    def test_check_generic_evening_hour(self, analyzer):
        """Test _check_generic_low_score_adjustment for evening hours."""
        bucket = PatternBucket(
            key="18", dimension="hour_of_day", sample_count=20, mean_score=0.3
        )
        stats = DimensionStats(dimension="hour_of_day", global_mean=0.7, global_std=0.1)
        result = analyzer._check_generic_low_score_adjustment(
            "hour_of_day", "18", bucket, stats, 0.8
        )
        assert result is not None

    def test_check_generic_summer(self, analyzer):
        """Test _check_generic_low_score_adjustment for summer."""
        bucket = PatternBucket(
            key="summer", dimension="season", sample_count=20, mean_score=0.3
        )
        stats = DimensionStats(dimension="season", global_mean=0.7, global_std=0.1)
        result = analyzer._check_generic_low_score_adjustment(
            "season", "summer", bucket, stats, 0.8
        )
        assert result is not None

    def test_check_generic_fallback_none(self, analyzer):
        """Test _check_generic_low_score_adjustment returns None for unmatched dimension."""
        bucket = PatternBucket(
            key="autumn", dimension="season", sample_count=20, mean_score=0.3
        )
        stats = DimensionStats(dimension="season", global_mean=0.7, global_std=0.1)
        result = analyzer._check_generic_low_score_adjustment(
            "season", "autumn", bucket, stats, 0.8
        )
        assert result is None

    def test_map_bias_to_correction_no_match(self, analyzer):
        """Test _map_bias_to_correction returns None when no check matches."""
        bucket = PatternBucket(
            key="medium",
            dimension="price_regime",
            sample_count=20,
            mean_score=0.3,
        )
        stats = DimensionStats(
            dimension="price_regime", global_mean=0.8, global_std=0.1
        )
        correction = analyzer._map_bias_to_correction(
            "price_regime", "medium", bucket, stats
        )
        assert correction is None

    def test_create_correction_invalid_param(self, analyzer):
        """Test _create_correction returns None for invalid parameter."""
        bucket = PatternBucket(key="test", dimension="test", sample_count=20)
        result = analyzer._create_correction(
            "invalid_param_name",
            0.1,
            "test condition",
            "test",
            "test",
            bucket,
            0.8,
        )
        assert result is None

    def test_key_weather_rain(self, analyzer):
        """Test _key_weather_condition for rain."""
        decision = DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.25,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=10.0,
            forecast_consumption_remaining_kwh=5.0,
            cheap_price_threshold=0.20,
            battery_target_soc=80.0,
            weather_condition="rainy",
            day_of_week=0,
            hour_of_day=12,
            is_demand_window=False,
            outcome_score=0.8,
        )
        assert analyzer._key_weather_condition(decision) == "rainy"

    def test_key_weather_snow(self, analyzer):
        """Test _key_weather_condition for snow."""
        decision = DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.25,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=10.0,
            forecast_consumption_remaining_kwh=5.0,
            cheap_price_threshold=0.20,
            battery_target_soc=80.0,
            weather_condition="snowy",
            day_of_week=0,
            hour_of_day=12,
            is_demand_window=False,
            outcome_score=0.8,
        )
        assert analyzer._key_weather_condition(decision) == "snow"

    def test_key_weather_fog(self, analyzer):
        """Test _key_weather_condition for fog."""
        decision = DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.25,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=10.0,
            forecast_consumption_remaining_kwh=5.0,
            cheap_price_threshold=0.20,
            battery_target_soc=80.0,
            weather_condition="foggy",
            day_of_week=0,
            hour_of_day=12,
            is_demand_window=False,
            outcome_score=0.8,
        )
        assert analyzer._key_weather_condition(decision) == "foggy"

    def test_key_weather_unknown(self, analyzer):
        """Test _key_weather_condition for unknown."""
        decision = DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.25,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=10.0,
            forecast_consumption_remaining_kwh=5.0,
            cheap_price_threshold=0.20,
            battery_target_soc=80.0,
            weather_condition="hurricane",
            day_of_week=0,
            hour_of_day=12,
            is_demand_window=False,
            outcome_score=0.8,
        )
        assert analyzer._key_weather_condition(decision) == "unknown"

    def test_key_season_spring(self, analyzer):
        """Test _key_season for spring (Sep/Oct/Nov in southern hemisphere)."""
        decision = DecisionRecord(
            timestamp=datetime(2024, 9, 15, 12, 0),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.HOLD,
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
        assert analyzer._key_season(decision) == "spring"

    def test_key_price_regime_low(self, analyzer):
        """Test _key_price_regime for low price."""
        decision = DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.05,
            feed_in_price_at_decision=0.02,
            forecast_solar_remaining_kwh=10.0,
            forecast_consumption_remaining_kwh=5.0,
            cheap_price_threshold=0.15,
            battery_target_soc=80.0,
            weather_condition="sunny",
            day_of_week=0,
            hour_of_day=12,
            is_demand_window=False,
            outcome_score=0.8,
        )
        assert analyzer._key_price_regime(decision) == "low"

    def test_key_solar_availability_low(self, analyzer):
        """Test _key_solar_availability for low solar."""
        decision = DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.25,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=2.0,
            forecast_consumption_remaining_kwh=5.0,
            cheap_price_threshold=0.20,
            battery_target_soc=80.0,
            weather_condition="sunny",
            day_of_week=0,
            hour_of_day=12,
            is_demand_window=False,
            outcome_score=0.8,
        )
        assert analyzer._key_solar_availability(decision) == "low"


class TestPatternAnalyzerUtilityMethods:
    """Tests for utility methods."""

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

    def test_should_run_analysis(self, analyzer):
        """Test should_run_analysis decision logic."""
        assert analyzer.should_run_analysis(days_since_last=7, new_decisions=100)
        assert not analyzer.should_run_analysis(days_since_last=1, new_decisions=5)
        assert analyzer.should_run_analysis(days_since_last=1, new_decisions=50)

    def test_reset(self, analyzer):
        """Test reset clears all data."""
        analyzer._pattern_data = {"test": "data"}
        analyzer._last_analysis_time = datetime.now()
        analyzer._weeks_of_data = 5
        analyzer.reset()
        assert analyzer._pattern_data == {}
        assert analyzer._last_analysis_time is None
        assert analyzer._weeks_of_data == 0

    def test_get_last_report_none(self, analyzer):
        """Test get_last_report returns None when no report."""
        assert analyzer.get_last_report() is None

    def test_get_diagnostics(self, analyzer):
        """Test get_diagnostics returns valid dict."""
        diagnostics = analyzer.get_diagnostics()
        assert isinstance(diagnostics, dict)
        assert "weeks_of_data" in diagnostics
        assert "last_analysis" in diagnostics
