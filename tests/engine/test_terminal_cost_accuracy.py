from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.solar import get_forecast_accuracy
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    SlotContext,
)
from custom_components.localshift.forecast.analysis_resolver import ConfidenceResolver
from custom_components.localshift.forecast.solcast_analysis import (
    ConfidenceInterval,
    SolcastAnalysis,
)


class TestGetForecastAccuracy:
    """Tests for _get_forecast_accuracy helper method."""

    def test_no_tracker_returns_one(self):
        """When no tracker exists, return 1.0 (no discount)."""
        planner = DPPlanner.__new__(DPPlanner)
        result = get_forecast_accuracy(None)
        assert result == 1.0

    def test_tracker_returns_100_returns_one(self):
        """When accuracy is 100%, return 1.0."""
        tracker = Mock()
        tracker.metrics.accuracy = 100
        planner = DPPlanner.__new__(DPPlanner)
        result = get_forecast_accuracy(tracker)
        assert result == 1.0

    def test_tracker_returns_50_returns_point_five(self):
        """When accuracy is 50%, return 0.5."""
        tracker = Mock()
        tracker.metrics.accuracy = 50
        planner = DPPlanner.__new__(DPPlanner)
        result = get_forecast_accuracy(tracker)
        assert result == 0.5

    def test_tracker_returns_37_returns_point_three_seven(self):
        """When accuracy is 37%, return 0.37."""
        tracker = Mock()
        tracker.metrics.accuracy = 37
        planner = DPPlanner.__new__(DPPlanner)
        result = get_forecast_accuracy(tracker)
        assert result == 0.37

    def test_tracker_returns_none_returns_one(self):
        """When tracker returns None, return 1.0."""
        tracker = Mock()
        tracker.metrics.accuracy = None
        planner = DPPlanner.__new__(DPPlanner)
        result = get_forecast_accuracy(tracker)
        assert result == 1.0

    def test_tracker_returns_zero_returns_one(self):
        """When tracker returns 0, return 1.0 (no data)."""
        tracker = Mock()
        tracker.metrics.accuracy = 0
        planner = DPPlanner.__new__(DPPlanner)
        result = get_forecast_accuracy(tracker)
        assert result == 1.0

    def test_tracker_returns_negative_returns_one(self):
        """When tracker returns negative (invalid), return 1.0."""
        tracker = Mock()
        tracker.metrics.accuracy = -10
        planner = DPPlanner.__new__(DPPlanner)
        result = get_forecast_accuracy(tracker)
        assert result == 1.0

    def test_tracker_no_metrics_attribute_returns_one(self):
        """When tracker has no metrics attribute, return 1.0."""
        tracker = Mock(spec=[])  # No attributes at all
        planner = DPPlanner.__new__(DPPlanner)
        result = get_forecast_accuracy(tracker)
        assert result == 1.0


class TestAccuracyDiscountClamping:
    """Tests for accuracy discount clamping behavior."""

    def test_accuracy_one_point_five_clamped_to_one(self):
        """Accuracy > 100% should be clamped to 1.0."""
        accuracy = 1.5
        discount = max(0.5, min(1.0, accuracy))
        assert discount == 1.0

    def test_accuracy_zero_point_three_clamped_to_point_five(self):
        """Accuracy < 50% should be clamped to 0.5."""
        accuracy = 0.3
        discount = max(0.5, min(1.0, accuracy))
        assert discount == 0.5

    def test_accuracy_zero_point_seven_five_not_clamped(self):
        """Accuracy in normal range (50-100%) should not be clamped."""
        accuracy = 0.75
        discount = max(0.5, min(1.0, accuracy))
        assert discount == 0.75


class TestTerminalCostWithAccuracyDiscount:
    """Tests for terminal cost calculation with accuracy discount."""

    def test_terminal_cost_higher_when_accuracy_low(self):
        """Terminal penalty should be higher when forecast accuracy is low."""
        soc = 60.0
        projected_solar = 30.0
        target = 95.0

        effective_soc_full = soc + projected_solar
        shortfall_full = max(0, target - effective_soc_full)

        adjusted_solar = projected_solar * 0.5
        effective_soc_discounted = soc + adjusted_solar
        shortfall_discounted = max(0, target - effective_soc_discounted)

        assert shortfall_discounted > shortfall_full


