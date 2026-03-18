"""Tests for counterfactual TOU baseline scoring.

Issue #683: Counterfactual scoring against TOU baseline to measure optimizer value.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.util import dt as dt_util

from custom_components.localshift.coordinator.data import PerformanceMetrics
from custom_components.localshift.engine.counterfactual import (
    CounterfactualEvaluator,
    CounterfactualPeriod,
    CounterfactualResult,
    CounterfactualScoreIntegrator,
    TOUBaselineSimulator,
)
from custom_components.localshift.engine.optimizer_dp import PlannerAction


class MockDecision:
    """Mock decision for testing."""

    def __init__(
        self,
        timestamp: datetime,
        hour_of_day: int,
        duration_minutes: float = 30.0,
        mode_chosen: PlannerAction = PlannerAction.HOLD,
        soc_at_decision: float = 50.0,
        actual_cost_during_period: float = 0.0,
        actual_soc_change: float = 0.0,
        general_price_at_decision: float = 0.15,
    ):
        self.timestamp = timestamp
        self.hour_of_day = hour_of_day
        self.duration_minutes = duration_minutes
        self.mode_chosen = mode_chosen
        self.soc_at_decision = soc_at_decision
        self.actual_cost_during_period = actual_cost_during_period
        self.actual_soc_change = actual_soc_change
        self.general_price_at_decision = general_price_at_decision


class TestTOUBaselineSimulator:
    """Test TOU baseline simulator."""

    def test_identify_cheapest_hours_empty(self):
        """Test cheapest hour identification with empty forecast."""
        simulator = TOUBaselineSimulator()
        cheapest = simulator.identify_cheapest_hours([])
        assert cheapest == {0, 1, 2, 3}

    def test_identify_cheapest_hours_simple(self):
        """Test cheapest hour identification with simple forecast."""
        simulator = TOUBaselineSimulator()
        price_forecast = [{"hour": i, "price": 0.1 + (i * 0.01)} for i in range(24)]
        cheapest = simulator.identify_cheapest_hours(price_forecast)
        assert len(cheapest) == 4
        assert 0 in cheapest
        assert 1 in cheapest
        assert 2 in cheapest
        assert 3 in cheapest

    def test_identify_cheapest_hours_varied_prices(self):
        """Test cheapest hour identification with varied prices."""
        simulator = TOUBaselineSimulator()
        price_forecast = [
            {"hour": 2, "price": 0.05},
            {"hour": 3, "price": 0.04},
            {"hour": 4, "price": 0.06},
            {"hour": 5, "price": 0.03},
        ]
        cheapest = simulator.identify_cheapest_hours(price_forecast, num_hours=2)
        assert cheapest == {3, 5}

    def test_simulate_tou_action_charge_hours(self):
        """Test TOU action during charge hours."""
        simulator = TOUBaselineSimulator()
        simulator._charge_hours = {2, 3, 4, 5}

        action, new_soc, cost = simulator.simulate_tou_action(
            hour=3,
            soc=50.0,
            price=0.10,
            solar_kwh=0.0,
            consumption_kwh=0.0,
        )

        assert action in (
            PlannerAction.CHARGE_GRID_NORMAL,
            PlannerAction.CHARGE_GRID_BOOST,
        )
        assert new_soc > 50.0
        assert cost > 0

    def test_simulate_tou_action_peak_hours(self):
        """Test TOU action during peak hours with excess solar."""
        simulator = TOUBaselineSimulator()
        simulator._charge_hours = {2, 3, 4, 5}

        action, new_soc, cost = simulator.simulate_tou_action(
            hour=12,
            soc=50.0,
            price=0.30,
            solar_kwh=2.0,
            consumption_kwh=1.0,
        )

        assert new_soc > 50.0
        assert cost == 0.0

    def test_simulate_tou_action_full_battery(self):
        """Test TOU action when battery is already full."""
        simulator = TOUBaselineSimulator()
        simulator._charge_hours = {2, 3, 4, 5}

        action, new_soc, cost = simulator.simulate_tou_action(
            hour=3,
            soc=100.0,
            price=0.10,
            solar_kwh=0.0,
            consumption_kwh=0.0,
        )

        assert action == PlannerAction.HOLD
        assert new_soc == 100.0
        assert cost == 0.0

    def test_simulate_period_no_decisions(self):
        """Test simulation with no decisions."""
        simulator = TOUBaselineSimulator()
        result = simulator.simulate_period([], [], [], [])
        assert result is None

    def test_simulate_period_with_decisions(self):
        """Test simulation with mock decisions."""
        simulator = TOUBaselineSimulator()

        now = dt_util.now()
        decisions = [
            MockDecision(
                timestamp=now + timedelta(hours=i),
                hour_of_day=i,
                mode_chosen=PlannerAction.HOLD,
                actual_cost_during_period=0.5,
                actual_soc_change=0.0,
            )
            for i in range(4)
        ]

        price_data = [{"hour": i, "price": 0.15} for i in range(4)]

        result = simulator.simulate_period(decisions, price_data, [], [])

        assert isinstance(result, CounterfactualResult)
        assert result.periods_simulated == 4
        assert result.total_cost_actual > 0


class TestCounterfactualResult:
    """Test CounterfactualResult dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        result = CounterfactualResult(
            period_start=datetime(2026, 3, 12, 0, 0),
            period_end=datetime(2026, 3, 12, 23, 59),
            total_cost_tou=8.5,
            total_cost_actual=7.0,
            optimizer_advantage=1.5,
            advantage_percent=17.6,
            periods_simulated=48,
        )

        d = result.to_dict()
        assert d["total_cost_tou"] == 8.5
        assert d["total_cost_actual"] == 7.0
        assert d["optimizer_advantage"] == 1.5
        assert d["advantage_percent"] == 17.6
        assert d["periods_simulated"] == 48

    def test_counterfactual_period_to_dict(self):
        """Test CounterfactualPeriod to_dict."""
        period = CounterfactualPeriod(
            start_time=datetime(2026, 3, 12, 0, 0),
            end_time=datetime(2026, 3, 12, 0, 30),
            tou_action=PlannerAction.CHARGE_GRID_NORMAL,
            actual_action=PlannerAction.HOLD,
            price_per_kwh=0.15,
            soc_start=50.0,
            soc_end_tou=55.0,
            soc_end_actual=50.0,
            cost_tou=0.5,
            cost_actual=0.0,
            solar_kwh=0.0,
            consumption_kwh=0.0,
        )

        d = period.to_dict()
        assert d["tou_action"] == "charge_grid_normal"
        assert d["actual_action"] == "hold"
        assert d["price_per_kwh"] == 0.15


