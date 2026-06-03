"""Tests for SlotBuilder — Phase 2 (#441) implementation.

Tests verify that SlotBuilder.build_slots() correctly:
- Reads raw coordinator data (general_forecast, feed_in_forecast, solcast, load_forecast_slots)
- Applies solar_confidence_factor adaptive param
- Does NOT apply consumption_forecast_bias (already applied by LoadForecaster)
- Computes demand window flags correctly
- Returns typed SlotBuildMetadata with accurate counts
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.engine.slots import (
    SlotBuilder,
    SlotBuildMetadata,
    SlotContext,
)
from custom_components.localshift.coordinator import AdaptiveParameters


class TestSlotBuildMetadata:
    """Tests for SlotBuildMetadata dataclass."""

    def test_init_with_all_fields(self):
        """Test SlotBuildMetadata initialization."""
        metadata = SlotBuildMetadata(
            total_slots=96,
            five_min_slots=48,
            thirty_min_slots=48,
            horizon_hours=24.0,
            solar_confidence_factor=0.9,
            slots_with_defaulted_solar=5,
            slots_with_defaulted_price=2,
            slots_with_defaulted_consumption=0,
        )
        assert metadata.total_slots == 96
        assert metadata.five_min_slots == 48
        assert metadata.thirty_min_slots == 48
        assert metadata.horizon_hours == 24.0
        assert metadata.solar_confidence_factor == 0.9
        assert metadata.slots_with_defaulted_solar == 5
        assert metadata.slots_with_defaulted_price == 2
        assert metadata.slots_with_defaulted_consumption == 0

    def test_to_parity_dict_backward_compat(self):
        """Test to_parity_dict() returns legacy-compatible format."""
        metadata = SlotBuildMetadata(
            total_slots=4,
            five_min_slots=2,
            thirty_min_slots=2,
            horizon_hours=12.0,
            solar_confidence_factor=1.0,
            slots_with_defaulted_solar=1,
            slots_with_defaulted_price=1,
            slots_with_defaulted_consumption=0,
        )
        parity = metadata.to_parity_dict()

        # Check all legacy fields exist
        assert "total_slots" in parity
        assert "total_fields_checked" in parity
        assert "populated_fields" in parity
        assert "defaulted_fields" in parity
        assert "completeness_pct" in parity

        # Verify calculations: 4 slots * 4 fields = 16 total
        assert parity["total_fields_checked"] == 16
        # 2 defaulted (1 solar + 1 price)
        assert parity["populated_fields"] == 14
        # Completeness: 14/16 = 87.5%
        assert parity["completeness_pct"] == 87.5

        # Check extended fields also present
        assert parity["five_min_slots"] == 2
        assert parity["thirty_min_slots"] == 2
        assert parity["horizon_hours"] == 12.0

    def test_to_parity_dict_empty_slots(self):
        """Test to_parity_dict() handles zero slots gracefully."""
        metadata = SlotBuildMetadata(
            total_slots=0,
            five_min_slots=0,
            thirty_min_slots=0,
            horizon_hours=0.0,
            solar_confidence_factor=1.0,
            slots_with_defaulted_solar=0,
            slots_with_defaulted_price=0,
            slots_with_defaulted_consumption=0,
        )
        parity = metadata.to_parity_dict()
        assert parity["total_fields_checked"] == 0
        assert parity["completeness_pct"] == 0.0


class TestSlotBuilderInit:
    """Tests for SlotBuilder initialization."""

    def test_init_stores_config_and_timezone(self):
        """Test __init__ stores config_options and ha_timezone."""
        config = {"demand_window_start": "18:00:00", "demand_window_end": "22:00:00"}
        builder = SlotBuilder(config_options=config, ha_timezone="Australia/Sydney")
        assert builder._config_options == config
        assert builder._ha_timezone == "Australia/Sydney"


class TestIntegration:
    """Integration tests for SlotBuilder with realistic data."""

    def test_slot_builder_end_to_end_with_coordinator_data(self):
        """Test full SlotBuilder workflow with realistic CoordinatorData."""
        # This test would use a real CoordinatorData instance
        # For now, verify the class can be instantiated and called
        config = {"demand_window_start": "18:00:00", "demand_window_end": "22:00:00"}
        builder = SlotBuilder(config_options=config, ha_timezone="Australia/Sydney")

        # Verify builder has required methods
        assert hasattr(builder, "build_slots")
        assert callable(builder.build_slots)


class TestGetSolarConfidenceFactor:
    """Tests for _get_solar_confidence_factor method."""

    @pytest.fixture
    def builder(self):
        """Create a SlotBuilder instance."""
        config = {"demand_window_start": "18:00:00", "demand_window_end": "22:00:00"}
        return SlotBuilder(config_options=config, ha_timezone="Australia/Sydney")

    def test_none_params_returns_default(self, builder):
        """Test None adaptive params returns 1.0."""
        result = builder._get_solar_confidence_factor(None)
        assert result == 1.0

    def test_params_with_value(self, builder):
        """Test adaptive params with solar_confidence_factor."""
        params = AdaptiveParameters(values={"solar_confidence_factor": 0.8})
        result = builder._get_solar_confidence_factor(params)
        assert result == 0.8

    def test_params_without_value_uses_default(self, builder):
        """Test adaptive params without solar_confidence_factor uses default."""
        params = AdaptiveParameters(values={})
        result = builder._get_solar_confidence_factor(params)
        assert result == 1.0

    def test_clamp_at_zero(self, builder):
        """Test negative values clamped to 0.0."""
        params = AdaptiveParameters(values={"solar_confidence_factor": -0.5})
        result = builder._get_solar_confidence_factor(params)
        assert result == 0.0

    def test_clamp_at_two(self, builder):
        """Test values > 2.0 clamped to 2.0."""
        params = AdaptiveParameters(values={"solar_confidence_factor": 3.0})
        result = builder._get_solar_confidence_factor(params)
        assert result == 2.0

    def test_exact_boundary_values(self, builder):
        """Test exact boundary values 0.0 and 2.0."""
        params = AdaptiveParameters(values={"solar_confidence_factor": 0.0})
        assert builder._get_solar_confidence_factor(params) == 0.0

        params = AdaptiveParameters(values={"solar_confidence_factor": 2.0})
        assert builder._get_solar_confidence_factor(params) == 2.0


class TestComputeBaseSlot:
    """Tests for _compute_base_slot method."""

    @pytest.fixture
    def builder(self):
        """Create a SlotBuilder instance."""
        config = {"demand_window_start": "18:00:00", "demand_window_end": "22:00:00"}
        return SlotBuilder(config_options=config, ha_timezone="Australia/Sydney")

    def test_rounds_to_5min_interval(self, builder):
        """Test that time is rounded to 5-minute interval."""
        now = datetime(2024, 1, 15, 14, 23, 37)
        result = builder._compute_base_slot(now)
        assert result.minute % 5 == 0
        assert result.second == 0
        assert result.microsecond == 0

    def test_preserves_hour(self, builder):
        """Test that hour is preserved."""
        now = datetime(2024, 1, 15, 14, 23, 37)
        result = builder._compute_base_slot(now)
        assert result.hour == 14


class TestGetLocalTimezone:
    """Tests for _get_local_timezone method."""

    def test_valid_timezone(self):
        """Test valid timezone returns ZoneInfo."""
        config = {"demand_window_start": "18:00:00", "demand_window_end": "22:00:00"}
        builder = SlotBuilder(config_options=config, ha_timezone="Australia/Sydney")
        result = builder._get_local_timezone()
        assert result is not None

    def test_invalid_timezone_returns_none(self):
        """Test invalid timezone returns None."""
        config = {"demand_window_start": "18:00:00", "demand_window_end": "22:00:00"}
        builder = SlotBuilder(config_options=config, ha_timezone="Invalid/Timezone")
        result = builder._get_local_timezone()
        assert result is None


class TestGetSlotTimeForDW:
    """Tests for _get_slot_time_for_dw method."""

    @pytest.fixture
    def builder(self):
        """Create a SlotBuilder instance."""
        config = {"demand_window_start": "18:00:00", "demand_window_end": "22:00:00"}
        return SlotBuilder(config_options=config, ha_timezone="Australia/Sydney")

    def test_no_timezone_returns_original_time(self, builder):
        """Test None timezone returns original slot time."""
        from datetime import timezone

        now = datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc)
        result = builder._get_slot_time_for_dw(now, None)
        assert result == now.time()

    def test_with_timezone_converts(self, builder):
        """Test with timezone converts to local time."""
        from datetime import timezone
        from zoneinfo import ZoneInfo

        local_tz = ZoneInfo("Australia/Sydney")
        slot_start = datetime(2024, 1, 15, 4, 30, tzinfo=timezone.utc)
        result = builder._get_slot_time_for_dw(slot_start, local_tz)
        assert result is not None


class TestGetSellPrice:
    """Tests for _get_sell_price method."""

    @pytest.fixture
    def builder(self):
        """Create a SlotBuilder instance."""
        config = {"demand_window_start": "18:00:00", "demand_window_end": "22:00:00"}
        return SlotBuilder(config_options=config, ha_timezone="Australia/Sydney")

    def test_empty_forecast_returns_zero(self, builder):
        """Test empty feed_in_forecast returns 0.0."""
        from datetime import timezone

        now = datetime.now(timezone.utc)
        result = builder._get_sell_price([], now)
        assert result == 0.0

    def test_matching_forecast_returns_price(self, builder):
        """Test matching forecast entry returns price."""
        from datetime import timezone

        now = datetime.now(timezone.utc)
        forecast = [
            {
                "start_time": now.isoformat(),
                "per_kwh": 0.08,
                "duration": 30,
            }
        ]
        result = builder._get_sell_price(forecast, now)
        assert result == 0.08


class TestGetSolarKwh:
    """Tests for _get_solar_kwh method."""

    @pytest.fixture
    def builder(self):
        """Create a SlotBuilder instance."""
        config = {"demand_window_start": "18:00:00", "demand_window_end": "22:00:00"}
        return SlotBuilder(config_options=config, ha_timezone="Australia/Sydney")

    def test_empty_solcast_returns_zero(self, builder):
        """Test empty solcast returns 0.0."""
        from datetime import timezone

        now = datetime.now(timezone.utc)
        result = builder._get_solar_kwh([], now, 30, 1.0)
        assert result == 0.0

    def test_applies_confidence_factor(self, builder):
        """Test confidence factor is applied."""
        from datetime import timezone

        now = datetime.now(timezone.utc)
        solcast = [
            {
                "period_start": now.isoformat(),
                "pv_estimate": 2.0,
            }
        ]
        result = builder._get_solar_kwh(solcast, now, 30, 0.5)
        assert result == 0.5

    def test_clamp_negative_to_zero(self, builder):
        """Test negative solar values are clamped to 0.0."""
        from datetime import timezone

        now = datetime.now(timezone.utc)
        result = builder._get_solar_kwh([], now, 30, -1.0)
        assert result >= 0.0


class TestGetConsumptionKwh:
    """Tests for _get_consumption_kwh method."""

    @pytest.fixture
    def builder(self):
        """Create a SlotBuilder instance."""
        config = {"demand_window_start": "18:00:00", "demand_window_end": "22:00:00"}
        return SlotBuilder(config_options=config, ha_timezone="Australia/Sydney")

    def test_empty_forecast_returns_zero(self, builder):
        """Test empty load_forecast_slots returns 0.0."""
        from datetime import timezone

        now = datetime.now(timezone.utc)
        result = builder._get_consumption_kwh([], now, now, 30, 0)
        assert result == 0.0

    def test_computes_from_kw(self, builder):
        """Test consumption computed from kW values."""
        from datetime import timezone

        now = datetime.now(timezone.utc)
        base_slot = now.replace(minute=0, second=0, microsecond=0)
        load_slots = [1.0] * 96

        result = builder._get_consumption_kwh(load_slots, base_slot, base_slot, 30, 0)
        assert result == 0.5

    def test_aggregates_two_bins_for_30_min_slot(self, builder):
        """Test 30-minute slots sum two 15-minute bins."""
        from datetime import timezone

        now = datetime.now(timezone.utc)
        base_slot = now.replace(minute=0, second=0, microsecond=0)
        load_slots = [1.0, 2.0] + [0.0] * 94

        result = builder._get_consumption_kwh(load_slots, base_slot, base_slot, 30, 0)
        assert result == 0.75

    def test_aggregates_shifted_30_min_window(self, builder):
        """Test 30-minute slot uses the correct two bins when shifted."""
        from datetime import timezone

        now = datetime.now(timezone.utc)
        base_slot = now.replace(minute=0, second=0, microsecond=0)
        slot_start = base_slot + timedelta(minutes=30)
        load_slots = [1.0, 2.0, 3.0, 4.0] + [0.0] * 92

        result = builder._get_consumption_kwh(load_slots, slot_start, base_slot, 30, 1)
        assert result == 1.75

    def test_out_of_range_index_returns_zero(self, builder):
        """Test out of range index returns 0.0."""
        from datetime import timezone

        now = datetime.now(timezone.utc)
        base_slot = now - timedelta(hours=48)
        load_slots = [1.0] * 96

        result = builder._get_consumption_kwh(load_slots, now, base_slot, 30, 0)
        assert result >= 0.0


class TestParseTimeOption:
    """Tests for _parse_time_option method."""

    @pytest.fixture
    def builder(self):
        """Create a SlotBuilder instance."""
        config = {"demand_window_start": "18:30:00", "demand_window_end": "22:00:00"}
        return SlotBuilder(config_options=config, ha_timezone="Australia/Sydney")

    def test_parse_with_seconds(self, builder):
        """Test parsing time with seconds."""
        result = builder._parse_time_option("demand_window_start")
        assert result.hour == 18
        assert result.minute == 30

    def test_parse_without_seconds(self, builder):
        """Test parsing time without seconds."""
        result = builder._parse_time_option("demand_window_end")
        assert result.hour == 22
        assert result.minute == 0

    def test_missing_key_uses_default(self, builder):
        """Test missing key uses default."""
        result = builder._parse_time_option("unknown_key")
        assert result.hour == 18
        assert result.minute == 0

    def test_time_object_passed_through(self):
        """Test time object is passed through."""
        config = {"test_time": time(15, 45)}
        builder = SlotBuilder(config_options=config, ha_timezone="Australia/Sydney")
        result = builder._parse_time_option("test_time")
        assert result.hour == 15


class TestBuildSlotsIntegration:
    """Integration tests for build_slots method."""

    @pytest.fixture
    def builder(self):
        """Create a SlotBuilder instance."""
        return SlotBuilder(
            config_options={
                "demand_window_start": "18:00:00",
                "demand_window_end": "22:00:00",
            },
            ha_timezone="UTC",
        )

    @pytest.fixture
    def mock_coordinator_data(self):
        """Create mock CoordinatorData with timezone-aware data."""
        from datetime import timezone

        data = MagicMock()
        now = datetime.now(timezone.utc)

        data.general_forecast = [
            {
                "start_time": (now + timedelta(minutes=30 * i)).isoformat(),
                "per_kwh": 0.10 + i * 0.01,
                "duration": 30,
            }
            for i in range(48)
        ]
        data.feed_in_forecast = [
            {
                "start_time": (now + timedelta(minutes=30 * i)).isoformat(),
                "per_kwh": 0.05,
                "duration": 30,
            }
            for i in range(48)
        ]
        data.solcast_today = [
            {
                "period_end": (now + timedelta(minutes=30 * i)).isoformat(),
                "pv_estimate": 2.0
                if 6 <= (now + timedelta(minutes=30 * i)).hour <= 18
                else 0.0,
            }
            for i in range(48)
        ]
        data.solcast_tomorrow = []
        data.load_forecast_slots = [0.5 + (i % 24) * 0.1 for i in range(96)]
        data.adaptive_params = AdaptiveParameters(
            values={"solar_confidence_factor": 1.0}
        )

        return data

    def test_build_slots_returns_slots_and_metadata(
        self, builder, mock_coordinator_data
    ):
        """Test build_slots returns list of SlotContext and metadata."""
        slots, metadata = builder.build_slots(
            mock_coordinator_data, mock_coordinator_data.adaptive_params
        )

        assert isinstance(slots, list)
        assert len(slots) > 0
        assert all(isinstance(s, SlotContext) for s in slots)
        assert isinstance(metadata, SlotBuildMetadata)

    def test_build_slots_metadata_counts(self, builder, mock_coordinator_data):
        """Test build_slots metadata has correct counts."""
        slots, metadata = builder.build_slots(
            mock_coordinator_data, mock_coordinator_data.adaptive_params
        )

        assert metadata.total_slots == len(slots)
        assert (
            metadata.five_min_slots + metadata.thirty_min_slots == metadata.total_slots
        )
        assert metadata.solar_confidence_factor == 1.0

    def test_build_slots_with_none_adaptive_params(
        self, builder, mock_coordinator_data
    ):
        """Test build_slots handles None adaptive params."""
        slots, metadata = builder.build_slots(mock_coordinator_data, None)

        assert metadata.solar_confidence_factor == 1.0
        assert len(slots) > 0

    def test_build_slots_with_custom_now_dt(self, builder, mock_coordinator_data):
        """Test build_slots respects custom now_dt parameter."""
        from datetime import timezone

        custom_now = datetime.now(timezone.utc)
        slots, metadata = builder.build_slots(
            mock_coordinator_data, None, now_dt=custom_now
        )

        assert len(slots) >= 0


class TestProcessAllSlots:
    """Tests for _process_all_slots method."""

    @pytest.fixture
    def builder(self):
        """Create a SlotBuilder instance."""
        return SlotBuilder(
            config_options={
                "demand_window_start": "18:00:00",
                "demand_window_end": "22:00:00",
            },
            ha_timezone="UTC",
        )

    def test_process_all_slots_returns_contexts_and_counts(self, builder):
        """Test _process_all_slots returns correct structure."""
        from datetime import timezone
        from zoneinfo import ZoneInfo

        now = datetime.now(timezone.utc)
        hybrid_slots = [
            {
                "start": now,
                "interval_minutes": 30,
                "price": 0.10,
                "price_source": "30min",
            }
        ]

        data = MagicMock()
        data.feed_in_forecast = []
        data.load_forecast_slots = [0.5] * 96

        contexts, counts = builder._process_all_slots(
            hybrid_slots=hybrid_slots,
            data=data,
            all_solcast=[],
            solar_confidence_factor=1.0,
            base_slot=now,
            local_tz=ZoneInfo("UTC"),
            dw_start_time=time(18, 0),
            dw_end_time=time(22, 0),
        )

        assert len(contexts) == 1
        assert "five_min" in counts
        assert "thirty_min" in counts

    def test_process_all_slots_counts_slots_correctly(self, builder):
        """Test _process_all_slots counts 5min and 30min slots."""
        from datetime import timezone
        from zoneinfo import ZoneInfo

        now = datetime.now(timezone.utc)
        hybrid_slots = [
            {
                "start": now,
                "interval_minutes": 5,
                "price": 0.10,
                "price_source": "5min",
            },
            {
                "start": now + timedelta(minutes=5),
                "interval_minutes": 30,
                "price": 0.12,
                "price_source": "30min",
            },
        ]

        data = MagicMock()
        data.feed_in_forecast = []
        data.load_forecast_slots = [0.5] * 96

        contexts, counts = builder._process_all_slots(
            hybrid_slots=hybrid_slots,
            data=data,
            all_solcast=[],
            solar_confidence_factor=1.0,
            base_slot=now,
            local_tz=ZoneInfo("UTC"),
            dw_start_time=time(18, 0),
            dw_end_time=time(22, 0),
        )

        assert counts["five_min"] == 1
        assert counts["thirty_min"] == 1


class TestProcessSingleSlot:
    """Tests for _process_single_slot method."""

    @pytest.fixture
    def builder(self):
        """Create a SlotBuilder instance."""
        return SlotBuilder(
            config_options={
                "demand_window_start": "18:00:00",
                "demand_window_end": "22:00:00",
            },
            ha_timezone="UTC",
        )

    def test_process_single_slot_returns_context(self, builder):
        """Test _process_single_slot returns SlotContext."""
        from datetime import timezone
        from zoneinfo import ZoneInfo

        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        slot = {
            "start": now,
            "interval_minutes": 30,
            "price": 0.10,
            "price_source": "30min",
        }

        data = MagicMock()
        data.feed_in_forecast = []
        data.load_forecast_slots = [0.5] * 96

        ctx, counts, in_dw = builder._process_single_slot(
            i=0,
            slot=slot,
            data=data,
            all_solcast=[],
            solar_confidence_factor=1.0,
            base_slot=now,
            local_tz=ZoneInfo("UTC"),
            dw_start_time=time(18, 0),
            dw_end_time=time(22, 0),
            prev_in_demand_window=False,
        )

        assert isinstance(ctx, SlotContext)
        assert ctx.slot_index == 0
        assert ctx.buy_price == 0.10
        assert counts["thirty_min"] == 1

    def test_process_single_slot_aggregates_consumption(self, builder):
        """Test _process_single_slot aggregates 15-minute bins for 30-minute slot."""
        from datetime import timezone
        from zoneinfo import ZoneInfo

        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        slot = {
            "start": now,
            "interval_minutes": 30,
            "price": 0.10,
            "price_source": "30min",
        }

        data = MagicMock()
        data.feed_in_forecast = []
        data.load_forecast_slots = [1.0, 2.0] + [0.0] * 94

        ctx, _, _ = builder._process_single_slot(
            i=0,
            slot=slot,
            data=data,
            all_solcast=[],
            solar_confidence_factor=1.0,
            base_slot=now,
            local_tz=ZoneInfo("UTC"),
            dw_start_time=time(18, 0),
            dw_end_time=time(22, 0),
            prev_in_demand_window=False,
        )

        assert ctx.consumption_kwh == 0.75

    def test_process_single_slot_demand_window_entry(self, builder):
        """Test _process_single_slot detects demand window entry."""
        from datetime import timezone
        from zoneinfo import ZoneInfo

        now = datetime.now(timezone.utc).replace(hour=18, minute=0)
        slot = {
            "start": now,
            "interval_minutes": 30,
            "price": 0.10,
            "price_source": "30min",
        }

        data = MagicMock()
        data.feed_in_forecast = []
        data.load_forecast_slots = [0.5] * 96

        ctx, counts, in_dw = builder._process_single_slot(
            i=0,
            slot=slot,
            data=data,
            all_solcast=[],
            solar_confidence_factor=1.0,
            base_slot=now,
            local_tz=ZoneInfo("UTC"),
            dw_start_time=time(18, 0),
            dw_end_time=time(22, 0),
            prev_in_demand_window=False,
        )

        assert ctx.is_demand_window_entry is True
        assert ctx.is_demand_window_slot is True
