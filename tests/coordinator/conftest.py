"""Shared fixtures for coordinator tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.coordinator import LocalShiftCoordinator


@pytest.fixture
def mock_hass_with_services():
    """Create a mock Home Assistant instance with services."""
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.states = MagicMock()
    # async_create_task receives coroutines - close them to avoid
    # "coroutine was never awaited" RuntimeWarnings
    hass.async_create_task = MagicMock(
        side_effect=lambda coro, name=None: coro.close()
        if hasattr(coro, "close")
        else None
    )
    return hass


@pytest.fixture
def coordinator(mock_hass_with_services, mock_entry):
    """Create a LocalShiftCoordinator instance."""
    from custom_components.localshift.coordinator.tick_scheduler import TickScheduler

    coord = LocalShiftCoordinator(mock_hass_with_services, mock_entry)
    # Initialize tick_scheduler for tests that don't call async_start
    coord._tick_scheduler = TickScheduler(coord)
    return coord
