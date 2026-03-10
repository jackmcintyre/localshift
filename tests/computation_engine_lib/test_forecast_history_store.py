"""Tests for ForecastHistoryStore refactoring (Issue #581)."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

from homeassistant.util import dt as dt_util

from custom_components.localshift.computation_engine_lib.forecast_history_store import (
    ForecastHistoryStore,
)
from custom_components.localshift.coordinator import CoordinatorData


@pytest.fixture
def mock_hass():
    """Create mock HomeAssistant instance."""
    return MagicMock()


@pytest.fixture
def store(mock_hass):
    """Create ForecastHistoryStore instance."""
    return ForecastHistoryStore(mock_hass)


@pytest.fixture
def data():
    """Create CoordinatorData instance."""
    return CoordinatorData()


class TestAsyncInitialize:
    """Tests for async_initialize method."""

    @pytest.mark.asyncio
    async def test_initialize_success(self, store, mock_hass):
        """Initialize storage successfully."""
        with patch(
            "homeassistant.helpers.storage.Store",
        ) as mock_store_class:
            mock_store_instance = MagicMock()
            mock_store_class.return_value = mock_store_instance

            await store.async_initialize()

            assert store._store is not None
            mock_store_class.assert_called_once_with(mock_hass, 1, store._store_key)

    @pytest.mark.asyncio
    async def test_initialize_failure(self, store):
        """Handle initialization failure gracefully."""
        with patch(
            "homeassistant.helpers.storage.Store",
            side_effect=Exception("Storage error"),
        ):
            await store.async_initialize()

            assert store._store is None


class TestAsyncLoad:
    """Tests for async_load method."""

    @pytest.mark.asyncio
    async def test_load_no_store(self, store, data):
        """Skip load when store is None."""
        store._store = None
        await store.async_load(data)
        assert len(data.forecast_history) == 0

    @pytest.mark.asyncio
    async def test_load_empty_data(self, store, data):
        """Handle empty stored data."""
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value=None)

        await store.async_load(data)

        assert len(data.forecast_history) == 0

    @pytest.mark.asyncio
    async def test_load_non_dict_data(self, store, data):
        """Handle non-dict stored data."""
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value="not a dict")

        await store.async_load(data)

        assert len(data.forecast_history) == 0

    @pytest.mark.asyncio
    async def test_load_valid_history(self, store, data):
        """Load valid history entries."""
        now_dt = dt_util.now()
        store._store = MagicMock()
        store._store.async_load = AsyncMock(
            return_value={
                "forecast_history": [
                    {
                        "target_time": (now_dt + timedelta(hours=1)).isoformat(),
                        "offset_minutes": 60,
                        "predicted_soc": 80,
                    }
                ],
                "first_prediction_time": now_dt.isoformat(),
            }
        )

        await store.async_load(data)

        assert len(data.forecast_history) == 1
        assert data.forecast_first_prediction_time == now_dt.isoformat()

    @pytest.mark.asyncio
    async def test_load_filters_old_entries(self, store, data):
        """Filter out entries older than 4 hours."""
        now_dt = dt_util.now()
        store._store = MagicMock()
        store._store.async_load = AsyncMock(
            return_value={
                "forecast_history": [
                    {
                        "target_time": (now_dt - timedelta(hours=5)).isoformat(),
                        "offset_minutes": 60,
                    },
                    {
                        "target_time": (now_dt + timedelta(hours=1)).isoformat(),
                        "offset_minutes": 60,
                    },
                ],
                "first_prediction_time": "",
            }
        )

        await store.async_load(data)

        assert len(data.forecast_history) == 1

    @pytest.mark.asyncio
    async def test_load_exception(self, store, data):
        """Handle load exception gracefully."""
        store._store = MagicMock()
        store._store.async_load = AsyncMock(side_effect=Exception("Load error"))

        await store.async_load(data)

        assert len(data.forecast_history) == 0

    @pytest.mark.asyncio
    async def test_load_empty_data(self, store, data):
        """Handle empty stored data."""
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value=None)

        await store.async_load(data)

        assert len(data.forecast_history) == 0

    @pytest.mark.asyncio
    async def test_load_valid_history(self, store, data):
        """Load valid history entries."""
        now_dt = dt_util.now()
        store._store = MagicMock()
        store._store.async_load = AsyncMock(
            return_value={
                "forecast_history": [
                    {
                        "target_time": (now_dt + timedelta(hours=1)).isoformat(),
                        "offset_minutes": 60,
                        "predicted_soc": 80,
                    }
                ],
                "first_prediction_time": now_dt.isoformat(),
            }
        )

        await store.async_load(data)

        assert len(data.forecast_history) == 1
        assert data.forecast_first_prediction_time == now_dt.isoformat()


class TestAsyncSave:
    """Tests for async_save method."""

    @pytest.mark.asyncio
    async def test_save_exception(self, store, data):
        """Handle save exception gracefully."""
        store._store = MagicMock()
        store._store.async_save = AsyncMock(side_effect=Exception("Save error"))
        data.forecast_history = [
            {"target_time": "2024-01-01T12:00:00", "offset_minutes": 15}
        ]

        await store.async_save(data)

    @pytest.mark.asyncio
    async def test_save_filters_entries(self, store, data):
        """Save only entries with required fields."""
        store._store = MagicMock()
        store._store.async_save = AsyncMock()
        data.forecast_history = [
            {"target_time": "2024-01-01T12:00:00", "offset_minutes": 15},
            {"prediction_time": "2024-01-01T11:00:00"},
        ]

        await store.async_save(data)

        call_args = store._store.async_save.call_args[0][0]
        assert len(call_args["forecast_history"]) == 1

    @pytest.mark.asyncio
    async def test_save_limits_to_100(self, store, data):
        """Limit saved entries to 100."""
        store._store = MagicMock()
        store._store.async_save = AsyncMock()
        for i in range(150):
            data.forecast_history.append({
                "target_time": f"2024-01-01T{i:02d}:00:00",
                "offset_minutes": i,
            })

        await store.async_save(data)

        call_args = store._store.async_save.call_args[0][0]
        assert len(call_args["forecast_history"]) == 100


class TestLocalizeDatetime:
    """Tests for _localize_datetime helper."""

    def test_naive_datetime(self, store):
        """Convert naive datetime to local timezone."""
        naive_dt = datetime(2024, 1, 1, 12, 0, 0)
        result = store._localize_datetime(naive_dt)

        assert result.tzinfo is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 1
        assert result.hour == 12

    def test_aware_datetime(self, store):
        """Convert aware datetime to local timezone."""
        utc_dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=dt_util.UTC)
        result = store._localize_datetime(utc_dt)

        assert result.tzinfo is not None
        local_dt = dt_util.as_local(utc_dt)
        assert result.hour == local_dt.hour


class TestMatchSlotToTarget:
    """Tests for _match_slot_to_target helper."""

    def test_match_creates_entry(self, store):
        """Create history entry when slot matches target time."""
        now_dt = dt_util.now()
        target_dt = now_dt + timedelta(minutes=15)

        slot = {
            "timestamp_iso": (now_dt + timedelta(minutes=10)).isoformat(),
            "slot_interval_minutes": 15,
            "predicted_soc_pct": 80,
            "buy_price": 0.10,
            "sell_price": 0.05,
        }

        entry = store._match_slot_to_target(slot, target_dt, now_dt, 15)

        assert entry is not None
        assert entry["prediction_time"] == now_dt.isoformat()
        assert entry["target_time"] == target_dt.isoformat()
        assert entry["offset_minutes"] == 15
        assert entry["predicted_soc"] == 80
        assert entry["predicted_buy_price"] == 0.10
        assert entry["predicted_sell_price"] == 0.05

    def test_no_match_returns_none(self, store):
        """Return None when slot doesn't match target time."""
        now_dt = dt_util.now()
        target_dt = now_dt + timedelta(minutes=15)

        slot = {
            "timestamp_iso": (now_dt + timedelta(hours=2)).isoformat(),
            "slot_interval_minutes": 15,
            "predicted_soc_pct": 80,
        }

        entry = store._match_slot_to_target(slot, target_dt, now_dt, 15)

        assert entry is None

    def test_invalid_timestamp_returns_none(self, store):
        """Return None when slot has invalid timestamp."""
        now_dt = dt_util.now()
        target_dt = now_dt + timedelta(minutes=15)

        slot = {
            "timestamp_iso": "invalid-iso-format",
            "slot_interval_minutes": 15,
            "predicted_soc_pct": 80,
        }

        entry = store._match_slot_to_target(slot, target_dt, now_dt, 15)

        assert entry is None

    def test_missing_timestamp_returns_none(self, store):
        """Return None when slot has no timestamp."""
        now_dt = dt_util.now()
        target_dt = now_dt + timedelta(minutes=15)

        slot = {
            "slot_interval_minutes": 15,
            "predicted_soc_pct": 80,
        }

        entry = store._match_slot_to_target(slot, target_dt, now_dt, 15)

        assert entry is None


