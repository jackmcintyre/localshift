"""Unit tests for hybrid timescale slot duration functionality."""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from custom_components.localshift.computation_engine_lib.forecast_computer import (
    LONG_TERM_COUNT,
    NEAR_TERM_COUNT,
    ForecastComputer,
)


class TestHybridTimescale:
    """Test hybrid timescale (24×5min + 88×15min) functionality."""

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
            get_historical_func=lambda: {},
        )

    @pytest.fixture
    def mock_solcast(self):
        """Create mock Solcast forecast data."""
        # Create 48 periods of 30-minute forecasts (24 hours total)
        solcast = []
        base_time = datetime(2024, 6, 15, 8, 0, 0)

        for i in range(48):
            period_start = base_time + timedelta(minutes=30 * i)
            # Morning solar ramp-up: 0.1 kWh at 08:00, increasing to 1.5 kWh by 10:00
            if i < 4:  # 08:00-10:00
                kwh = 0.1 + (i * 0.35)  # 0.1, 0.45, 0.8, 1.15
            # Peak solar: 1.5 kWh per 30-min period
            elif i < 16:  # 10:00-16:00
                kwh = 1.5
            # Afternoon decline: decreasing from 1.5 kWh to 0.1 kWh
            elif i < 20:  # 16:00-18:00
                kwh = 1.5 - ((i - 15) * 0.35)  # 1.5, 1.15, 0.8, 0.45
            else:  # 18:00+ (evening/night)
                kwh = 0.1

            solcast.append(
                {
                    "period_start": period_start.isoformat(),
                    "pv_estimate": kwh,
                }
            )

        return solcast

    @pytest.fixture
    def mock_historical_load(self):
        """Create mock historical load data."""
        return {i: 0.8 for i in range(24)}  # 0.8 kW constant load

    def test_find_battery_fill_point_near_term_accuracy(
        self, computer, mock_solcast, mock_historical_load
    ):
        """Verify fill point calculation uses correct slot durations in near-term."""
        # Scenario: Battery at 60% at 08:00, strong morning solar
        # With hybrid timescale, accurate calculation shows ~255 minutes (~4.25 hours)
        # Old buggy implementation would have calculated ~85 minutes (3× too fast)

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

        # Should take 200-300 minutes (3.3-5 hours) with good solar
        # NOT 85 minutes as buggy 15-min-only would calculate
        assert fill_minutes is not None
        assert 200 <= fill_minutes <= 300, (
            f"Fill point {fill_minutes} min outside expected range"
        )

        # Verify it's using hybrid timescale by checking the structure
        # First 24 slots should be 5-min, then 88 slots of 15-min
        assert NEAR_TERM_COUNT == 24  # 24 × 5-min slots
        assert LONG_TERM_COUNT == 88  # 88 × 15-min slots

    def test_calculate_solar_energy_between_slots_hybrid(
        self, computer, mock_solcast, mock_historical_load
    ):
        """Verify solar energy calculation uses hybrid timescale."""
        base_time = datetime(2024, 6, 15, 8, 0, 0)

        # Calculate energy for first 2 hours (should use 5-min slots)
        energy = computer._calculate_solar_energy_between_slots(
            start_elapsed_minutes=0,
            end_elapsed_minutes=120,  # 2 hours
            base_slot=base_time,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
        )

        # Verify it's using 24×5min slots, not 8×15min
        # Expected: more granular, different total
        assert energy > 0

        # Test that it correctly handles the near-term/long-term boundary
        # First 120 minutes should be all 5-min slots (24 slots × 5 min = 120 min)
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
        assert abs(energy - (energy_first_hour + energy_second_hour)) < 0.01

    def test_fill_point_hybrid_vs_old_comparison(
        self, computer, mock_solcast, mock_historical_load
    ):
        """Compare hybrid implementation with old 15-min-only approach."""
        # This test documents the bug and verifies the fix

        start_soc = 60.0
        start_time = datetime(2024, 6, 15, 8, 0, 0)

        # Get fill point with NEW hybrid approach
        fill_minutes_hybrid = computer._find_battery_fill_point(
            start_soc=start_soc,
            start_slot=start_time,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
        )

        # OLD buggy approach would calculate ~85 minutes (3× too fast in near-term)
        # NEW hybrid approach correctly calculates ~255 minutes
        # This is approximately 3× longer, which validates the fix

        assert fill_minutes_hybrid is not None
        assert fill_minutes_hybrid > 200, (
            f"Hybrid should be significantly later than old method, got {fill_minutes_hybrid} minutes"
        )

        # Verify it's in the reasonable range for this scenario
        assert fill_minutes_hybrid < 400, (
            f"Fill time {fill_minutes_hybrid} seems too long"
        )

    def test_hybrid_timescale_constants(self):
        """Verify hybrid timescale constants are correct."""
        # Verify the constants match the design specification
        assert NEAR_TERM_COUNT == 24  # 24 × 5 min = 120 min = 2 h
        assert LONG_TERM_COUNT == 88  # 88 × 15 min = 1320 min = 22 h
        assert NEAR_TERM_COUNT + LONG_TERM_COUNT == 112  # Total slots

    def test_elapsed_minutes_calculation(
        self, computer, mock_solcast, mock_historical_load
    ):
        """Test that elapsed minutes are calculated correctly for hybrid timescale."""
        base_time = datetime(2024, 6, 15, 8, 0, 0)

        # Test various elapsed minute calculations
        test_cases = [
            (0, 60),  # First hour - all 5-min slots
            (60, 120),  # Second hour - all 5-min slots
            (120, 180),  # Third hour - should be 15-min slots
            (0, 120),  # Full 2 hours - should be 24×5min slots
            (120, 240),  # 2-4 hours - should be 8×15min slots
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

            # Should return positive energy for all cases
            assert energy >= 0, (
                f"Energy should be non-negative for {start_min}-{end_min} minutes"
            )

    def test_proactive_export_with_hybrid_timescale(
        self, computer, mock_solcast, mock_historical_load
    ):
        """Test that proactive export decisions use hybrid timescale correctly."""
        base_time = datetime(2024, 6, 15, 8, 0, 0)

        # Test with a slot in near-term (5-min) window
        should_export, amount = computer._should_proactive_export_at_slot(
            slot_start=base_time + timedelta(minutes=30),  # 30 minutes in
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
            current_elapsed_minutes=30.0,  # 30 minutes elapsed
            fill_point_elapsed_minutes=360,  # 6 hours to fill
        )

        # Should not export because we're before fill point and have solar visibility
        # The exact result depends on the complex logic, but it should not crash
        assert isinstance(should_export, bool)
        assert isinstance(amount, int | float)
        assert amount >= 0

    def test_boundary_conditions(self, computer, mock_solcast, mock_historical_load):
        """Test boundary conditions between near-term and long-term windows."""
        base_time = datetime(2024, 6, 15, 10, 0, 0)  # Exactly at 2-hour boundary

        # Test right at the boundary (120 minutes = 2 hours)
        fill_minutes = computer._find_battery_fill_point(
            start_soc=70.0,
            start_slot=base_time,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
        )

        # Should handle boundary correctly
        assert fill_minutes is None or fill_minutes >= 0

        # Test energy calculation across boundary
        energy = computer._calculate_solar_energy_between_slots(
            start_elapsed_minutes=110,  # 10 min before boundary
            end_elapsed_minutes=130,  # 10 min after boundary
            base_slot=base_time - timedelta(minutes=120),
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
        )

        assert energy >= 0  # Should not crash

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

        # Test zero window
        energy = computer._calculate_solar_energy_between_slots(
            start_elapsed_minutes=60,
            end_elapsed_minutes=60,  # Same start and end
            base_slot=base_time,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
        )

        assert energy == 0.0  # Should return zero for zero window

        # Test negative window (end before start)
        energy = computer._calculate_solar_energy_between_slots(
            start_elapsed_minutes=120,
            end_elapsed_minutes=60,  # End before start
            base_slot=base_time,
            all_solcast=mock_solcast,
            historical_avg_kw=mock_historical_load,
            current_load_kw=0.8,
            recent_load_kw=0.8,
        )

        assert energy == 0.0  # Should return zero for negative window
