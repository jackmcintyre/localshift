"""Unit tests for CostTracker."""

from unittest.mock import MagicMock

import pytest

from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.utils.costs import CostTracker


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
    data.battery_power_kw = 1.0  # Discharging 1kW (positive = discharge)
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
        coordinator_data.battery_power_kw = 1.5  # Discharging 1.5kW (positive)
        coordinator_data.general_price = 0.30

        cost_tracker.accumulate_costs(coordinator_data)

        # Savings = battery_power × buy_price / 60 = 1.5 × 0.30 / 60
        expected_savings = 1.5 * 0.30 / 60
        assert coordinator_data.battery_savings == pytest.approx(expected_savings)

    def test_accumulate_battery_charge_cost(self, cost_tracker, coordinator_data):
        """Test battery charge cost accumulation."""
        coordinator_data.grid_power_kw = 0.0
        coordinator_data.battery_power_kw = -3.3  # Charging at 3.3kW (negative)
        coordinator_data.general_price = 0.15

        cost_tracker.accumulate_costs(coordinator_data)

        # Charge cost = -battery_power × buy_price / 60 = 3.3 × 0.15 / 60
        expected_cost = 3.3 * 0.15 / 60
        assert coordinator_data.battery_charge_cost == pytest.approx(expected_cost)

    def test_accumulate_all_together(self, cost_tracker, coordinator_data):
        """Test accumulation with all power flows simultaneously."""
        coordinator_data.grid_power_kw = 1.0  # 1kW import
        coordinator_data.battery_power_kw = -2.0  # 2kW charging (negative)
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

    def test_accumulate_positive_battery_zero_charge(
        self, cost_tracker, coordinator_data
    ):
        """Test that positive battery power (discharge) doesn't add charge cost."""
        coordinator_data.battery_power_kw = 3.0  # Discharging (positive)
        coordinator_data.general_price = 0.30
        coordinator_data.grid_power_kw = 0.0

        cost_tracker.accumulate_costs(coordinator_data)

        # Charge cost should be 0 (max of -positive value and 0)
        assert coordinator_data.battery_charge_cost == 0.0
        # Savings should be calculated
        assert coordinator_data.battery_savings > 0

    def test_tesla_sign_convention_discharge_accrues_savings_not_charge_cost(
        self, cost_tracker, coordinator_data
    ):
        """Pin the Tesla/Teslemetry sign convention against a live snapshot.

        Snapshot (2026-06-11 23:28 +10): Battery Power +0.381 kW exactly covered
        0.381 kW of load with grid and solar at 0 — i.e. positive battery power is
        DISCHARGING. Under the corrected convention this must accrue battery_savings
        (avoided purchase) and leave battery_charge_cost at exactly $0.00. The old
        inverted code did the reverse (Savings permanently $0, Charge Cost accruing
        the value of discharge).
        """
        coordinator_data.grid_power_kw = 0.0
        coordinator_data.battery_power_kw = 0.381  # Discharging to cover load
        coordinator_data.general_price = 0.30

        cost_tracker.accumulate_costs(coordinator_data)

        assert coordinator_data.battery_savings > 0
        assert coordinator_data.battery_charge_cost == 0.0


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
# ISSUE #868: DAILY ENERGY (kWh) ACCUMULATION
# =============================================================================


