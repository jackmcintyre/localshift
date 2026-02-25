"""Unit tests for CostTracker."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.coordinator_data import CoordinatorData
from custom_components.localshift.cost_tracker import CostTracker, ReconciliationReport


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    return hass


@pytest.fixture
def cost_tracker(mock_hass):
    """Create a CostTracker instance."""
    return CostTracker(mock_hass)


@pytest.fixture
def coordinator_data():
    """Create CoordinatorData with power and price values."""
    data = CoordinatorData()
    data.grid_power_kw = 2.5  # Importing 2.5kW
    data.battery_power_kw = -1.0  # Discharging 1kW
    data.general_price = 0.30  # $0.30/kWh buy price
    data.feed_in_price = 0.08  # $0.08/kWh sell price
    data.grid_import_cost = 0.0
    data.grid_export_revenue = 0.0
    data.battery_savings = 0.0
    data.battery_charge_cost = 0.0
    data.target_reached_today = False
    return data


# =============================================================================
# ACCUMULATE_COSTS TESTS
# =============================================================================


class TestAccumulateCosts:
    """Tests for accumulate_costs method."""

    def test_accumulate_grid_import_cost(self, cost_tracker, coordinator_data):
        """Test grid import cost accumulation."""
        coordinator_data.grid_power_kw = 2.5  # 2.5kW import
        coordinator_data.general_price = 0.30  # $0.30/kWh
        coordinator_data.battery_power_kw = 0.0

        cost_tracker.accumulate_costs(coordinator_data)

        # Cost = power_kW × price_$/kWh / 60 = 2.5 × 0.30 / 60 = 0.0125
        expected_cost = 2.5 * 0.30 / 60
        assert coordinator_data.grid_import_cost == pytest.approx(expected_cost)

    def test_accumulate_grid_export_revenue(self, cost_tracker, coordinator_data):
        """Test grid export revenue accumulation."""
        coordinator_data.grid_power_kw = -2.0  # 2kW export (negative)
        coordinator_data.feed_in_price = 0.08  # $0.08/kWh
        coordinator_data.battery_power_kw = 0.0

        cost_tracker.accumulate_costs(coordinator_data)

        # Revenue = -grid_power × feed_in_price / 60 = 2.0 × 0.08 / 60
        expected_revenue = 2.0 * 0.08 / 60
        assert coordinator_data.grid_export_revenue == pytest.approx(expected_revenue)

    def test_accumulate_battery_savings_discharge(self, cost_tracker, coordinator_data):
        """Test battery savings when discharging (avoided purchase)."""
        coordinator_data.grid_power_kw = 0.0
        coordinator_data.battery_power_kw = -1.5  # Discharging 1.5kW (negative)
        coordinator_data.general_price = 0.30

        cost_tracker.accumulate_costs(coordinator_data)

        # Savings = -battery_power × buy_price / 60 = 1.5 × 0.30 / 60
        expected_savings = 1.5 * 0.30 / 60
        assert coordinator_data.battery_savings == pytest.approx(expected_savings)

    def test_accumulate_battery_charge_cost(self, cost_tracker, coordinator_data):
        """Test battery charge cost accumulation."""
        coordinator_data.grid_power_kw = 0.0
        coordinator_data.battery_power_kw = 3.3  # Charging at 3.3kW (positive)
        coordinator_data.general_price = 0.15

        cost_tracker.accumulate_costs(coordinator_data)

        # Charge cost = battery_power × buy_price / 60 = 3.3 × 0.15 / 60
        expected_cost = 3.3 * 0.15 / 60
        assert coordinator_data.battery_charge_cost == pytest.approx(expected_cost)

    def test_accumulate_all_together(self, cost_tracker, coordinator_data):
        """Test accumulation with all power flows simultaneously."""
        coordinator_data.grid_power_kw = 1.0  # 1kW import
        coordinator_data.battery_power_kw = 2.0  # 2kW charging
        coordinator_data.general_price = 0.25
        coordinator_data.feed_in_price = 0.10

        cost_tracker.accumulate_costs(coordinator_data)

        # Import cost: 1.0 × 0.25 / 60
        expected_import = 1.0 * 0.25 / 60
        assert coordinator_data.grid_import_cost == pytest.approx(expected_import)

        # No export (grid_power is positive)
        assert coordinator_data.grid_export_revenue == 0.0

        # No savings (battery is charging, not discharging)
        assert coordinator_data.battery_savings == 0.0

        # Charge cost: 2.0 × 0.25 / 60
        expected_charge = 2.0 * 0.25 / 60
        assert coordinator_data.battery_charge_cost == pytest.approx(expected_charge)

    def test_accumulate_multiple_calls(self, cost_tracker, coordinator_data):
        """Test that multiple calls accumulate values."""
        coordinator_data.grid_power_kw = 1.0
        coordinator_data.general_price = 0.30
        coordinator_data.battery_power_kw = 0.0

        # First call
        cost_tracker.accumulate_costs(coordinator_data)
        first_cost = coordinator_data.grid_import_cost

        # Second call
        cost_tracker.accumulate_costs(coordinator_data)
        second_cost = coordinator_data.grid_import_cost

        # Should have doubled
        assert second_cost == pytest.approx(first_cost * 2)

    def test_accumulate_zero_power(self, cost_tracker, coordinator_data):
        """Test accumulation with zero power."""
        coordinator_data.grid_power_kw = 0.0
        coordinator_data.battery_power_kw = 0.0

        cost_tracker.accumulate_costs(coordinator_data)

        assert coordinator_data.grid_import_cost == 0.0
        assert coordinator_data.grid_export_revenue == 0.0
        assert coordinator_data.battery_savings == 0.0
        assert coordinator_data.battery_charge_cost == 0.0

    def test_accumulate_negative_grid_zero_export(self, cost_tracker, coordinator_data):
        """Test that negative grid power (export) doesn't add import cost."""
        coordinator_data.grid_power_kw = -5.0  # Exporting
        coordinator_data.general_price = 0.30
        coordinator_data.battery_power_kw = 0.0

        cost_tracker.accumulate_costs(coordinator_data)

        # Import cost should be 0 (max of negative value and 0)
        assert coordinator_data.grid_import_cost == 0.0
        # Export revenue should be calculated
        assert coordinator_data.grid_export_revenue > 0

    def test_accumulate_negative_battery_zero_charge(
        self, cost_tracker, coordinator_data
    ):
        """Test that negative battery power (discharge) doesn't add charge cost."""
        coordinator_data.battery_power_kw = -3.0  # Discharging
        coordinator_data.general_price = 0.30
        coordinator_data.grid_power_kw = 0.0

        cost_tracker.accumulate_costs(coordinator_data)

        # Charge cost should be 0 (max of negative value and 0)
        assert coordinator_data.battery_charge_cost == 0.0
        # Savings should be calculated
        assert coordinator_data.battery_savings > 0