class TestCounterfactualEvaluator:
    """Test CounterfactualEvaluator."""

    def test_init(self):
        """Test evaluator initialization."""
        evaluator = CounterfactualEvaluator()
        assert evaluator._daily_results == []
        assert evaluator._last_evaluation is None

    @patch("custom_components.localshift.engine.counterfactual.dt_util")
    def test_evaluate_daily_no_decisions(self, mock_dt_util):
        """Test evaluation with no decisions."""
        mock_dt_util.now.return_value = datetime(2026, 3, 12, 12, 0)
        evaluator = CounterfactualEvaluator()

        data = MagicMock()
        result = evaluator.evaluate_daily([], data)
        assert result is None

    @patch("custom_components.localshift.engine.counterfactual.dt_util")
    def test_evaluate_daily_with_decisions(self, mock_dt_util):
        """Test evaluation with mock decisions."""
        mock_dt_util.now.return_value = datetime(2026, 3, 12, 12, 0)

        now = datetime(2026, 3, 12, 0, 0)
        decisions = [
            MockDecision(
                timestamp=now + timedelta(hours=i),
                hour_of_day=i,
                mode_chosen=PlannerAction.HOLD,
                actual_cost_during_period=0.5,
            )
            for i in range(4)
        ]

        data = MagicMock()
        data.general_forecast = [{"hour": i, "price": 0.15} for i in range(4)]
        data.solcast_today = [{"hour": i, "kwh": 1.0} for i in range(4)]

        evaluator = CounterfactualEvaluator()
        result = evaluator.evaluate_daily(decisions, data)

        assert result is not None
        assert result.periods_simulated == 4

    def test_get_rolling_advantage_empty(self):
        """Test rolling advantage with no data."""
        evaluator = CounterfactualEvaluator()
        metrics = evaluator.get_rolling_advantage(7)

        assert metrics["advantage_total"] == 0.0
        assert metrics["advantage_daily_avg"] == 0.0
        assert metrics["days_with_data"] == 0

    @patch("custom_components.localshift.engine.counterfactual.dt_util")
    def test_get_rolling_advantage_with_data(self, mock_dt_util):
        """Test rolling advantage with stored results."""
        mock_dt_util.now.return_value = datetime(2026, 3, 12, 12, 0)

        evaluator = CounterfactualEvaluator()
        evaluator._daily_results = [
            CounterfactualResult(
                period_start=datetime(2026, 3, 11, 0, 0),
                period_end=datetime(2026, 3, 11, 23, 59),
                total_cost_tou=10.0,
                total_cost_actual=8.0,
                optimizer_advantage=2.0,
                advantage_percent=20.0,
                periods_simulated=48,
            ),
            CounterfactualResult(
                period_start=datetime(2026, 3, 12, 0, 0),
                period_end=datetime(2026, 3, 12, 11, 59),
                total_cost_tou=9.0,
                total_cost_actual=7.5,
                optimizer_advantage=1.5,
                advantage_percent=16.7,
                periods_simulated=24,
            ),
        ]

        metrics = evaluator.get_rolling_advantage(7)
        assert metrics["advantage_total"] == 3.5
        assert metrics["advantage_daily_avg"] == 1.75
        assert metrics["days_with_data"] == 2

    def test_is_degrading_positive(self):
        """Test degradation detection with positive advantage."""
        evaluator = CounterfactualEvaluator()
        evaluator._daily_results = [
            CounterfactualResult(
                period_start=datetime(2026, 3, 11, 0, 0),
                period_end=datetime(2026, 3, 11, 23, 59),
                total_cost_tou=10.0,
                total_cost_actual=8.0,
                optimizer_advantage=2.0,
                advantage_percent=20.0,
                periods_simulated=48,
            ),
        ]

        assert evaluator.is_degrading() is False

    def test_is_degrading_negative(self):
        """Test degradation detection with negative advantage."""
        evaluator = CounterfactualEvaluator()
        evaluator._daily_results = [
            CounterfactualResult(
                period_start=datetime(2026, 3, 11, 0, 0),
                period_end=datetime(2026, 3, 11, 23, 59),
                total_cost_tou=8.0,
                total_cost_actual=10.0,
                optimizer_advantage=-2.0,
                advantage_percent=-25.0,
                periods_simulated=48,
            ),
        ]

        assert evaluator.is_degrading() is True

    def test_update_performance_metrics(self):
        """Test updating performance metrics."""
        evaluator = CounterfactualEvaluator()
        metrics = PerformanceMetrics()

        result = CounterfactualResult(
            period_start=datetime(2026, 3, 12, 0, 0),
            period_end=datetime(2026, 3, 12, 23, 59),
            total_cost_tou=10.0,
            total_cost_actual=8.0,
            optimizer_advantage=2.0,
            advantage_percent=20.0,
            periods_simulated=48,
        )

        updated = evaluator.update_performance_metrics(metrics, result)

        assert updated.counterfactual_tou_cost == 10.0
        assert updated.counterfactual_actual_cost == 8.0
        assert updated.optimizer_advantage_daily == 2.0
        assert updated.optimizer_advantage_percent == 20.0