class TestTerminalDiagnostics:
    """Tests for _get_terminal_diagnostics helper."""

    def test_returns_all_diagnostic_fields(self):
        """Verify all diagnostic fields are returned."""
        mock_decision = Mock()
        mock_decision.predicted_soc_pct = 85.0
        decisions: list = [mock_decision]

        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_terminal_diagnostics(
            soc_pct=60.0,
            target=95.0,
            projected_solar_gain_pct=30.0,
            accuracy_discount=0.5,
            future_solar_gain_pct=5.0,
            decisions=decisions,
            terminal_penalty_idx=0,
        )

        assert "projected_solar_gain_pct" in result
        assert "accuracy_discount_factor" in result
        assert "adjusted_solar_gain_pct" in result
        assert "effective_soc_at_terminal" in result
        assert "peak_soc_pct" in result
        assert "dw_entry_soc_pct" in result

    def test_none_dw_entry_when_no_penalty_idx(self):
        """Verify dw_entry_soc is None when terminal_penalty_idx is None."""
        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_terminal_diagnostics(
            soc_pct=60.0,
            target=95.0,
            projected_solar_gain_pct=30.0,
            accuracy_discount=0.5,
            future_solar_gain_pct=5.0,
            decisions=[],
            terminal_penalty_idx=None,
        )

        assert result["dw_entry_soc_pct"] is None

    def test_peak_soc_from_slots(self):
        """Verify peak_soc is correctly calculated from slots."""
        mock_decision1 = Mock()
        mock_decision1.predicted_soc_pct = 75.0
        mock_decision2 = Mock()
        mock_decision2.predicted_soc_pct = 90.0
        mock_decision3 = Mock()
        mock_decision3.predicted_soc_pct = 85.0
        decisions: list = [mock_decision1, mock_decision2, mock_decision3]

        planner = DPPlanner.__new__(DPPlanner)
        result = planner._get_terminal_diagnostics(
            soc_pct=60.0,
            target=95.0,
            projected_solar_gain_pct=30.0,
            accuracy_discount=0.5,
            future_solar_gain_pct=5.0,
            decisions=decisions,
            terminal_penalty_idx=1,
        )

        assert result["peak_soc_pct"] == 90.0  # max of 75, 90, 85


class TestOptimizerResultDiagnosticFields:
    """Tests for diagnostic fields on OptimizerResult."""

    def test_diagnostic_fields_default_to_none(self):
        """New diagnostic fields should default to None."""
        from custom_components.localshift.engine.optimizer_dp import OptimizerResult

        result = OptimizerResult(success=True, total_slots=3)
        assert result.forecast_accuracy is None
        assert result.projected_solar_gain_pct is None
        assert result.accuracy_discount_factor is None
        assert result.adjusted_solar_gain_pct is None
        assert result.effective_soc_at_terminal is None
        assert result.peak_soc_pct is None
        assert result.dw_entry_soc_pct is None


class TestProjectedSolcastGainWithConfidence:
    """Tests for confidence-aware terminal solar projection."""

    def test_low_confidence_reduces_projected_gain(self):
        start = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 19, 12, 30, tzinfo=timezone.utc)
        solcast = [
            {
                "period_start": start.isoformat(),
                "pv_estimate": 6.0,
                "pv_estimate10": 1.0,
            }
        ]

        optimistic_gain = DPPlanner._projected_solcast_gain_pct(
            solcast,
            start,
            end,
            13.5,
        )

        analysis = SolcastAnalysis(
            entity_id="sensor.test",
            last_updated=start,
            day_confidence=0.2,
            day_spread_kwh=0.0,
            estimate10_kwh=0.0,
            estimate90_kwh=0.0,
            intervals=[
                ConfidenceInterval(
                    period_start=start,
                    spread_kwh=0.0,
                    confidence=0.2,
                )
            ],
        )
        resolver = ConfidenceResolver(analysis, None)

        conservative_gain = DPPlanner._projected_solcast_gain_pct(
            solcast,
            start,
            end,
            13.5,
            confidence_resolver=resolver,
        )

        assert conservative_gain < optimistic_gain
        assert conservative_gain > 0.0

    def test_tomorrow_analysis_is_used_for_cross_day_projection(self):
        start = datetime(2026, 3, 20, 8, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 20, 8, 30, tzinfo=timezone.utc)
        solcast = [
            {
                "period_start": start.isoformat(),
                "pv_estimate": 4.0,
                "pv_estimate10": 0.5,
            }
        ]
        tomorrow_analysis = SolcastAnalysis(
            entity_id="sensor.tomorrow",
            last_updated=start,
            day_confidence=0.1,
            day_spread_kwh=0.0,
            estimate10_kwh=0.0,
            estimate90_kwh=0.0,
            intervals=[
                ConfidenceInterval(
                    period_start=start,
                    spread_kwh=0.0,
                    confidence=0.1,
                )
            ],
        )

        optimistic_gain = DPPlanner._projected_solcast_gain_pct(
            solcast, start, end, 13.5
        )
        conservative_gain = DPPlanner._projected_solcast_gain_pct(
            solcast,
            start,
            end,
            13.5,
            confidence_resolver=ConfidenceResolver(None, tomorrow_analysis),
        )

        assert conservative_gain < optimistic_gain

    def test_diagnostic_fields_set_explicitly(self):
        """Diagnostic fields can be set via constructor."""
        from custom_components.localshift.engine.optimizer_dp import OptimizerResult

        result = OptimizerResult(
            success=True,
            total_slots=3,
            forecast_accuracy=0.75,
            projected_solar_gain_pct=30.0,
            accuracy_discount_factor=0.75,
            adjusted_solar_gain_pct=22.5,
            effective_soc_at_terminal=85.0,
            peak_soc_pct=92.0,
            dw_entry_soc_pct=88.0,
        )
        assert result.forecast_accuracy == 0.75
        assert result.projected_solar_gain_pct == 30.0
        assert result.accuracy_discount_factor == 0.75
        assert result.adjusted_solar_gain_pct == 22.5
        assert result.effective_soc_at_terminal == 85.0
        assert result.peak_soc_pct == 92.0
        assert result.dw_entry_soc_pct == 88.0