# =============================================================================
# RESET_DAILY_ACCUMULATORS TESTS
# =============================================================================


class TestResetDailyAccumulators:
    """Tests for reset_daily_accumulators method."""

    def test_reset_clears_all_accumulators(self, cost_tracker, coordinator_data):
        """Test that reset clears all cost accumulators."""
        # Set some values
        coordinator_data.grid_import_cost = 10.0
        coordinator_data.grid_export_revenue = 5.0
        coordinator_data.battery_savings = 3.0
        coordinator_data.battery_charge_cost = 2.0
        coordinator_data.target_reached_today = True

        cost_tracker.reset_daily_accumulators(coordinator_data)

        assert coordinator_data.grid_import_cost == 0.0
        assert coordinator_data.grid_export_revenue == 0.0
        assert coordinator_data.battery_savings == 0.0
        assert coordinator_data.battery_charge_cost == 0.0
        assert coordinator_data.target_reached_today is False

    def test_reset_already_zero(self, cost_tracker, coordinator_data):
        """Test reset when values are already zero."""
        coordinator_data.grid_import_cost = 0.0
        coordinator_data.grid_export_revenue = 0.0
        coordinator_data.battery_savings = 0.0
        coordinator_data.battery_charge_cost = 0.0
        coordinator_data.target_reached_today = False

        cost_tracker.reset_daily_accumulators(coordinator_data)

        # Should remain zero
        assert coordinator_data.grid_import_cost == 0.0
        assert coordinator_data.grid_export_revenue == 0.0
        assert coordinator_data.battery_savings == 0.0
        assert coordinator_data.battery_charge_cost == 0.0
        assert coordinator_data.target_reached_today is False

    def test_reset_preserves_other_data(self, cost_tracker, coordinator_data):
        """Test that reset doesn't affect other CoordinatorData fields."""
        coordinator_data.soc = 75.0
        coordinator_data.operation_mode = "autonomous"
        coordinator_data.grid_import_cost = 10.0

        cost_tracker.reset_daily_accumulators(coordinator_data)

        # Other fields should be preserved
        assert coordinator_data.soc == 75.0
        assert coordinator_data.operation_mode == "autonomous"
        # Cost should be reset
        assert coordinator_data.grid_import_cost == 0.0