class TestAccumulateEnergyKwh:
    """Per-minute kWh accumulation for the #868 performance metrics."""

    def _make_data(self, **overrides):
        data = CoordinatorData()
        data.grid_power_kw = 0.0
        data.battery_power_kw = 0.0
        data.general_price = 0.30
        data.feed_in_price = 0.08
        data.soc = 50.0
        for key, val in overrides.items():
            setattr(data, key, val)
        return data

    def test_charging_from_grid_accumulates_grid_to_battery(self, cost_tracker):
        """Importing + charging accumulates grid-to-battery kWh = min(charge, import)/60."""
        # grid_power_kw=3.0 import, battery_power_kw=-2.0 charging.
        data = self._make_data(grid_power_kw=3.0, battery_power_kw=-2.0, soc=50.0)
        cost_tracker.accumulate_costs(data)

        # First sample seeds SOC baseline (no gain yet).
        assert data.grid_import_kwh_today == pytest.approx(3.0 / 60)
        assert data.grid_to_battery_kwh_today == pytest.approx(2.0 / 60)
        assert data.soc_gain_during_grid_charge_kwh_today == 0.0
        # Not exporting, so export accumulators stay zero.
        assert data.grid_export_kwh_today == 0.0
        assert data.export_while_battery_not_full_kwh_today == 0.0

    def test_soc_gain_tracked_after_first_sample(self, cost_tracker):
        """SOC gain while grid-charging converts SOC delta to kWh on the 2nd sample."""
        data = self._make_data(grid_power_kw=3.0, battery_power_kw=-2.0, soc=50.0)
        cost_tracker.accumulate_costs(data)  # seeds baseline at 50%
        data.soc = 51.0  # +1% over the interval
        cost_tracker.accumulate_costs(data)

        # 1% of 13.5 kWh = 0.135 kWh gained, attributed to grid charging.
        assert data.soc_gain_during_grid_charge_kwh_today == pytest.approx(
            0.01 * 13.5
        )

    def test_exporting_with_battery_room_counts_as_leak(self, cost_tracker):
        """Exporting while SOC < full accumulates both export and export-with-room."""
        # grid_power_kw=-4.0 export, battery idle, SOC has room.
        data = self._make_data(grid_power_kw=-4.0, battery_power_kw=0.0, soc=60.0)
        cost_tracker.accumulate_costs(data)

        assert data.grid_export_kwh_today == pytest.approx(4.0 / 60)
        assert data.export_while_battery_not_full_kwh_today == pytest.approx(4.0 / 60)
        # Not importing/charging.
        assert data.grid_import_kwh_today == 0.0
        assert data.grid_to_battery_kwh_today == 0.0

    def test_exporting_with_full_battery_not_a_leak(self, cost_tracker):
        """Exporting while the battery is full counts to export total but not leak."""
        data = self._make_data(grid_power_kw=-4.0, battery_power_kw=0.0, soc=100.0)
        cost_tracker.accumulate_costs(data)

        assert data.grid_export_kwh_today == pytest.approx(4.0 / 60)
        assert data.export_while_battery_not_full_kwh_today == 0.0

    def test_idle_below_deadband_accumulates_nothing(self, cost_tracker):
        """Power flows under the 0.1 kW deadband are treated as idle/noise."""
        data = self._make_data(grid_power_kw=0.05, battery_power_kw=-0.05, soc=50.0)
        cost_tracker.accumulate_costs(data)

        assert data.grid_import_kwh_today == 0.0
        assert data.grid_export_kwh_today == 0.0
        assert data.grid_to_battery_kwh_today == 0.0
        assert data.soc_gain_during_grid_charge_kwh_today == 0.0
        assert data.export_while_battery_not_full_kwh_today == 0.0

    def test_reset_daily_accumulators_zeroes_energy_fields(self, cost_tracker):
        """CostTracker.reset_daily_accumulators clears the new kWh fields."""
        data = self._make_data()
        data.grid_import_kwh_today = 5.0
        data.grid_export_kwh_today = 3.0
        data.grid_to_battery_kwh_today = 2.0
        data.soc_gain_during_grid_charge_kwh_today = 1.5
        data.export_while_battery_not_full_kwh_today = 1.0

        cost_tracker.reset_daily_accumulators(data)

        assert data.grid_import_kwh_today == 0.0
        assert data.grid_export_kwh_today == 0.0
        assert data.grid_to_battery_kwh_today == 0.0
        assert data.soc_gain_during_grid_charge_kwh_today == 0.0
        assert data.export_while_battery_not_full_kwh_today == 0.0