class TestStoreForecastHistory:
    """Tests for store_forecast_history method."""

    def test_basic_storage(self, store, data):
        """Store entries at 15/60/240 min offsets."""
        now_dt = dt_util.now()

        # Slots must INCLUDE the target times (condition: slot_dt <= target < slot_end)
        # For 15min target: slot starts at now+5min, covers [now+5, now+20) which includes now+15
        slot_15min = {
            "timestamp_iso": (now_dt + timedelta(minutes=5)).isoformat(),
            "slot_interval_minutes": 15,
            "predicted_soc_pct": 80,
            "buy_price": 0.10,
            "sell_price": 0.05,
        }

        # For 60min target: slot starts at now+50min, covers [now+50, now+65) which includes now+60
        slot_60min = {
            "timestamp_iso": (now_dt + timedelta(minutes=50)).isoformat(),
            "slot_interval_minutes": 15,
            "predicted_soc_pct": 75,
            "buy_price": 0.12,
            "sell_price": 0.06,
        }

        # For 240min target: slot starts at now+230min, covers [now+230, now+245) which includes now+240
        slot_240min = {
            "timestamp_iso": (now_dt + timedelta(minutes=230)).isoformat(),
            "slot_interval_minutes": 15,
            "predicted_soc_pct": 70,
            "buy_price": 0.15,
            "sell_price": 0.07,
        }

        data.optimizer_decisions = [slot_15min, slot_60min, slot_240min]

        store.store_forecast_history(data, now_dt)

        assert len(data.forecast_history) == 3
        assert data.forecast_history_count == 3

        offsets = [entry["offset_minutes"] for entry in data.forecast_history]
        assert 15 in offsets
        assert 60 in offsets
        assert 240 in offsets

    def test_skips_same_hour(self, store, data):
        """Don't store twice in same hour."""
        now_dt = dt_util.now()

        data.optimizer_decisions = [
            {
                "timestamp_iso": now_dt.isoformat(),
                "slot_interval_minutes": 15,
                "predicted_soc_pct": 80,
            },
        ]

        store.store_forecast_history(data, now_dt)
        initial_count = len(data.forecast_history)

        later_dt = now_dt + timedelta(minutes=30)
        store.store_forecast_history(data, later_dt)

        assert len(data.forecast_history) == initial_count

    def test_handles_empty_slots(self, store, data):
        """Gracefully handle no slots."""
        now_dt = dt_util.now()

        data.optimizer_decisions = []
        store.store_forecast_history(data, now_dt)

        assert len(data.forecast_history) == 0

    def test_limits_to_200(self, store, data):
        """Trim history to 200 entries."""
        now_dt = dt_util.now()

        for i in range(250):
            entry = {
                "prediction_time": now_dt.isoformat(),
                "target_time": (now_dt + timedelta(minutes=i)).isoformat(),
                "offset_minutes": i,
                "predicted_soc": 80,
                "predicted_buy_price": 0.10,
                "predicted_sell_price": 0.05,
            }
            data.forecast_history.append(entry)

        data.optimizer_decisions = [
            {
                "timestamp_iso": now_dt.isoformat(),
                "slot_interval_minutes": 15,
                "predicted_soc_pct": 90,
            },
        ]

        store.store_forecast_history(data, now_dt)

        assert len(data.forecast_history) <= 200
