"""Tests for AccuracyMetricsStore (Issue #706, TDD RED phase)."""

from unittest.mock import MagicMock, AsyncMock, patch
import pytest

from custom_components.localshift.forecast.accuracy_store import AccuracyMetricsStore
from custom_components.localshift.coordinator.data import CoordinatorData


@pytest.fixture
def mock_hass():
    """Create mock HomeAssistant instance."""
    return MagicMock()


@pytest.fixture
def store(mock_hass):
    """Create AccuracyMetricsStore instance."""
    return AccuracyMetricsStore(mock_hass)


@pytest.fixture
def data():
    """Create CoordinatorData instance."""
    return CoordinatorData()


class TestAsyncInitialize:
    """Tests for async_initialize method."""

    @pytest.mark.asyncio
    async def test_initialize_success(self, store, mock_hass):
        """Store created with hass, version=1, key."""
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
        """Exception during init → _store = None."""
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
        """Skip load when _store is None — noop."""
        store._store = None
        original_error = data.forecast_error_soc_15min

        await store.async_load(data)

        assert data.forecast_error_soc_15min == original_error

    @pytest.mark.asyncio
    async def test_load_empty_data(self, store, data):
        """Store returns None → noop."""
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value=None)

        await store.async_load(data)

        # All scalar fields remain at defaults
        assert data.forecast_error_soc_15min == 0.0
        assert data.forecast_comparisons_made == 0

    @pytest.mark.asyncio
    async def test_load_non_dict_data(self, store, data):
        """Store returns non-dict → noop."""
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value="not a dict")

        await store.async_load(data)

        assert data.forecast_error_soc_15min == 0.0
        assert data.forecast_comparisons_made == 0

    @pytest.mark.asyncio
    async def test_load_restores_scalars(self, store, data):
        """All 9 scalar fields restored correctly from stored data."""
        store._store = MagicMock()
        store._store.async_load = AsyncMock(
            return_value={
                "forecast_error_soc_15min": 1.5,
                "forecast_error_soc_1h": 2.5,
                "forecast_error_soc_4h": 3.5,
                "forecast_accuracy_soc_15min": 95.0,
                "forecast_accuracy_soc_1h": 90.0,
                "forecast_accuracy_soc_4h": 85.0,
                "forecast_error_buy_price_1h": 0.02,
                "forecast_error_sell_price_1h": 0.03,
                "forecast_comparisons_made": 42,
            }
        )

        await store.async_load(data)

        assert data.forecast_error_soc_15min == 1.5
        assert data.forecast_error_soc_1h == 2.5
        assert data.forecast_error_soc_4h == 3.5
        assert data.forecast_accuracy_soc_15min == 95.0
        assert data.forecast_accuracy_soc_1h == 90.0
        assert data.forecast_accuracy_soc_4h == 85.0
        assert data.forecast_error_buy_price_1h == 0.02
        assert data.forecast_error_sell_price_1h == 0.03
        assert data.forecast_comparisons_made == 42

    @pytest.mark.asyncio
    async def test_load_legacy_extended_key_is_harmless(self, store, data):
        """Issue #868: legacy blobs still carry the removed extended_accuracy_metrics
        key — async_load is .get()-based and must ignore it without error."""
        store._store = MagicMock()
        store._store.async_load = AsyncMock(
            return_value={
                "forecast_error_soc_15min": 1.5,
                "forecast_comparisons_made": 7,
                # Stale key from the removed Issue #270 stack — must be ignored.
                "extended_accuracy_metrics": {"accuracy_24h": 95.0},
            }
        )

        await store.async_load(data)

        # The live scalar fields still load; the stale key does not raise or set
        # any attribute.
        assert data.forecast_error_soc_15min == 1.5
        assert data.forecast_comparisons_made == 7
        assert not hasattr(data, "extended_accuracy_metrics")

    @pytest.mark.asyncio
    async def test_load_missing_keys_use_defaults(self, store, data):
        """Missing keys in stored data → None/0 defaults."""
        store._store = MagicMock()
        store._store.async_load = AsyncMock(return_value={})

        await store.async_load(data)

        # Fields should remain at CoordinatorData defaults
        assert data.forecast_error_soc_15min == 0.0
        assert data.forecast_error_soc_1h == 0.0
        assert data.forecast_error_soc_4h == 0.0
        assert data.forecast_accuracy_soc_15min is None
        assert data.forecast_accuracy_soc_1h is None
        assert data.forecast_accuracy_soc_4h is None
        assert data.forecast_error_buy_price_1h == 0.0
        assert data.forecast_error_sell_price_1h == 0.0
        assert data.forecast_comparisons_made == 0

    @pytest.mark.asyncio
    async def test_load_exception(self, store, data):
        """Exception during async_load → noop."""
        store._store = MagicMock()
        store._store.async_load = AsyncMock(side_effect=Exception("Load error"))

        await store.async_load(data)

        # Data should remain unchanged (defaults)
        assert data.forecast_error_soc_15min == 0.0
        assert data.forecast_comparisons_made == 0


class TestAsyncSave:
    """Tests for async_save method."""

    @pytest.mark.asyncio
    async def test_save_no_store(self, store, data):
        """_store=None → noop, no exception."""
        store._store = None

        # Should not raise
        await store.async_save(data)

    @pytest.mark.asyncio
    async def test_save_writes_scalars(self, store, data):
        """All 9 scalar fields written to storage."""
        store._store = MagicMock()
        store._store.async_save = AsyncMock()

        data.forecast_error_soc_15min = 1.5
        data.forecast_error_soc_1h = 2.5
        data.forecast_error_soc_4h = 3.5
        data.forecast_accuracy_soc_15min = 95.0
        data.forecast_accuracy_soc_1h = 90.0
        data.forecast_accuracy_soc_4h = 85.0
        data.forecast_error_buy_price_1h = 0.02
        data.forecast_error_sell_price_1h = 0.03
        data.forecast_comparisons_made = 42

        await store.async_save(data)

        store._store.async_save.assert_called_once()
        saved = store._store.async_save.call_args[0][0]

        assert saved["forecast_error_soc_15min"] == 1.5
        assert saved["forecast_error_soc_1h"] == 2.5
        assert saved["forecast_error_soc_4h"] == 3.5
        assert saved["forecast_accuracy_soc_15min"] == 95.0
        assert saved["forecast_accuracy_soc_1h"] == 90.0
        assert saved["forecast_accuracy_soc_4h"] == 85.0
        assert saved["forecast_error_buy_price_1h"] == 0.02
        assert saved["forecast_error_sell_price_1h"] == 0.03
        assert saved["forecast_comparisons_made"] == 42

    @pytest.mark.asyncio
    async def test_save_omits_extended_metrics_key(self, store, data):
        """Issue #868: the removed extended_accuracy_metrics key is no longer written."""
        store._store = MagicMock()
        store._store.async_save = AsyncMock()

        await store.async_save(data)

        saved = store._store.async_save.call_args[0][0]
        assert "extended_accuracy_metrics" not in saved

    @pytest.mark.asyncio
    async def test_save_exception(self, store, data):
        """Exception during async_save → noop, no propagation."""
        store._store = MagicMock()
        store._store.async_save = AsyncMock(side_effect=Exception("Save error"))

        # Should not raise
        await store.async_save(data)
