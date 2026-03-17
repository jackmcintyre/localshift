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


class TestReadAllExternalState:
    """Test read_all_external_state method."""

    def test_read_all_external_state_with_state_reader(self) -> None:
        """Test reading external state when state_reader exists."""
        # ARRANGE
        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_coordinator._state_reader = MagicMock()
        mock_coordinator.data = MagicMock()
        monitor = EntityMonitor(mock_coordinator)

        # ACT
        monitor.read_all_external_state()

        # ASSERT
        mock_coordinator._state_reader.read_all_external_state.assert_called_once_with(
            mock_coordinator.data
        )

    def test_read_all_external_state_without_state_reader(self) -> None:
        """Test reading external state when state_reader is None."""
        # ARRANGE
        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_coordinator._state_reader = None
        monitor = EntityMonitor(mock_coordinator)

        # ACT
        monitor.read_all_external_state()

        # ASSERT - should return early without error
        # No assertions needed, test passes if no exception raised


class TestCheckEntityHealth:
    """Test check_entity_health method."""

    def test_check_entity_health_without_validator(self) -> None:
        """Test check_entity_health when entity_validator is None."""
        # ARRANGE
        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_coordinator._entity_validator = None
        monitor = EntityMonitor(mock_coordinator)

        # ACT
        monitor.check_entity_health()

        # ASSERT - should return early without error
        # No assertions needed, test passes if no exception raised

    def test_check_entity_health_with_validator(self) -> None:
        """Test check_entity_health with entity_validator present."""
        # ARRANGE
        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_validator = MagicMock()
        mock_validator.status.value = "healthy"
        mock_validator.errors = []
        mock_validator.warnings = []
        mock_validator.get_user_friendly_message.return_value = (
            "All systems operational"
        )
        mock_validator.get_required_entities_status.return_value = {
            "battery": True,
            "grid": True,
        }
        mock_validator.get_health_summary.return_value = {
            "entities": {"battery": "ok", "grid": "ok"},
            "last_check": "2024-01-01T00:00:00",
        }
        mock_validator.check_all_localshift_entities.return_value = {
            "sensor.localshift_soc": "available"
        }

        mock_coordinator._entity_validator = mock_validator
        mock_data = MagicMock()
        mock_coordinator.data = mock_data
        monitor = EntityMonitor(mock_coordinator)

        # ACT
        monitor.check_entity_health()

        # ASSERT - validator methods called
        mock_validator.check_all_entities.assert_called_once()
        mock_validator.get_user_friendly_message.assert_called_once()
        mock_validator.get_required_entities_status.assert_called_once()
        mock_validator.get_health_summary.assert_called_once()
        mock_validator.check_all_localshift_entities.assert_called_once()

        # ASSERT - data updated correctly
        assert mock_data.integration_status == "healthy"
        assert mock_data.integration_status_message == "All systems operational"
        assert mock_data.entity_errors == []
        assert mock_data.entity_warnings == []
        assert mock_data.required_entities_healthy is True
        assert mock_data.entity_health == {"battery": "ok", "grid": "ok"}
        assert mock_data.last_entity_check == "2024-01-01T00:00:00"
        assert mock_data.localshift_entity_health == {
            "sensor.localshift_soc": "available"
        }

    def test_check_entity_health_with_errors(self) -> None:
        """Test check_entity_health logs errors when present."""
        # ARRANGE
        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_validator = MagicMock()
        mock_validator.status.value = "degraded"
        mock_validator.errors = ["Battery sensor unavailable"]
        mock_validator.warnings = []
        mock_validator.get_user_friendly_message.return_value = (
            "Some entities unavailable"
        )
        mock_validator.get_required_entities_status.return_value = {
            "battery": False,
            "grid": True,
        }
        mock_validator.get_health_summary.return_value = {
            "entities": {"battery": "unavailable", "grid": "ok"},
            "last_check": "2024-01-01T00:00:00",
        }
        mock_validator.check_all_localshift_entities.return_value = {}

        mock_coordinator._entity_validator = mock_validator
        mock_data = MagicMock()
        mock_coordinator.data = mock_data
        monitor = EntityMonitor(mock_coordinator)

        # ACT
        monitor.check_entity_health()

        # ASSERT
        assert mock_data.integration_status == "degraded"
        assert mock_data.entity_errors == ["Battery sensor unavailable"]
        assert mock_data.required_entities_healthy is False

    def test_check_entity_health_with_warnings(self) -> None:
        """Test check_entity_health logs warnings when present."""
        # ARRANGE
        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_validator = MagicMock()
        mock_validator.status.value = "healthy"
        mock_validator.errors = []
        mock_validator.warnings = ["Solar sensor data is stale"]
        mock_validator.get_user_friendly_message.return_value = (
            "All systems operational"
        )
        mock_validator.get_required_entities_status.return_value = {
            "battery": True,
            "grid": True,
        }
        mock_validator.get_health_summary.return_value = {
            "entities": {"battery": "ok", "grid": "ok", "solar": "stale"},
            "last_check": "2024-01-01T00:00:00",
        }
        mock_validator.check_all_localshift_entities.return_value = {}

        mock_coordinator._entity_validator = mock_validator
        mock_data = MagicMock()
        mock_coordinator.data = mock_data
        monitor = EntityMonitor(mock_coordinator)

        # ACT
        monitor.check_entity_health()

        # ASSERT
        assert mock_data.integration_status == "healthy"
        assert mock_data.entity_warnings == ["Solar sensor data is stale"]
        assert mock_data.required_entities_healthy is True

    def test_check_entity_health_all_required_false(self) -> None:
        """Test check_entity_health when all required entities are not healthy."""
        # ARRANGE
        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_validator = MagicMock()
        mock_validator.status.value = "error"
        mock_validator.errors = [
            "Battery sensor unavailable",
            "Grid sensor unavailable",
        ]
        mock_validator.warnings = []
        mock_validator.get_user_friendly_message.return_value = (
            "Critical entities unavailable"
        )
        mock_validator.get_required_entities_status.return_value = {
            "battery": False,
            "grid": False,
        }
        mock_validator.get_health_summary.return_value = {
            "entities": {},
            "last_check": "2024-01-01T00:00:00",
        }
        mock_validator.check_all_localshift_entities.return_value = {}

        mock_coordinator._entity_validator = mock_validator
        mock_data = MagicMock()
        mock_coordinator.data = mock_data
        monitor = EntityMonitor(mock_coordinator)

        # ACT
        monitor.check_entity_health()

        # ASSERT
        assert mock_data.required_entities_healthy is False