class TestCounterfactualScoreIntegrator:
    """Test CounterfactualScoreIntegrator."""

    def test_compute_counterfactual_score_positive(self):
        """Test score computation with positive advantage."""
        integrator = CounterfactualScoreIntegrator()
        score = integrator.compute_counterfactual_score(1.0, expected_savings=1.0)

        assert score > 0.5
        assert 0.0 <= score <= 1.0

    def test_compute_counterfactual_score_negative(self):
        """Test score computation with negative advantage."""
        integrator = CounterfactualScoreIntegrator()
        score = integrator.compute_counterfactual_score(-1.0, expected_savings=1.0)

        assert score < 0.5
        assert 0.0 <= score <= 1.0

    def test_compute_counterfactual_score_clamped(self):
        """Test score is clamped to [0, 1]."""
        integrator = CounterfactualScoreIntegrator()
        score = integrator.compute_counterfactual_score(10.0, expected_savings=1.0)

        assert score == 1.0

    def test_blend_with_decision_score(self):
        """Test blending scores."""
        integrator = CounterfactualScoreIntegrator()
        blended = integrator.blend_with_decision_score(
            base_score=0.7,
            counterfactual_score=0.8,
            counterfactual_weight=0.3,
        )

        expected = 0.7 * 0.7 + 0.8 * 0.3
        assert abs(blended - expected) < 0.001

    def test_blend_with_decision_score_clamped_weight(self):
        """Test weight is clamped to [0, 1]."""
        integrator = CounterfactualScoreIntegrator()
        blended = integrator.blend_with_decision_score(
            base_score=0.7,
            counterfactual_score=0.8,
            counterfactual_weight=1.5,
        )

        assert blended == 0.8

    @patch.object(CounterfactualEvaluator, "evaluate_daily")
    def test_get_counterfactual_component_no_result(self, mock_evaluate):
        """Test component retrieval with no evaluation result."""
        mock_evaluate.return_value = None

        integrator = CounterfactualScoreIntegrator()
        component = integrator.get_counterfactual_component([], MagicMock())

        assert component["available"] is False
        assert component["score"] == 0.5
        assert component["advantage"] == 0.0

    @patch.object(CounterfactualEvaluator, "evaluate_daily")
    def test_get_counterfactual_component_with_result(self, mock_evaluate):
        """Test component retrieval with evaluation result."""
        mock_evaluate.return_value = CounterfactualResult(
            period_start=datetime(2026, 3, 12, 0, 0),
            period_end=datetime(2026, 3, 12, 23, 59),
            total_cost_tou=10.0,
            total_cost_actual=8.0,
            optimizer_advantage=2.0,
            advantage_percent=20.0,
            periods_simulated=48,
        )

        integrator = CounterfactualScoreIntegrator()
        component = integrator.get_counterfactual_component([], MagicMock())

        assert component["available"] is True
        assert component["advantage"] == 2.0
        assert component["tou_cost"] == 10.0
        assert component["actual_cost"] == 8.0


