"""Tests for the DecisionOutcomeTracker learning system (Issue #170 Phase 1)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.const import BatteryMode
from custom_components.localshift.coordinator import (
    CoordinatorData,
    PerformanceMetrics,
)
from custom_components.localshift.engine.optimizer_dp import (
    PlannerAction,
)
from custom_components.localshift.engine.outcomes import (
    DecisionOutcomeTracker,
    DecisionRecord,
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
            "custom_components.localshift.engine.outcomes.Store",
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

    def test_compute_outcome_score_hold_null_cost_data(
        self, mock_hass, coordinator_data
    ):
        """Test HOLD decision with null cost data gets neutral cost score.

        Bug #1: When actual_cost_during_period is None (common for HOLD/self-consumption),
        the cost scoring section was skipped entirely, leaving score at 0.5 base.
        Expected: HOLD with null data should get neutral cost score (0.5), resulting in
        score = 0.5 * 0.6 + 0.5 * 0.4 = 0.5 (proper weighted average).
        """
        tracker = DecisionOutcomeTracker(mock_hass, "test_entry_id")

        record = DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.HOLD,
            previous_mode=PlannerAction.CHARGE_GRID_NORMAL,
            soc_at_decision=50.0,
            general_price_at_decision=0.10,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=5.0,
            forecast_consumption_remaining_kwh=10.0,
            cheap_price_threshold=0.15,
            battery_target_soc=55.0,
            weather_condition="sunny",
            day_of_week=0,
            hour_of_day=14,
            is_demand_window=False,
            actual_soc_change=5.0,
            duration_minutes=30.0,
        )

        score = tracker.compute_outcome_score(record)

        assert score > 0.5, "HOLD with null cost data should get neutral cost boost"

    def test_compute_outcome_score_duration_threshold_3min(
        self, mock_hass, coordinator_data
    ):
        """Test cycling penalty threshold is 3 minutes, not 5 minutes.

        Bug #2: Control loop runs every 5 minutes (PERIODIC_INTERVAL_MEDIUM).
        Old threshold of 5 minutes penalized normal re-planning.
        Expected: Durations 3-5 min should NOT be penalized (normal re-planning).
        Durations < 3 min should be penalized (actual cycling).
        """
        tracker = DecisionOutcomeTracker(mock_hass, "test_entry_id")

        record_2min = DecisionRecord(
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
            duration_minutes=2.0,
        )

        record_4min = DecisionRecord(
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
            duration_minutes=4.0,
        )

        score_2min = tracker.compute_outcome_score(record_2min)
        score_4min = tracker.compute_outcome_score(record_4min)

        assert score_2min < 0.5, "2-min duration should be penalized (actual cycling)"
        assert score_4min >= 0.5, (
            "4-min duration should NOT be penalized (normal re-planning)"
        )

    def test_compute_outcome_score_short_duration_penalty(
        self, mock_hass, coordinator_data
    ):
        """Test that short duration decisions get penalized."""
        tracker = DecisionOutcomeTracker(mock_hass, "test_entry_id")

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
            duration_minutes=2.0,
        )

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

        assert score_short < score_long


class TestTargetScoreGradient:
    """Tests for smooth gradient target scoring (Issue #626 Task 1)."""

    @pytest.fixture
    def tracker(self, mock_hass):
        """Create a tracker for testing."""
        return DecisionOutcomeTracker(mock_hass, "test_entry_id")

    def _make_record(
        self,
        soc_at_decision: float,
        actual_soc_change: float,
        target_soc: float,
        weather: str = "sunny",
        mode: PlannerAction = PlannerAction.HOLD,
        forecast_solar: float = 10.0,
    ) -> DecisionRecord:
        """Helper to create a DecisionRecord with minimal boilerplate."""
        return DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=mode,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=soc_at_decision,
            general_price_at_decision=0.10,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=forecast_solar,
            forecast_consumption_remaining_kwh=5.0,
            cheap_price_threshold=0.15,
            battery_target_soc=target_soc,
            weather_condition=weather,
            day_of_week=0,
            hour_of_day=14,
            is_demand_window=False,
            actual_soc_change=actual_soc_change,
            duration_minutes=30.0,
        )

    def test_target_diff_zero_returns_max_bonus(self, tracker):
        """Exact target hit: diff=0 should return +0.15."""
        record = self._make_record(
            soc_at_decision=50.0,
            actual_soc_change=30.0,
            target_soc=80.0,
        )
        assert tracker._compute_target_score(record) == pytest.approx(0.15)

    def test_target_diff_15_returns_zero(self, tracker):
        """End of gradient: diff=15 should return 0.0."""
        record = self._make_record(
            soc_at_decision=50.0,
            actual_soc_change=15.0,
            target_soc=80.0,
        )
        assert tracker._compute_target_score(record) == pytest.approx(0.0)

    def test_target_diff_10_returns_mid_gradient(self, tracker):
        """Mid gradient: diff=10 should return +0.05 (same as before, smoother path)."""
        record = self._make_record(
            soc_at_decision=50.0,
            actual_soc_change=20.0,
            target_soc=80.0,
        )
        assert tracker._compute_target_score(record) == pytest.approx(0.05)

    def test_target_diff_5_in_gradient(self, tracker):
        """Within gradient: diff=5 should return +0.10."""
        record = self._make_record(
            soc_at_decision=50.0,
            actual_soc_change=25.0,
            target_soc=80.0,
        )
        assert tracker._compute_target_score(record) == pytest.approx(0.10)

    def test_neutral_zone_sunny(self, tracker):
        """Neutral zone: diff=18 (sunny, far_threshold=20) should return 0.0."""
        record = self._make_record(
            soc_at_decision=50.0,
            actual_soc_change=12.0,
            target_soc=80.0,
            weather="sunny",
        )
        assert tracker._compute_target_score(record) == pytest.approx(0.0)

    def test_far_penalty_below_target_can_increase(self, tracker):
        """Far penalty: diff=25 (sunny), CHARGE_GRID, below target -> -0.10."""
        record = self._make_record(
            soc_at_decision=50.0,
            actual_soc_change=5.0,
            target_soc=80.0,
            weather="sunny",
            mode=PlannerAction.CHARGE_GRID_NORMAL,
        )
        assert tracker._compute_target_score(record) == pytest.approx(-0.10)

    def test_far_penalty_above_target_can_decrease(self, tracker):
        """Far penalty: diff=25 (sunny), EXPORT, above target -> -0.10."""
        record = self._make_record(
            soc_at_decision=90.0,
            actual_soc_change=0.0,
            target_soc=65.0,
            weather="sunny",
            mode=PlannerAction.EXPORT_PROACTIVE,
        )
        assert tracker._compute_target_score(record) == pytest.approx(-0.10)

    def test_far_no_penalty_wrong_mode(self, tracker):
        """Far no-penalty: diff=25 (sunny), EXPORT, below target -> 0.0 (can't increase)."""
        record = self._make_record(
            soc_at_decision=50.0,
            actual_soc_change=5.0,
            target_soc=80.0,
            weather="sunny",
            mode=PlannerAction.EXPORT_PROACTIVE,
        )
        assert tracker._compute_target_score(record) == pytest.approx(0.0)

    def test_neutral_zone_low_solar_weather(self, tracker):
        """Low-solar neutral zone: diff=35 (rainy, far_threshold=40) -> 0.0."""
        record = self._make_record(
            soc_at_decision=50.0,
            actual_soc_change=-5.0,
            target_soc=80.0,
            weather="rainy",
        )
        assert tracker._compute_target_score(record) == pytest.approx(0.0)

    def test_far_penalty_low_solar_weather(self, tracker):
        """Low-solar far penalty: diff=50 (rainy), CHARGE_GRID, below -> -0.10."""
        record = self._make_record(
            soc_at_decision=20.0,
            actual_soc_change=10.0,
            target_soc=80.0,
            weather="rainy",
            mode=PlannerAction.CHARGE_GRID_NORMAL,
        )
        assert tracker._compute_target_score(record) == pytest.approx(-0.10)

    def test_gradient_is_smooth(self, tracker):
        """Gradient should be smooth: each step changes score by ~0.01."""
        scores = []
        for diff in range(0, 16):
            record = self._make_record(
                soc_at_decision=50.0,
                actual_soc_change=30.0 - diff,
                target_soc=80.0,
            )
            scores.append(tracker._compute_target_score(record))

        for i in range(1, len(scores)):
            delta = abs(scores[i] - scores[i - 1])
            assert delta == pytest.approx(0.01, abs=0.001), (
                f"Score change from diff={i - 1} to diff={i} is {delta}, expected ~0.01"
            )

    def test_achievability_full_solar_coverage(self, tracker):
        """Full achievability (solar=20kWh, required=10kWh) -> -0.10."""
        record = self._make_record(
            soc_at_decision=50.0,
            actual_soc_change=5.0,  # Only got to 55%, target is 80%
            target_soc=80.0,
            weather="sunny",
            mode=PlannerAction.CHARGE_GRID_NORMAL,
            forecast_solar=20.0,  # 20kWh available
        )
        # Required: 25% * 13.5kWh / 100 = 3.375kWh
        # Available: 20kWh
        # Achievability = min(20/3.375, 1.0) = 1.0
        assert tracker._compute_target_score(record) == pytest.approx(-0.10)

    def test_achievability_partial_solar_coverage(self, tracker):
        """Partial achievability (solar=5kWh, required=10kWh) -> -0.05."""
        record = self._make_record(
            soc_at_decision=50.0,
            actual_soc_change=5.0,  # Only got to 55%, target is 80%
            target_soc=80.0,
            weather="sunny",
            mode=PlannerAction.CHARGE_GRID_NORMAL,
            forecast_solar=1.6875,  # Half of required
        )
        # Required: 25% * 13.5kWh / 100 = 3.375kWh
        # Available: 1.6875kWh
        # Achievability = min(1.6875/3.375, 1.0) = 0.5
        assert tracker._compute_target_score(record) == pytest.approx(-0.05)

    def test_achievability_zero_solar(self, tracker):
        """Zero solar (solar=0, required=10kWh) -> ~0.0."""
        record = self._make_record(
            soc_at_decision=50.0,
            actual_soc_change=5.0,
            target_soc=80.0,
            weather="sunny",
            mode=PlannerAction.CHARGE_GRID_NORMAL,
            forecast_solar=0.0,
        )
        # With no solar, achievability approaches 0
        assert tracker._compute_target_score(record) == pytest.approx(0.0, abs=0.01)

    def test_achievability_no_forecast_data(self, tracker):
        """No forecast data (solar=0) -> ~0.0."""
        record = self._make_record(
            soc_at_decision=50.0,
            actual_soc_change=5.0,
            target_soc=80.0,
            weather="sunny",
            mode=PlannerAction.CHARGE_GRID_NORMAL,
            forecast_solar=0.0,
        )
        # With no forecast data, treated as 0 available
        assert tracker._compute_target_score(record) == pytest.approx(0.0, abs=0.01)


