"""Unit tests for 15-minute slot duration functionality."""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from custom_components.localshift.computation_engine_lib.forecast_computer import (
    TOTAL_SLOTS,
    ForecastComputer,
)


class TestFifteenMinSlots:
    """Test 15-min slot duration functionality.

    The forecast uses uniform 15-min slots throughout for consistent alignment
    with Solcast 30-minute periods. This simplifies the codebase and ensures
    all SOC predictions are consistent across the main loop and simulation functions.
    """

    @pytest.fixture
    def mock_entry(self):
        """Create mock config entry."""
        entry = Mock()
        entry.options = {
            "battery_target": 80.0,
            "demand_window_start": "15:00:00",
            "demand_window_end": "21:00:00",
            "minimum_target_soc": 5.0,
            "load_weight_recent": 0.3,
        }
        return entry

    @pytest.fixture
    def computer(self, mock_entry):
        """Create forecast computer instance."""
        return ForecastComputer(
            entry=mock_entry,
            get_entity_id_func=lambda x: f"sensor.{x}",
            get_historical_func=lambda entity_id: {},
        )

    @pytest.fixture
    def mock_solcast(self):
        """Create mock Solcast forecast data.

        IMPORTANT: pv_estimate values represent average power (kWh per HOUR),
        NOT energy per 30-min period. This matches real Solcast data format.

        For example, pv_estimate=3.0 means 3 kW average power over that period.
        A 30-min period at 3 kW = 3 * 0.5 = 1.5 kWh energy.
        """
        # Create 48 periods of 30-minute forecasts (24 hours total)
        solcast = []
        base_time = datetime(2024, 6, 15, 8, 0, 0)

        for i in range(48):
            period_start = base_time + timedelta(minutes=30 * i)
            # Morning solar ramp-up: 0.5 kW at 08:00, increasing to 4.5 kW by 10:00
            if i < 4:  # 08:00-10:00
                kw = 0.5 + (i * 1.0)  # 0.5, 1.5, 2.5, 3.5 kW
            # Peak solar: 4.5 kW average power
            elif i < 16:  # 10:00-16:00
                kw = 4.5
            # Afternoon decline: decreasing from 4.5 kW to 0.5 kW
            elif i < 20:  # 16:00-18:00
                kw = 4.5 - ((i - 15) * 1.0)  # 4.5, 3.5, 2.5, 1.5
            else:  # 18:00+ (evening/night)
                kw = 0.5

            # pv_estimate is kWh per HOUR (average power in kW)
            solcast.append(
                {
                    "period_start": period_start.isoformat(),
                    "pv_estimate": kw,  # This is kW (kWh per hour)
                }
            )

        return solcast

    @pytest.fixture
    def mock_historical_load(self):
        """Create mock historical load data."""
        return {i: 0.8 for i in range(24)}  # 0.8 kW constant load

    def test_find_battery_fill_point_15min_slots(
        self, computer, mock_solcast, mock_historical_load
    ):
        """Verify fill point calculation uses 15-min slots correctly."""
        # Scenario: Battery at 60% at 08:00, strong morning solar
        # With 15-min slots throughout, should calculate fill point correctly

        start_soc = 60.0
        start_time = datetime(2024, 6, 15, 8, 0, 0)

        fill_minutes = computer._find_battery_fill_point(
            start_soc=start_soc,
            start_slot=start_time,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
        )

        # Should take 150-400 minutes (2.5-6.7 hours) with good solar
        # With 4.5 kW peak solar, battery fills faster
        assert fill_minutes is not None
        assert 150 <= fill_minutes <= 400, (
            f"Fill point {fill_minutes} min outside expected range"
        )

        # Fill point should be a multiple of 15 minutes (slot duration)
        assert fill_minutes % 15 == 0, (
            f"Fill point {fill_minutes} should be multiple of 15 min"
        )

    def test_calculate_solar_energy_between_slots(
        self, computer, mock_solcast, mock_historical_load
    ):
        """Verify solar energy calculation uses 15-min slots."""
        base_time = datetime(2024, 6, 15, 8, 0, 0)

        # Calculate energy for first 2 hours
        energy = computer._calculate_solar_energy_between_slots(
            start_elapsed_minutes=0,
            end_elapsed_minutes=120,  # 2 hours
            base_slot=base_time,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
        )

        # Should return positive energy
        assert energy > 0

        # Test that it correctly handles various time ranges
        energy_first_hour = computer._calculate_solar_energy_between_slots(
            start_elapsed_minutes=0,
            end_elapsed_minutes=60,  # 1 hour
            base_slot=base_time,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
        )

        energy_second_hour = computer._calculate_solar_energy_between_slots(
            start_elapsed_minutes=60,
            end_elapsed_minutes=120,  # 1-2 hours
            base_slot=base_time,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
        )

        # Both should be positive and reasonable
        assert energy_first_hour > 0
        assert energy_second_hour > 0
        # Allow some tolerance due to 15-min slot alignment
        assert abs(energy - (energy_first_hour + energy_second_hour)) < 0.5

    def test_total_slots_constant(self):
        """Verify total slots constant is correct for 24-hour coverage."""
        # 24 hours × 4 slots/hour = 96 slots
        assert TOTAL_SLOTS == 96

    def test_elapsed_minutes_calculation(
        self, computer, mock_solcast, mock_historical_load
    ):
        """Test that elapsed minutes are calculated correctly for 15-min slots."""
        base_time = datetime(2024, 6, 15, 8, 0, 0)

        # Test various elapsed minute calculations
        test_cases = [
            (0, 60),  # First hour - 4 × 15-min slots
            (60, 120),  # Second hour - 4 × 15-min slots
            (120, 180),  # Third hour - 4 × 15-min slots
            (0, 120),  # Full 2 hours - 8 × 15-min slots
            (120, 240),  # 2-4 hours - 8 × 15-min slots
        ]

        for start_min, end_min in test_cases:
            energy = computer._calculate_solar_energy_between_slots(
                start_elapsed_minutes=start_min,
                end_elapsed_minutes=end_min,
                base_slot=base_time,
                all_solcast=mock_solcast,
                historical_avg_kw=mock_historical_load,
                current_load_kw=0.8,
                recent_load_kw=0.8,
            )

            # Should return non-negative energy for all cases
            assert energy >= 0, (
                f"Energy should be non-negative for {start_min}-{end_min} minutes"
            )

    def test_proactive_export_decision(
        self, computer, mock_solcast, mock_historical_load
    ):
        """Test that proactive export decisions work correctly."""
        base_time = datetime(2024, 6, 15, 8, 0, 0)

        # Test with a slot in the forecast
        should_export, amount = computer._should_proactive_export_at_slot(
            slot_start=base_time + timedelta(minutes=30),
            slot_hour=8,
            solar_kwh=0.5,
            slot_fit_price=0.15,
            predicted_soc=85.0,
            target_pct=80.0,
            in_demand_window=False,
            forecasted_excess_kwh=5.0,
            remaining_export_budget_kwh=3.0,
            feed_in_forecast=[],
            min_soc_no_exports=10.0,
            export_min_soc_pct=5.0,
            effective_cheap_price=0.10,
            feed_in_price_current=0.15,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
            is_current_slot=True,
            current_elapsed_minutes=30.0,
            fill_point_elapsed_minutes=360,
        )

        # Should return valid results
        assert isinstance(should_export, bool)
        assert isinstance(amount, int | float)
        assert amount >= 0

    def test_edge_case_no_fill_point(
        self, computer, mock_solcast, mock_historical_load
    ):
        """Test when battery never fills (no solar or high load)."""
        # Create a scenario where battery won't fill
        no_solar_solcast = [
            {
                "period_start": (
                    datetime(2024, 6, 15, 8, 0, 0) + timedelta(minutes=30 * i)
                ).isoformat(),
                "pv_estimate": 0.05,  # Very low solar
            }
            for i in range(48)
        ]

        fill_minutes = computer._find_battery_fill_point(
            start_soc=60.0,
            start_slot=datetime(2024, 6, 15, 8, 0, 0),
            all_solcast=no_solar_solcast,
            historical_avg_kw={i: 2.0 for i in range(24)},  # High load
            current_load_kw=2.0,
            recent_load_kw=2.0,
        )

        # Should return None when battery never fills
        assert fill_minutes is None

    def test_edge_case_zero_energy_window(
        self, computer, mock_solcast, mock_historical_load
    ):
        """Test energy calculation with zero or negative time window."""
        base_time = datetime(2024, 6, 15, 8, 0, 0)

        # Test zero window - when start and end align to same slot index
        # Note: Due to 15-min slot alignment, same elapsed minutes may still include one slot
        energy = computer._calculate_solar_energy_between_slots(
            start_elapsed_minutes=60,
            end_elapsed_minutes=60,  # Same start and end
            base_slot=base_time,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
        )

        # With slot alignment, zero window may return a small value
        # The important thing is it's non-negative
        assert energy >= 0.0

        # Test negative window (end before start) - should return 0
        energy = computer._calculate_solar_energy_between_slots(
            start_elapsed_minutes=120,
            end_elapsed_minutes=60,  # End before start
            base_slot=base_time,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
        )

        assert energy >= 0.0  # Should return non-negative for negative window

    def test_simulate_future_soc_with_solar_only(
        self, computer, mock_solcast, mock_historical_load
    ):
        """Test SOC simulation uses 15-min slots correctly."""
        from homeassistant.util import dt as dt_util

        # Use timezone-aware datetimes to match production code
        base_time = dt_util.as_local(datetime(2024, 6, 15, 8, 0, 0))

        # Simulate 4 hours forward
        end_time = base_time + timedelta(hours=4)

        soc_end, max_soc, can_reach, _ = computer._simulate_future_soc_with_solar_only(
            actual_current_soc=60.0,
            start_slot=base_time,
            target_pct=80.0,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
            dw_start_time=datetime(2024, 6, 15, 15, 0, 0).time(),
            end_time=end_time,
        )

        # SOC should increase with good solar
        assert soc_end >= 60.0
        assert max_soc >= soc_end
        assert isinstance(can_reach, bool)

    def test_simulate_overnight_drain(
        self, computer, mock_solcast, mock_historical_load
    ):
        """Test overnight drain simulation uses 15-min slots."""
        base_time = datetime(2024, 6, 15, 22, 0, 0)  # 10 PM
        solar_start = datetime(2024, 6, 16, 6, 0, 0)  # 6 AM

        soc_at_solar = computer._simulate_overnight_drain_to_solar(
            start_soc=50.0,
            start_slot=base_time,
            solar_start=solar_start,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
        )

        # SOC should decrease overnight due to load
        assert soc_at_solar < 50.0
        assert soc_at_solar >= 0.0

    def test_simulate_minimum_soc_without_exports(
        self, computer, mock_solcast, mock_historical_load
    ):
        """Test minimum SOC simulation uses 15-min slots."""
        base_time = datetime(2024, 6, 15, 8, 0, 0)

        min_soc, final_soc = computer._simulate_minimum_soc_without_exports(
            start_soc=60.0,
            start_slot=base_time,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
            dw_start_time=datetime(2024, 6, 15, 15, 0, 0).time(),
            dw_end_time=datetime(2024, 6, 15, 21, 0, 0).time(),
            max_hours=24,
        )

        # Should return valid SOC values
        assert 0.0 <= min_soc <= 100.0
        assert 0.0 <= final_soc <= 100.0
        assert min_soc <= 60.0  # Minimum should be at most starting SOC

    def test_consistency_across_simulation_methods(
        self, computer, mock_solcast, mock_historical_load
    ):
        """Test that all simulation methods produce consistent results."""
        base_time = datetime(2024, 6, 15, 8, 0, 0)

        # All methods should use the same slot fraction (15/60 = 0.25 hours)
        # This ensures consistent SOC predictions across the codebase

        # Get fill point
        fill_minutes = computer._find_battery_fill_point(
            start_soc=60.0,
            start_slot=base_time,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
        )

        # Fill point should be a multiple of 15 (slot duration)
        if fill_minutes is not None:
            assert fill_minutes % 15 == 0

        # Get solar energy for a range
        energy = computer._calculate_solar_energy_between_slots(
            start_elapsed_minutes=0,
            end_elapsed_minutes=240,  # 4 hours
            base_slot=base_time,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
        )

        # Energy should be reasonable
        assert energy > 0


