"""Tests for EntityMonitor class."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from custom_components.localshift.coordinator.entity_monitor import EntityMonitor


class TestEntityMonitorInitialization:
    """Test EntityMonitor initialization."""

    def test_entity_monitor_initialization(self) -> None:
        """Test EntityMonitor initializes with coordinator reference."""
        # ARRANGE
        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()

        # ACT
        monitor = EntityMonitor(mock_coordinator)

        # ASSERT
        assert monitor._coordinator is mock_coordinator
        assert monitor._hass is mock_coordinator.hass