class TestCostValueScoring:
    """Tests for Task 3: Cost-value scoring (Issue #626)."""

    @pytest.fixture
    def tracker(self, mock_hass):
        """Create a tracker for testing."""
        return DecisionOutcomeTracker(mock_hass, "test_entry")

    @pytest.fixture
    def base_record(self):
        """Create a base DecisionRecord for testing."""
        return DecisionRecord(
            timestamp=datetime.now(),
            mode_chosen=PlannerAction.CHARGE_GRID_NORMAL,
            previous_mode=PlannerAction.HOLD,
            soc_at_decision=50.0,
            general_price_at_decision=0.25,
            feed_in_price_at_decision=0.05,
            forecast_solar_remaining_kwh=10.0,
            forecast_consumption_remaining_kwh=8.0,
            cheap_price_threshold=0.10,
            battery_target_soc=80.0,
            weather_condition="sunny",
            day_of_week=0,
            hour_of_day=14,
            is_demand_window=False,
        )

    def test_grid_charge_50_percent_threshold(self, tracker, base_record):
        """Grid charge at 50% of threshold -> high score (~0.8)."""
        record = base_record
        record.actual_cost_during_period = 0.05  # 50% of 0.10 threshold
        # ratio = 0.05 / 0.10 = 0.5
        # score = 1.0 - 0.5 * 0.4 = 1.0 - 0.2 = 0.8
        assert tracker._compute_cost_score(record) == pytest.approx(0.8)

    def test_grid_charge_150_percent_threshold(self, tracker, base_record):
        """Grid charge at 150% of threshold -> low score (~0.4)."""
        record = base_record
        record.actual_cost_during_period = 0.15  # 150% of 0.10 threshold
        # ratio = 0.15 / 0.10 = 1.5
        # score = 1.0 - 1.5 * 0.4 = 1.0 - 0.6 = 0.4
        assert tracker._compute_cost_score(record) == pytest.approx(0.4)

    def test_grid_charge_with_export_penalty(self, tracker, base_record):
        """Grid charge with export > 0.5kWh -> 0.2 (unchanged)."""
        record = base_record
        record.actual_cost_during_period = 0.05
        record.actual_export_kwh = 1.0  # > 0.5
        assert tracker._compute_cost_score(record) == pytest.approx(0.2)

    def test_export_with_negative_cost(self, tracker, base_record):
        """Export with negative cost (revenue) -> high score (0.7+)."""
        record = base_record
        record.mode_chosen = PlannerAction.EXPORT_PROACTIVE
        record.actual_cost_during_period = -0.10  # Revenue
        # revenue_ratio = 0.10 / 0.10 = 1.0
        # score = min(0.95, 0.6 + 1.0 * 0.2) = 0.8
        assert tracker._compute_cost_score(record) == pytest.approx(0.8)

    def test_export_with_positive_cost(self, tracker, base_record):
        """Export with positive cost -> 0.3."""
        record = base_record
        record.mode_chosen = PlannerAction.EXPORT_PROACTIVE
        record.actual_cost_during_period = 0.05  # Cost money
        assert tracker._compute_cost_score(record) == pytest.approx(0.3)

    def test_hold_with_low_cost(self, tracker, base_record):
        """Hold with low cost -> ~0.6."""
        record = base_record
        record.mode_chosen = PlannerAction.HOLD
        record.actual_cost_during_period = 0.05  # Low cost
        # ratio = 0.05 / 0.10 = 0.5
        # score = 0.65 - 0.5 * 0.1 = 0.65 - 0.05 = 0.6
        assert tracker._compute_cost_score(record) == pytest.approx(0.6)

    def test_hold_with_high_cost(self, tracker, base_record):
        """Hold with high cost -> ~0.4."""
        record = base_record
        record.mode_chosen = PlannerAction.HOLD
        record.actual_cost_during_period = 0.25  # High cost
        # ratio = 0.25 / 0.10 = 2.5
        # score = 0.65 - 2.5 * 0.1 = 0.65 - 0.25 = 0.4
        assert tracker._compute_cost_score(record) == pytest.approx(0.4)

    def test_none_cost_returns_none(self, tracker, base_record):
        """None cost -> None (null handling preserved)."""
        record = base_record
        record.actual_cost_during_period = None
        assert tracker._compute_cost_score(record) is None