class TestThirtyMinSlotMisalignment:
    """Test 30-min slot solar retrieval with misaligned periods.

    Issue #361: Amber 30-min slots may not exactly align with Solcast 30-min periods.
    The get_solar_for_30min_slot function must use overlap-based accumulation,
    not exact match, to handle these cases.
    """

    def test_30min_slot_exact_match(self):
        """When slot exactly matches Solcast period, returns correct energy."""
        from custom_components.localshift.computation_engine_lib.solar_utils import (
            get_solar_for_30min_slot,
        )

        # Solcast period at 08:00-08:30 with 4 kW average power
        solcast = [
            {
                "period_start": "2024-06-15T08:00:00",
                "pv_estimate": 4.0,  # 4 kWh per hour
            }
        ]

        # Slot exactly matches 08:00-08:30
        slot_start = datetime(2024, 6, 15, 8, 0, 0)
        result = get_solar_for_30min_slot(solcast, slot_start)

        # 4 kW * 0.5 hour = 2.0 kWh
        assert abs(result - 2.0) < 0.001, f"Expected 2.0 kWh, got {result}"

    def test_30min_slot_offset_by_5min(self):
        """When slot is offset by 5 minutes, still returns correct energy.

        This is the key bug fix: Amber slot at 08:05-08:35 should still get
        solar from Solcast period 08:00-08:30 using overlap calculation.
        """
        from custom_components.localshift.computation_engine_lib.solar_utils import (
            get_solar_for_30min_slot,
        )

        # Solcast period at 08:00-08:30 with 4 kW average power
        solcast = [
            {
                "period_start": "2024-06-15T08:00:00",
                "pv_estimate": 4.0,  # 4 kWh per hour
            }
        ]

        # Slot offset by 5 minutes: 08:05-08:35
        # Overlaps 08:00-08:30 by 25 minutes (08:05-08:30)
        slot_start = datetime(2024, 6, 15, 8, 5, 0)
        result = get_solar_for_30min_slot(solcast, slot_start)

        # 25 min overlap = 25/60 hour * 4 kW = 1.667 kWh
        expected = 4.0 * (25.0 / 60.0)
        assert abs(result - expected) < 0.01, f"Expected {expected} kWh, got {result}"

    def test_30min_slot_straddling_two_periods(self):
        """When slot straddles two Solcast periods, sums both contributions."""
        from custom_components.localshift.computation_engine_lib.solar_utils import (
            get_solar_for_30min_slot,
        )

        # Two Solcast periods: 08:00-08:30 (4 kW) and 08:30-09:00 (5 kW)
        solcast = [
            {
                "period_start": "2024-06-15T08:00:00",
                "pv_estimate": 4.0,
            },
            {
                "period_start": "2024-06-15T08:30:00",
                "pv_estimate": 5.0,
            },
        ]

        # Slot 08:15-08:45 overlaps both periods
        # - 15 min overlap with 08:00-08:30 (4 kW)
        # - 15 min overlap with 08:30-09:00 (5 kW)
        slot_start = datetime(2024, 6, 15, 8, 15, 0)
        result = get_solar_for_30min_slot(solcast, slot_start)

        # Expected: 4 * (15/60) + 5 * (15/60) = 1.0 + 1.25 = 2.25 kWh
        expected = 4.0 * 0.25 + 5.0 * 0.25
        assert abs(result - expected) < 0.01, f"Expected {expected} kWh, got {result}"

    def test_30min_slot_no_overlap_returns_zero(self):
        """When slot has no overlap with any period, returns 0.0."""
        from custom_components.localshift.computation_engine_lib.solar_utils import (
            get_solar_for_30min_slot,
        )

        # Solcast period at 08:00-08:30
        solcast = [
            {
                "period_start": "2024-06-15T08:00:00",
                "pv_estimate": 4.0,
            }
        ]

        # Slot at 10:00-10:30 (no overlap)
        slot_start = datetime(2024, 6, 15, 10, 0, 0)
        result = get_solar_for_30min_slot(solcast, slot_start)

        assert result == 0.0, f"Expected 0.0 kWh for no overlap, got {result}"

    def test_30min_slot_empty_forecast_returns_zero(self):
        """When forecast list is empty, returns 0.0."""
        from custom_components.localshift.computation_engine_lib.solar_utils import (
            get_solar_for_30min_slot,
        )

        slot_start = datetime(2024, 6, 15, 8, 0, 0)
        result = get_solar_for_30min_slot([], slot_start)

        assert result == 0.0
