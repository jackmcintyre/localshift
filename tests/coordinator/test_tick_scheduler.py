"""Tests for TickScheduler."""

from __future__ import annotations

from custom_components.localshift.coordinator.tick_scheduler import TickScheduler


async def test_tick_scheduler_initialization(coordinator):
    """Test TickScheduler can be instantiated."""
    scheduler = TickScheduler(coordinator)

    assert scheduler is not None
    assert scheduler._coordinator is coordinator