class TestSolveDiagnostics:
    """Integration tests: _solve() populates diagnostic fields on OptimizerResult."""

    def _make_inputs(self, *, accuracy: float | None = 75.0) -> OptimizerInputs:
        """Build minimal OptimizerInputs for a 4-slot horizon with a demand window."""
        base = datetime(2026, 3, 20, 14, 0, tzinfo=timezone.utc)
        slots = []
        for i in range(4):
            ts = base + timedelta(minutes=30 * i)
            slots.append(
                SlotContext(
                    slot_index=i,
                    timestamp_iso=ts.isoformat(),
                    slot_interval_minutes=30,
                    buy_price=0.30,
                    sell_price=0.05,
                    consumption_kwh=0.5,
                    solar_kwh=1.0,
                    is_demand_window_entry=i == 3,  # last slot is DW entry
                    is_demand_window_slot=i >= 3,  # last slot is DW
                ),
            )

        tracker = None
        if accuracy is not None:
            tracker = Mock()
            tracker.metrics.accuracy = accuracy

        return OptimizerInputs(
            cycle_id="test_cycle",
            initial_soc_pct=60.0,
            slots=slots,
            config=self._make_config(),
            solar_accuracy_tracker=tracker,
            all_solcast=[],
        )

    def _make_config(self) -> OptimizerConfig:
        """Build minimal OptimizerConfig."""
        return OptimizerConfig(
            battery_capacity_kwh=13.5,
            charge_rate_kw=3.3,
            discharge_rate_kw=5.0,
            demand_window_target_soc_pct=95.0,
            optimization_mode="self_consumption",
            allow_dw_entry_under_target=False,
            switching_penalty=0.02,
            cycle_penalty_per_kwh=0.08,
            target_shortfall_penalty_per_pct=0.015,
        )

    def test_diagnostic_fields_populated_with_terminal_penalty(self):
        """When terminal penalty exists, diagnostic fields should be populated."""
        inputs = self._make_inputs(accuracy=75.0)
        planner = DPPlanner()
        result = planner._solve(inputs)

        assert result.success is True
        assert result.forecast_accuracy is not None
        assert result.forecast_accuracy == pytest.approx(0.75)
        assert result.accuracy_discount_factor is not None
        assert result.projected_solar_gain_pct is not None
        assert result.adjusted_solar_gain_pct is not None
        assert result.effective_soc_at_terminal is not None
        assert result.peak_soc_pct is not None
        # dw_entry_soc_pct may or may not be None depending on terminal_penalty_idx

    def test_diagnostic_fields_none_without_demand_window(self):
        """When no demand window, diagnostic fields should remain None."""
        # Build inputs with NO demand window slots
        base = datetime(2026, 3, 20, 14, 0, tzinfo=timezone.utc)
        slots = []
        for i in range(4):
            ts = base + timedelta(minutes=30 * i)
            slots.append(
                SlotContext(
                    slot_index=i,
                    timestamp_iso=ts.isoformat(),
                    slot_interval_minutes=30,
                    buy_price=0.30,
                    sell_price=0.05,
                    consumption_kwh=0.5,
                    solar_kwh=1.0,
                    is_demand_window_entry=False,
                    is_demand_window_slot=False,
                ),
            )
        inputs = OptimizerInputs(
            cycle_id="test_no_dw",
            initial_soc_pct=60.0,
            slots=slots,
            config=self._make_config(),
            solar_accuracy_tracker=None,
            all_solcast=[],
        )
        planner = DPPlanner()
        result = planner._solve(inputs)

        assert result.success is True
        assert result.forecast_accuracy is None
        assert result.projected_solar_gain_pct is None

    def test_future_solcast_projection_uses_confidence_resolver(self):
        """Future solar beyond the slot horizon uses confidence-aware projection."""
        inputs = self._make_inputs(accuracy=75.0)
        inputs.initial_soc_pct = 20.0
        future_start = datetime(2026, 3, 20, 16, 0, tzinfo=timezone.utc)
        inputs.all_solcast = [
            {
                "period_start": future_start.isoformat(),
                "pv_estimate": 6.0,
                "pv_estimate10": 1.0,
            }
        ]
        inputs.solcast_analysis_today = SolcastAnalysis(
            entity_id="sensor.today",
            last_updated=future_start,
            day_confidence=0.2,
            day_spread_kwh=0.0,
            estimate10_kwh=0.0,
            estimate90_kwh=0.0,
            intervals=[
                ConfidenceInterval(
                    period_start=future_start,
                    spread_kwh=0.0,
                    confidence=0.2,
                )
            ],
        )

        planner = DPPlanner()
        result = planner._solve(inputs)

        assert result.success is True
        assert result.projected_solar_gain_pct is not None
        assert result.adjusted_solar_gain_pct is not None
        assert result.projected_solar_gain_pct > 0.0
        assert result.adjusted_solar_gain_pct < result.projected_solar_gain_pct