class TestCounterfactualIntegration:
    """Integration tests for counterfactual system."""

    @patch("custom_components.localshift.engine.counterfactual.dt_util")
    def test_full_day_simulation(self, mock_dt_util):
        """Test full day simulation with various conditions."""
        mock_dt_util.now.return_value = datetime(2026, 3, 12, 23, 0)

        now = datetime(2026, 3, 12, 0, 0)
        decisions = []

        for hour in range(24):
            actual_cost = 0.0
            if hour in range(2, 6):
                actual_cost = 0.3
            elif hour == 18:
                actual_cost = -0.5

            decisions.append(
                MockDecision(
                    timestamp=now + timedelta(hours=hour),
                    hour_of_day=hour,
                    mode_chosen=PlannerAction.HOLD,
                    actual_cost_during_period=actual_cost,
                )
            )

        price_forecast = [
            {"hour": h, "price": 0.3 if 17 <= h <= 21 else 0.1} for h in range(24)
        ]

        solar_forecast = [
            {"hour": h, "kwh": 2.0 if 10 <= h <= 16 else 0.0} for h in range(24)
        ]

        data = MagicMock()
        data.general_forecast = price_forecast
        data.solcast_today = solar_forecast

        evaluator = CounterfactualEvaluator()
        result = evaluator.evaluate_daily(decisions, data)

        assert result is not None
        assert result.periods_simulated == 24
        assert result.total_cost_actual != 0

        metrics = evaluator.get_rolling_advantage(7)
        assert "advantage_total" in metrics
        assert "advantage_daily_avg" in metrics

    def test_performance_metrics_serialization(self):
        """Test that counterfactual fields serialize correctly."""
        metrics = PerformanceMetrics()
        metrics.counterfactual_tou_cost = 10.0
        metrics.counterfactual_actual_cost = 8.0
        metrics.optimizer_advantage_daily = 2.0
        metrics.optimizer_advantage_7d = 14.0
        metrics.optimizer_advantage_percent = 20.0
        metrics.counterfactual_degrading = False

        d = metrics.to_dict()
        restored = PerformanceMetrics.from_dict(d)

        assert restored.counterfactual_tou_cost == 10.0
        assert restored.counterfactual_actual_cost == 8.0
        assert restored.optimizer_advantage_daily == 2.0
        assert restored.counterfactual_degrading is False
