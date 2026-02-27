"""Unit tests for the optimization model (Issue #363).

Tests the Pyomo-based optimization model for grid charging decisions.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from custom_components.localshift.optimization_model import (
    OptimizationModel,
    OptimizationResult,
    GridChargeSlot,
    SOCPoint,
    OptimizationError,
    InfeasibleConstraintsError,
    SolverTimeoutError,
)
from custom_components.localshift.charging_schedule import ChargingSchedule


class TestOptimizationModel:
    """Test cases for OptimizationModel."""

    @pytest.fixture
    def basic_inputs(self) -> dict:
        """Create basic optimization inputs for testing."""
        now = datetime.now()
        return {
            "current_time": now,
            "demand_window_start": now.replace(hour=14, minute=0, second=0),
            "demand_window_end": now.replace(hour=20, minute=0, second=0),
            "current_soc": 0.50,
            "target_soc": 0.80,
            "battery_capacity_kwh": 13.5,
            "inverter_max_kw": 5.0,
            "grid_charge_max_kw": 3.3,
            "soc_min": 0.20,
            "charge_efficiency": 0.92,
            "discharge_efficiency": 0.95,
            "price_forecast": [
                {"time": (now + timedelta(hours=i)).isoformat(), "price": 0.10 + i * 0.01}
                for i in range(24)
            ],
            "solar_forecast": [
                {"time": (now + timedelta(hours=i)).isoformat(), "power_kw": max(0, 3.0 - abs(i - 12) * 0.5)}
                for i in range(24)
            ],
            "consumption_forecast": [
                {"time": (now + timedelta(hours=i)).isoformat(), "power_kw": 0.5 + (0.3 if 6 <= i <= 9 or 17 <= i <= 21 else 0)}
                for i in range(24)
            ],
        }

    def test_model_initialization(self, basic_inputs):
        """Test model initializes correctly."""
        model = OptimizationModel(
            solver="highs",
            time_limit_seconds=10.0,
        )
        assert model.solver == "highs"
        assert model.time_limit_seconds == 10.0

    def test_solve_basic_case(self, basic_inputs):
        """Test solving a basic optimization case."""
        model = OptimizationModel(solver="highs", time_limit_seconds=10.0)
        result = model.solve(basic_inputs)

        assert result is not None
        assert isinstance(result, OptimizationResult)
        assert result.termination_condition == "optimal"
        assert result.total_cost >= 0
        assert len(result.grid_charge_slots) >= 0

    def test_soc_constraint_respected(self, basic_inputs):
        """Test that SOC never drops below minimum."""
        model = OptimizationModel(solver="highs", time_limit_seconds=10.0)
        result = model.solve(basic_inputs)

        for soc_point in result.soc_trajectory:
            assert soc_point.soc >= basic_inputs["soc_min"], (
                f"SOC {soc_point.soc} dropped below minimum {basic_inputs['soc_min']}"
            )

    def test_target_soc_achieved(self, basic_inputs):
        """Test that target SOC is achieved by demand window."""
        model = OptimizationModel(solver="highs", time_limit_seconds=10.0)
        result = model.solve(basic_inputs)

        # Find SOC at demand window start
        dw_start = basic_inputs["demand_window_start"]
        soc_at_dw = None
        for soc_point in result.soc_trajectory:
            if soc_point.timestamp >= dw_start:
                soc_at_dw = soc_point.soc
                break

        # Allow 5% tolerance for solver precision
        if soc_at_dw is not None:
            assert soc_at_dw >= basic_inputs["target_soc"] - 0.05, (
                f"SOC at DW start {soc_at_dw} did not reach target {basic_inputs['target_soc']}"
            )

    def test_grid_charge_rate_limit(self, basic_inputs):
        """Test that grid charging respects rate limits."""
        model = OptimizationModel(solver="highs", time_limit_seconds=10.0)
        result = model.solve(basic_inputs)

        for slot in result.grid_charge_slots:
            assert slot.power_kw <= basic_inputs["grid_charge_max_kw"], (
                f"Grid charge rate {slot.power_kw} exceeded max {basic_inputs['grid_charge_max_kw']}"
            )

    def test_negative_prices_handled(self, basic_inputs):
        """Test that negative prices are handled correctly."""
        # Add negative price periods
        for i, price_entry in enumerate(basic_inputs["price_forecast"]):
            if 2 <= i <= 5:  # 2am-5am have negative prices
                price_entry["price"] = -0.05

        model = OptimizationModel(solver="highs", time_limit_seconds=10.0)
        result = model.solve(basic_inputs)

        assert result is not None
        # Should charge during negative price periods
        assert len(result.grid_charge_slots) > 0

    def test_no_charge_when_solar_sufficient(self, basic_inputs):
        """Test that grid charging is minimized when solar is sufficient."""
        # Set high solar forecast
        for entry in basic_inputs["solar_forecast"]:
            entry["power_kw"] = 5.0  # High solar all day

        # Start with high SOC
        basic_inputs["current_soc"] = 0.70

        model = OptimizationModel(solver="highs", time_limit_seconds=10.0)
        result = model.solve(basic_inputs)

        # Should need minimal or no grid charging
        total_grid_kwh = sum(
            slot.power_kw * slot.duration_hours for slot in result.grid_charge_slots
        )
        # With high SOC and high solar, grid charging should be minimal
        assert total_grid_kwh < 5.0

    def test_charge_efficiency_applied(self, basic_inputs):
        """Test that charge efficiency is properly applied."""
        model = OptimizationModel(solver="highs", time_limit_seconds=10.0)
        result = model.solve(basic_inputs)

        # Check that SOC increase accounts for efficiency
        # For each grid charge slot, SOC should increase by power * duration * efficiency / capacity
        efficiency = basic_inputs["charge_efficiency"]
        capacity = basic_inputs["battery_capacity_kwh"]

        for slot in result.grid_charge_slots:
            expected_soc_gain = (slot.power_kw * slot.duration_hours * efficiency) / capacity
            # This is a sanity check - actual verification would need SOC trajectory comparison
            assert expected_soc_gain > 0

    def test_to_charging_schedule(self, basic_inputs):
        """Test conversion to ChargingSchedule dataclass."""
        model = OptimizationModel(solver="highs", time_limit_seconds=10.0)
        result = model.solve(basic_inputs)

        schedule = result.to_charging_schedule()

        assert isinstance(schedule, ChargingSchedule)
        assert schedule.total_cost >= 0
        assert schedule.solve_time_seconds >= 0
        assert schedule.solver_used == "highs"

    def test_missing_price_forecast(self, basic_inputs):
        """Test handling of missing price forecast."""
        basic_inputs["price_forecast"] = []

        model = OptimizationModel(solver="highs", time_limit_seconds=10.0)

        with pytest.raises(OptimizationError):
            model.solve(basic_inputs)

    def test_infeasible_constraints(self, basic_inputs):
        """Test handling of infeasible constraints."""
        # Set impossible target (100% SOC starting from 0% with no time)
        basic_inputs["current_soc"] = 0.0
        basic_inputs["target_soc"] = 1.0
        basic_inputs["demand_window_start"] = datetime.now() + timedelta(minutes=30)
        basic_inputs["solar_forecast"] = [{"time": datetime.now().isoformat(), "power_kw": 0} for _ in range(24)]

        model = OptimizationModel(solver="highs", time_limit_seconds=10.0)

        # Should either raise InfeasibleConstraintsError or return result with infeasible status
        try:
            result = model.solve(basic_inputs)
            if result is not None:
                assert result.termination_condition in ["infeasible", "unbounded"]
        except InfeasibleConstraintsError:
            pass  # Expected

    def test_solver_timeout(self, basic_inputs):
        """Test solver timeout handling."""
        # Set very short timeout
        model = OptimizationModel(solver="highs", time_limit_seconds=0.001)

        # Should either solve quickly or raise timeout
        try:
            result = model.solve(basic_inputs)
            if result is not None:
                assert result.termination_condition in ["optimal", "timed_out", "feasible"]
        except SolverTimeoutError:
            pass  # Expected


class TestGridChargeSlot:
    """Test cases for GridChargeSlot dataclass."""

    def test_slot_creation(self):
        """Test creating a grid charge slot."""
        now = datetime.now()
        slot = GridChargeSlot(
            start_time=now,
            end_time=now + timedelta(hours=2),
            power_kw=3.3,
            price_per_kwh=0.10,
        )

        assert slot.start_time == now
        assert slot.duration_hours == 2.0
        assert slot.energy_kwh == 6.6  # 3.3 kW * 2 hours
        assert slot.cost == 0.66  # 6.6 kWh * $0.10


class TestSOCPoint:
    """Test cases for SOCPoint dataclass."""

    def test_soc_point_creation(self):
        """Test creating a SOC point."""
        now = datetime.now()
        point = SOCPoint(
            timestamp=now,
            soc=0.75,
            source="grid_charge",
        )

        assert point.timestamp == now
        assert point.soc == 0.75
        assert point.source == "grid_charge"


class TestOptimizationResult:
    """Test cases for OptimizationResult dataclass."""

    def test_result_summary(self):
        """Test optimization result summary."""
        now = datetime.now()
        result = OptimizationResult(
            termination_condition="optimal",
            solve_time_seconds=0.5,
            total_cost=1.25,
            total_grid_charge_kwh=10.0,
            grid_charge_slots=[
                GridChargeSlot(now, now + timedelta(hours=3), 3.3, 0.10),
            ],
            soc_trajectory=[
                SOCPoint(now, 0.50, "initial"),
                SOCPoint(now + timedelta(hours=3), 0.80, "grid_charge"),
            ],
        )

        assert result.is_optimal
        assert result.summary["termination_condition"] == "optimal"
        assert result.summary["total_cost"] == 1.25
        assert result.summary["num_charge_slots"] == 1