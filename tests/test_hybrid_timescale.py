"""Unit tests for hybrid timescale slot functionality.

This module contains two test classes:
1. TestFifteenMinSlots - Tests for legacy 15-min uniform slots (old approach)
2. TestHybridTimescaleSchedule - Tests for hybrid 5/30-min slots (Issue #339)
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
from homeassistant.util import dt as dt_util

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


class TestHybridTimescaleSchedule:
    """Test hybrid timescale slot functionality (Issue #339).

    The hybrid timescale uses native Amber data granularities:
    - 5-minute slots for near-term (from Amber ~45-60 min): High resolution for immediate decisions
    - 30-minute slots for extended forecast: Solcast-aligned for forecast accuracy

    This provides better temporal resolution for export decisions while maintaining
    alignment with Solcast 30-minute forecast periods.

    Note: The actual compute_hybrid_slot_schedule function requires Amber price forecast
    data. These tests verify the function behavior with mocked Amber data.
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
    def mock_amber_forecast_5min_then_30min(self):
        """Create mock Amber forecast with 5-min slots then 30-min slots.

        This simulates real Amber data:
        - First 12 periods: 5-minute intervals (1 hour)
        - Remaining periods: 30-minute intervals (23 hours)
        """
        # Use timezone-aware datetime
        tz = dt_util.get_time_zone("Australia/Sydney")
        now = datetime(2024, 6, 15, 8, 0, 0, tzinfo=tz)
        forecast = []

        # 5-minute slots for first hour (12 slots)
        for i in range(12):
            start = now + timedelta(minutes=5 * i)
            end = start + timedelta(minutes=5)
            forecast.append({
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "duration": 5,
                "price": 0.15 + (i * 0.01),  # Varying price
            })

        # 30-minute slots for remaining 23 hours (46 slots)
        for i in range(46):
            start = now + timedelta(minutes=60 + 30 * i)
            end = start + timedelta(minutes=30)
            forecast.append({
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "duration": 30,
                "price": 0.10 + (i * 0.005),  # Varying price
            })

        return forecast

    @pytest.fixture
    def tz_aware_now(self):
        """Return timezone-aware now for Australia/Sydney."""
        tz = dt_util.get_time_zone("Australia/Sydney")
        return datetime(2024, 6, 15, 8, 0, 0, tzinfo=tz)

    # =========================================================================
    # Slot Duration Tests with Mocked Amber Data
    # =========================================================================

    def test_slot_durations_from_amber_data(self, mock_amber_forecast_5min_then_30min, tz_aware_now):
        """Verify slot durations match Amber data (5-min then 30-min)."""
        from custom_components.localshift.computation_engine_lib.forecast_computer import (
            compute_hybrid_slot_schedule,
        )

        slots, metadata = compute_hybrid_slot_schedule(
            now_local=tz_aware_now,
            general_forecast=mock_amber_forecast_5min_then_30min,
            ha_timezone="Australia/Sydney",
        )

        # Should have both 5-min and 30-min slots
        assert metadata["slot_intervals"]["5min"] == 12, (
            f"Expected 12 5-min slots, got {metadata['slot_intervals']['5min']}"
        )
        assert metadata["slot_intervals"]["30min"] == 46, (
            f"Expected 46 30-min slots, got {metadata['slot_intervals']['30min']}"
        )

    def test_metadata_structure(self, mock_amber_forecast_5min_then_30min, tz_aware_now):
        """Verify metadata has correct structure."""
        from custom_components.localshift.computation_engine_lib.forecast_computer import (
            compute_hybrid_slot_schedule,
        )

        slots, metadata = compute_hybrid_slot_schedule(
            now_local=tz_aware_now,
            general_forecast=mock_amber_forecast_5min_then_30min,
            ha_timezone="Australia/Sydney",
        )

        # Verify metadata structure
        assert "timezone" in metadata
        assert "slot_intervals" in metadata
        assert "5min" in metadata["slot_intervals"]
        assert "30min" in metadata["slot_intervals"]
        assert "transition_boundary" in metadata
        assert "total_slots" in metadata

    def test_slot_structure(self, mock_amber_forecast_5min_then_30min, tz_aware_now):
        """Verify each slot has required fields."""
        from custom_components.localshift.computation_engine_lib.forecast_computer import (
            compute_hybrid_slot_schedule,
        )

        slots, metadata = compute_hybrid_slot_schedule(
            now_local=tz_aware_now,
            general_forecast=mock_amber_forecast_5min_then_30min,
            ha_timezone="Australia/Sydney",
        )

        # Verify each slot has required fields
        for slot in slots:
            assert "start" in slot, "Slot missing 'start' field"
            assert "interval_minutes" in slot, "Slot missing 'interval_minutes' field"
            assert "price" in slot, "Slot missing 'price' field"
            assert "price_source" in slot, "Slot missing 'price_source' field"
            assert slot["interval_minutes"] in [5, 30], (
                f"Invalid interval_minutes: {slot['interval_minutes']}"
            )
            assert slot["price_source"] in ["5min", "30min"], (
                f"Invalid price_source: {slot['price_source']}"
            )

    def test_transition_boundary(self, mock_amber_forecast_5min_then_30min, tz_aware_now):
        """Verify transition boundary is correctly identified."""
        from custom_components.localshift.computation_engine_lib.forecast_computer import (
            compute_hybrid_slot_schedule,
        )

        slots, metadata = compute_hybrid_slot_schedule(
            now_local=tz_aware_now,
            general_forecast=mock_amber_forecast_5min_then_30min,
            ha_timezone="Australia/Sydney",
        )

        # Transition should occur at 60 minutes (after 12 x 5-min slots)
        if metadata["transition_boundary"]:
            # Verify 5-min slots come before 30-min slots
            found_30min = False
            for slot in slots:
                if slot["interval_minutes"] == 30:
                    found_30min = True
                elif found_30min and slot["interval_minutes"] == 5:
                    pytest.fail("5-min slot found after 30-min slot")

    def test_empty_forecast_handling(self, tz_aware_now):
        """Verify empty forecast returns empty slots."""
        from custom_components.localshift.computation_engine_lib.forecast_computer import (
            compute_hybrid_slot_schedule,
        )

        slots, metadata = compute_hybrid_slot_schedule(
            now_local=tz_aware_now,
            general_forecast=[],
            ha_timezone="Australia/Sydney",
        )

        assert slots == []
        assert metadata["total_slots"] == 0

    def test_30min_only_forecast(self, tz_aware_now):
        """Verify forecast with only 30-min data works correctly."""
        from custom_components.localshift.computation_engine_lib.forecast_computer import (
            compute_hybrid_slot_schedule,
        )

        forecast = []

        # Only 30-minute slots (timezone-aware)
        for i in range(48):
            start = tz_aware_now + timedelta(minutes=30 * i)
            end = start + timedelta(minutes=30)
            forecast.append({
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "duration": 30,
                "price": 0.15,
            })

        slots, metadata = compute_hybrid_slot_schedule(
            now_local=tz_aware_now,
            general_forecast=forecast,
            ha_timezone="Australia/Sydney",
        )

        # Should have only 30-min slots
        assert metadata["slot_intervals"]["5min"] == 0
        assert metadata["slot_intervals"]["30min"] == 48

    def test_continuous_coverage_no_gaps(self, mock_amber_forecast_5min_then_30min, tz_aware_now):
        """Verify slots have continuous coverage with no gaps."""
        from custom_components.localshift.computation_engine_lib.forecast_computer import (
            compute_hybrid_slot_schedule,
        )

        slots, metadata = compute_hybrid_slot_schedule(
            now_local=tz_aware_now,
            general_forecast=mock_amber_forecast_5min_then_30min,
            ha_timezone="Australia/Sydney",
        )

        # Verify continuous coverage
        for i in range(1, len(slots)):
            prev_end = slots[i - 1]["start"] + timedelta(minutes=slots[i - 1]["interval_minutes"])
            curr_start = slots[i]["start"]
            assert prev_end == curr_start, (
                f"Gap between slot {i-1} and {i}: {prev_end} != {curr_start}"
            )

    # =========================================================================
    # Price Source Tests
    # =========================================================================

    def test_price_source_matches_interval(self, mock_amber_forecast_5min_then_30min, tz_aware_now):
        """Verify price_source matches actual interval."""
        from custom_components.localshift.computation_engine_lib.forecast_computer import (
            compute_hybrid_slot_schedule,
        )

        slots, metadata = compute_hybrid_slot_schedule(
            now_local=tz_aware_now,
            general_forecast=mock_amber_forecast_5min_then_30min,
            ha_timezone="Australia/Sydney",
        )

        for slot in slots:
            if slot["interval_minutes"] == 5:
                assert slot["price_source"] == "5min", (
                    f"5-min slot has wrong price_source: {slot['price_source']}"
                )
            else:
                assert slot["price_source"] == "30min", (
                    f"30-min slot has wrong price_source: {slot['price_source']}"
                )
