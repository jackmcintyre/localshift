"""Tests for the DecisionOutcomeTracker learning system (Issue #170 Phase 1)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.computation_engine_lib.decision_outcome_tracker import (
    DecisionOutcomeTracker,
    DecisionRecord,
)
from custom_components.localshift.computation_engine_lib.optimizer_dp import (
    PlannerAction,
)
from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator_data import (
    CoordinatorData,
    PerformanceMetrics,
)


@pytest.fixture
def mock_hass():
    """Create a mock HomeAssistant instance."""
    hass = MagicMock()
    hass.storage = MagicMock()
    return hass


@pytest.fixture
def coordinator_data():
    """Create a CoordinatorData instance with sample values."""
    data = CoordinatorData()
    data.soc = 50.0
    data.battery_target_soc = 80.0
    data.general_price = 0.25
    data.feed_in_price = 0.05
    data.solar_remaining_kwh = 15.0
    data.effective_cheap_price = 0.10
    data.weather_condition = "sunny"
    data.demand_window_active = False
    data.consumption_hourly_profile_kw = {14: 1.0, 15: 1.2, 16: 1.5}
    return data


class TestDecisionRecord:
    """Tests for DecisionRecord dataclass."""

    def test_decision_record_creation(self):
        """Test creating a DecisionRecord (Issue #449: uses PlannerAction)."""
        now = datetime.now()
        record = DecisionRecord(
            timestamp=now,
            mode_chosen=PlannerAction.CHARGE_GRID_NORMAL,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.25,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=15.0,
            forecast_consumption_remaining_kwh=12.0,
            cheap_price_threshold=0.10,
            battery_target_soc=80.0,
            weather_condition="sunny",
            day_of_week=0,
            hour_of_day=14,
            is_demand_window=False,
        )

        assert record.timestamp == now
        assert record.mode_chosen == PlannerAction.CHARGE_GRID_NORMAL
        assert record.previous_mode == PlannerAction.HOLD
        assert record.soc_at_decision == 50.0
        assert record.outcome_score is None  # Pending

    def test_decision_record_to_dict(self):
        """Test serializing DecisionRecord to dict (Issue #449: DP action strings)."""
        now = datetime.now()
        record = DecisionRecord(
            timestamp=now,
            mode_chosen=PlannerAction.CHARGE_GRID_NORMAL,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.25,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=15.0,
            forecast_consumption_remaining_kwh=12.0,
            cheap_price_threshold=0.10,
            battery_target_soc=80.0,
            weather_condition="sunny",
            day_of_week=0,
            hour_of_day=14,
            is_demand_window=False,
            outcome_score=0.75,
        )

        result = record.to_dict()

        assert result["mode_chosen"] == "charge_grid_normal"
        assert result["previous_mode"] == "hold"
        assert result["soc_at_decision"] == 50.0
        assert result["outcome_score"] == 0.75

    def test_decision_record_from_dict_dp_action(self):
        """Test deserializing DecisionRecord from dict with native DP action strings."""
        now = datetime.now()
        data = {
            "timestamp": now.isoformat(),
            "mode_chosen": "export_proactive",
            "previous_mode": "hold",
            "soc_at_decision": 75.0,
            "general_price_at_decision": 2.50,
            "feed_in_price_at_decision": 0.05,
            "forecast_solar_remaining_kwh": 10.0,
            "forecast_consumption_remaining_kwh": 8.0,
            "cheap_price_threshold": 0.10,
            "battery_target_soc": 80.0,
            "weather_condition": "cloudy",
            "day_of_week": 2,
            "hour_of_day": 18,
            "is_demand_window": True,
            "actual_soc_change": -5.0,
            "duration_minutes": 15.0,
            "next_mode": "hold",
            "outcome_score": 0.85,
        }

        record = DecisionRecord.from_dict(data)

        assert record.mode_chosen == PlannerAction.EXPORT_PROACTIVE
        assert record.previous_mode == PlannerAction.HOLD
        assert record.soc_at_decision == 75.0
        assert record.outcome_score == 0.85

    def test_decision_record_from_dict_legacy_mode(self):
        """Test deserializing DecisionRecord with legacy BatteryMode strings (backward compat)."""
        now = datetime.now()
        data = {
            "timestamp": now.isoformat(),
            "mode_chosen": "spike_discharge",  # legacy BatteryMode value
            "previous_mode": "self_consumption",  # legacy BatteryMode value
            "soc_at_decision": 75.0,
            "general_price_at_decision": 2.50,
            "feed_in_price_at_decision": 0.05,
            "forecast_solar_remaining_kwh": 10.0,
            "forecast_consumption_remaining_kwh": 8.0,
            "cheap_price_threshold": 0.10,
            "battery_target_soc": 80.0,
            "weather_condition": "cloudy",
            "day_of_week": 2,
            "hour_of_day": 18,
            "is_demand_window": True,
            "actual_soc_change": -5.0,
            "duration_minutes": 15.0,
            "next_mode": "self_consumption",
            "outcome_score": 0.85,
        }

        record = DecisionRecord.from_dict(data)

        # Legacy spike_discharge → EXPORT_PROACTIVE
        assert record.mode_chosen == PlannerAction.EXPORT_PROACTIVE
        # Legacy self_consumption → HOLD
        assert record.previous_mode == PlannerAction.HOLD
        assert record.soc_at_decision == 75.0
        assert record.outcome_score == 0.85


class TestPerformanceMetrics:
    """Tests for PerformanceMetrics dataclass."""

    def test_performance_metrics_defaults(self):
        """Test default PerformanceMetrics values."""
        metrics = PerformanceMetrics()

        assert metrics.total_decisions_today == 0
        assert metrics.avg_decision_score_today == 0.0
        assert metrics.cost_trend == "stable"
        assert metrics.mode_durations_today == {}

    def test_performance_metrics_to_dict(self):
        """Test serializing PerformanceMetrics to dict."""
        metrics = PerformanceMetrics(
            total_decisions_today=5,
            avg_decision_score_today=0.75,
            cost_trend="improving",
            mode_durations_today={"grid_charging": 30.0, "self_consumption": 60.0},
        )

        result = metrics.to_dict()

        assert result["total_decisions_today"] == 5
        assert result["avg_decision_score_today"] == 0.75
        assert result["cost_trend"] == "improving"

    def test_performance_metrics_from_dict(self):
        """Test deserializing PerformanceMetrics from dict."""
        data = {
            "total_decisions_today": 3,
            "avg_decision_score_today": 0.8,
            "cost_trend": "stable",
            "mode_durations_today": {"spike_discharge": 15.0},
        }

        metrics = PerformanceMetrics.from_dict(data)

        assert metrics.total_decisions_today == 3
        assert metrics.avg_decision_score_today == 0.8


class TestDecisionOutcomeTracker:
    """Tests for DecisionOutcomeTracker class."""

    def test_tracker_initialization(self, mock_hass):
        """Test tracker initialization."""
        tracker = DecisionOutcomeTracker(mock_hass, "test_entry_id")

        assert tracker._hass == mock_hass
        assert tracker._pending_decisions == []
        assert tracker.completed_count == 0

    def test_record_decision(self, mock_hass, coordinator_data):
        """Test recording a decision."""
        tracker = DecisionOutcomeTracker(mock_hass, "test_entry_id")

        tracker.record_decision(
            coordinator_data,
            BatteryMode.GRID_CHARGING,
            BatteryMode.SELF_CONSUMPTION,
        )

        assert tracker.pending_count == 1
        # record_decision normalises BatteryMode → PlannerAction
        assert (
            tracker._pending_decisions[0].mode_chosen
            == PlannerAction.CHARGE_GRID_NORMAL
        )
        assert tracker._pending_decisions[0].previous_mode == PlannerAction.HOLD

    def test_backfill_on_next_decision(self, mock_hass, coordinator_data):
        """Test that backfill happens when next decision is recorded."""
        tracker = DecisionOutcomeTracker(mock_hass, "test_entry_id")

        # Record first decision
        tracker.record_decision(
            coordinator_data,
            BatteryMode.GRID_CHARGING,
            BatteryMode.SELF_CONSUMPTION,
        )
        assert tracker.pending_count == 1
        assert tracker.completed_count == 0

        # Record second decision - should backfill first
        coordinator_data.soc = 60.0  # Battery gained 10%
        tracker.record_decision(
            coordinator_data,
            BatteryMode.SELF_CONSUMPTION,
            BatteryMode.GRID_CHARGING,
        )

        # First decision should be completed
        assert tracker.completed_count == 1
        assert tracker._completed_decisions[0].outcome_score is not None

    def test_get_daily_summary(self, mock_hass, coordinator_data):
        """Test getting daily summary metrics."""
        tracker = DecisionOutcomeTracker(mock_hass, "test_entry_id")

        # Record and complete a decision
        tracker.record_decision(
            coordinator_data,
            BatteryMode.GRID_CHARGING,
            BatteryMode.SELF_CONSUMPTION,
        )

        coordinator_data.soc = 60.0
        tracker.record_decision(
            coordinator_data,
            BatteryMode.SELF_CONSUMPTION,
            BatteryMode.GRID_CHARGING,
        )

        summary = tracker.get_daily_summary()

        assert summary.total_decisions_today == 1
        assert summary.avg_decision_score_today >= 0.0

    def test_get_decision_log(self, mock_hass, coordinator_data):
        """Test getting decision log."""
        tracker = DecisionOutcomeTracker(mock_hass, "test_entry_id")

        # Record and complete a few decisions
        for i in range(3):
            tracker.record_decision(
                coordinator_data,
                BatteryMode.GRID_CHARGING
                if i % 2 == 0
                else BatteryMode.SELF_CONSUMPTION,
                BatteryMode.SELF_CONSUMPTION
                if i % 2 == 0
                else BatteryMode.GRID_CHARGING,
            )
            coordinator_data.soc += 5.0

        # Last decision stays pending
        log = tracker.get_decision_log(limit=10)

        # Should have 2 completed decisions (the last one is still pending)
        assert len(log) == 2

    @pytest.mark.asyncio
    async def test_async_save_and_load(self, mock_hass, coordinator_data):
        """Test saving and loading decisions from storage."""
        # Mock the storage with async methods
        mock_store = MagicMock()
        mock_store.async_load = MagicMock(return_value={"completed_decisions": []})

        # Create an async mock for async_save
        async def mock_async_save(data):
            pass

        mock_store.async_save = mock_async_save

        with patch(
            "custom_components.localshift.computation_engine_lib.decision_outcome_tracker.Store",
            return_value=mock_store,
        ):
            tracker = DecisionOutcomeTracker(mock_hass, "test_entry_id")

            # Record and complete a decision
            tracker.record_decision(
                coordinator_data,
                BatteryMode.GRID_CHARGING,
                BatteryMode.SELF_CONSUMPTION,
            )
            coordinator_data.soc = 60.0
            tracker.record_decision(
                coordinator_data,
                BatteryMode.SELF_CONSUMPTION,
                BatteryMode.GRID_CHARGING,
            )

            # Save - should not raise
            await tracker.async_save()

            # Verify we have completed decisions
            assert tracker.completed_count == 1


class TestDecisionOutcomeScoring:
    """Tests for decision outcome scoring logic."""

    def test_compute_outcome_score_grid_charging(self, mock_hass, coordinator_data):
        """Test scoring for grid charging decision (Issue #449: uses PlannerAction)."""
        tracker = DecisionOutcomeTracker(mock_hass, "test_entry_id")

        record = DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.CHARGE_GRID_NORMAL,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.10,  # Low price
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=5.0,
            forecast_consumption_remaining_kwh=10.0,
            cheap_price_threshold=0.15,
            battery_target_soc=80.0,
            weather_condition="cloudy",
            day_of_week=0,
            hour_of_day=14,
            is_demand_window=False,
            actual_soc_change=10.0,  # Gained 10% SOC
            duration_minutes=30.0,
        )

        score = tracker.compute_outcome_score(record)

        # Grid charging at low price should score well
        assert 0.5 <= score <= 1.0

    def test_compute_outcome_score_proactive_export(self, mock_hass, coordinator_data):
        """Test scoring for proactive export decision (Issue #449: uses PlannerAction)."""
        tracker = DecisionOutcomeTracker(mock_hass, "test_entry_id")

        record = DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.EXPORT_PROACTIVE,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=80.0,
            general_price_at_decision=2.50,  # High sell price
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=0.0,
            forecast_consumption_remaining_kwh=5.0,
            cheap_price_threshold=0.15,
            battery_target_soc=80.0,
            weather_condition="sunny",
            day_of_week=0,
            hour_of_day=18,
            is_demand_window=True,
            actual_soc_change=-10.0,  # Lost 10% SOC (discharged)
            actual_cost_during_period=-2.50,  # Made money
            duration_minutes=15.0,
        )

        score = tracker.compute_outcome_score(record)

        # Proactive export during high price should score well
        assert 0.5 <= score <= 1.0

    def test_compute_outcome_score_short_duration_penalty(
        self, mock_hass, coordinator_data
    ):
        """Test that short duration decisions get penalized."""
        tracker = DecisionOutcomeTracker(mock_hass, "test_entry_id")

        # Short duration decision
        record_short = DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.CHARGE_GRID_NORMAL,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.10,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=5.0,
            forecast_consumption_remaining_kwh=10.0,
            cheap_price_threshold=0.15,
            battery_target_soc=80.0,
            weather_condition="cloudy",
            day_of_week=0,
            hour_of_day=14,
            is_demand_window=False,
            duration_minutes=2.0,  # Very short - rapid cycling
        )

        # Long duration decision
        record_long = DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.CHARGE_GRID_NORMAL,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.10,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=5.0,
            forecast_consumption_remaining_kwh=10.0,
            cheap_price_threshold=0.15,
            battery_target_soc=80.0,
            weather_condition="cloudy",
            day_of_week=0,
            hour_of_day=14,
            is_demand_window=False,
            duration_minutes=30.0,
        )

        score_short = tracker.compute_outcome_score(record_short)
        score_long = tracker.compute_outcome_score(record_long)

        # Short duration should be penalized
        assert score_short < score_long
