"""Tests for integration setup and option updates."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift import _async_options_updated


@pytest.mark.asyncio
async def test_options_update_invalidates_charge_rate_curves(mock_hass):
    """Options update triggers charge rate curve invalidation."""
    entry = MagicMock()
    coordinator = MagicMock()
    coordinator.async_invalidate_charge_rate_curves = AsyncMock()
    coordinator.reschedule_daily_summary_timer = MagicMock()
    coordinator.reset_entity_tracking_on_options_change = MagicMock()
    coordinator.async_recompute_and_evaluate = AsyncMock()
    entry.runtime_data = coordinator

    await _async_options_updated(mock_hass, entry)

    coordinator.async_invalidate_charge_rate_curves.assert_awaited_once()