# =============================================================================
# RECONCILIATION REPORT TESTS (Issue #269)
# =============================================================================


class TestReconciliationReport:
    """Tests for ReconciliationReport dataclass."""

    def test_default_values(self):
        """Test default values are set correctly."""
        report = ReconciliationReport()
        assert report.estimated_cost == 0.0
        assert report.actual_cost == 0.0
        assert report.variance_pct == 0.0
        assert report.is_significant is False
        assert report.period_start is None
        assert report.period_end is None
        assert report.last_run is None
        assert report.errors == []

    def test_to_dict(self):
        """Test serialization to dictionary."""
        now = datetime(2026, 2, 26, 8, 0, 0)
        report = ReconciliationReport(
            estimated_cost=10.50,
            actual_cost=11.00,
            variance_pct=4.76,
            is_significant=False,
            last_run=now,
        )
        result = report.to_dict()

        assert result["estimated_cost"] == 10.50
        assert result["actual_cost"] == 11.00
        assert result["variance_pct"] == 4.76
        assert result["is_significant"] is False
        assert result["last_run"] == "2026-02-26T08:00:00"

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "estimated_cost": 15.0,
            "actual_cost": 14.0,
            "variance_pct": 7.14,
            "is_significant": False,
            "last_run": "2026-02-26T08:00:00",
            "errors": [],
        }
        report = ReconciliationReport.from_dict(data)

        assert report.estimated_cost == 15.0
        assert report.actual_cost == 14.0
        assert report.variance_pct == 7.14
        assert report.is_significant is False
        assert report.last_run == datetime(2026, 2, 26, 8, 0, 0)


# =============================================================================
# COST RECONCILIATION TESTS (Issue #269)
# =============================================================================


class TestCostReconciliation:
    """Tests for cost reconciliation against metered statistics."""

    @pytest.mark.asyncio
    async def test_reconcile_with_statistics_no_entity(self, cost_tracker):
        """Test reconciliation when no entity is configured."""
        report = await cost_tracker.async_reconcile_with_statistics(
            estimated_cost=10.0,
            grid_import_entity=None,
            period_start=datetime.now() - timedelta(days=1),
            period_end=datetime.now(),
        )

        assert report is not None
        assert "No grid import entity configured" in report.errors

    @pytest.mark.asyncio
    async def test_calculate_variance_pct(self, cost_tracker):
        """Test variance percentage calculation."""
        # 10% variance
        variance = cost_tracker._calculate_variance_pct(10.0, 11.0)
        assert variance == pytest.approx(-10.0, rel=0.1)

        # 20% variance
        variance = cost_tracker._calculate_variance_pct(10.0, 12.0)
        assert variance == pytest.approx(-20.0, rel=0.1)

        # No variance
        variance = cost_tracker._calculate_variance_pct(10.0, 10.0)
        assert variance == 0.0

    @pytest.mark.asyncio
    async def test_calculate_variance_pct_zero_actual(self, cost_tracker):
        """Test variance calculation when actual is zero."""
        # Should return 0 to avoid division by zero
        variance = cost_tracker._calculate_variance_pct(10.0, 0.0)
        assert variance == 0.0
