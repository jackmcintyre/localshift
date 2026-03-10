"""Unit tests for ForecastHistoryStore."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.computation_engine_lib.forecast_history_store import (
    ForecastHistoryStore,
)
from custom_components.localshift.coordinator import CoordinatorData


def test_store_forecast_history_appends_entries():
    """Store should append prediction entries once per hour."""
    hass = MagicMock()
    store = ForecastHistoryStore(hass)
    data = CoordinatorData()
    data.forecast_history = []
    # Create slots that will match the target offsets (15, 60, 240 minutes)
    # Slot 1: 10:00-10:30 covers target at 10:15
    # Slot 2: 11:00-11:30 covers target at 11:00 (60 min offset)
    # Slot 3: 14:00-14:30 covers target at 14:00 (240 min offset)
    data.optimizer_decisions = [
        {
            "timestamp_iso": "2026-02-16T10:00:00+00:00",
            "predicted_soc_pct": 75,
            "slot_interval_minutes": 30,
            "buy_price": 0.2,
            "sell_price": 0.05,
        },
        {
            "timestamp_iso": "2026-02-16T11:00:00+00:00",
            "predicted_soc_pct": 80,
            "slot_interval_minutes": 30,
            "buy_price": 0.18,
            "sell_price": 0.04,
        },
        {
            "timestamp_iso": "2026-02-16T14:00:00+00:00",
            "predicted_soc_pct": 85,
            "slot_interval_minutes": 30,
            "buy_price": 0.15,
            "sell_price": 0.03,
        },
    ]

    now_dt = datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC)
    store.store_forecast_history(data, now_dt)

    assert len(data.forecast_history) == 3

    # Second call should not add more (hour hasn't changed)
    store.store_forecast_history(data, now_dt)
    assert len(data.forecast_history) == 3


@pytest.mark.asyncio
async def test_async_load_filters_old_entries():
    """Load should filter out entries older than the cutoff window."""
    hass = MagicMock()
    store = ForecastHistoryStore(hass)
    store._store = MagicMock()

    now_dt = datetime(2026, 2, 16, 12, 0, 0, tzinfo=UTC)
    older = (now_dt - timedelta(hours=6)).isoformat()
    recent = (now_dt - timedelta(hours=1)).isoformat()

    store._store.async_load = AsyncMock(
        return_value={
            "forecast_history": [
                {"target_time": older, "prediction_time": older},
                {"target_time": recent, "prediction_time": recent},
            ],
            "first_prediction_time": "",
        }
    )

    data = CoordinatorData()
    data.forecast_history = []

    with patch("homeassistant.util.dt.now", return_value=now_dt):
        await store.async_load(data)

    assert len(data.forecast_history) == 1
    assert data.forecast_history_count == 1
