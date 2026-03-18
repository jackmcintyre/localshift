"""Tests for LocalShiftSensorBase class.

Tests cover:
- Initialization
- device_info property
- async_added_to_hass lifecycle
- async_will_remove_from_hass cleanup
- _handle_coordinator_update callback
"""

from unittest.mock import MagicMock

import pytest

from custom_components.localshift.sensors.base import LocalShiftSensorBase


class ConcreteSensor(LocalShiftSensorBase):
    """Concrete implementation for testing."""

    _attr_unique_id = "test_sensor"
    _attr_name = "Test Sensor"

    def _update_from_coordinator(self) -> None:
        """Test implementation."""
        self._attr_native_value = "test_value"


class TestLocalShiftSensorBase:
    """Tests for LocalShiftSensorBase."""

    def test_init(self):
        """Test sensor initialization."""
        mock_coordinator = MagicMock()
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_id"

        sensor = ConcreteSensor(mock_coordinator, mock_entry)

        assert sensor.coordinator is mock_coordinator
        assert sensor._entry is mock_entry
        assert sensor._unsub is None
        assert sensor._attr_has_entity_name is True

    def test_device_info(self):
        """Test device_info property returns correct DeviceInfo."""
        mock_coordinator = MagicMock()
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_123"

        sensor = ConcreteSensor(mock_coordinator, mock_entry)
        device_info = sensor.device_info

        assert device_info["identifiers"] == {("localshift", "test_entry_123")}
        assert device_info["name"] == "LocalShift"
        assert device_info["manufacturer"] == "Custom"
        assert device_info["model"] == "Solar Battery Automation"
        assert device_info["sw_version"] == "0.0.2"

    @pytest.mark.asyncio
    async def test_async_added_to_hass(self):
        """Test async_added_to_hass registers listener and calls update."""
        mock_coordinator = MagicMock()
        mock_coordinator.async_add_listener = MagicMock(return_value="unsubscribe_fn")
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry_id"

        sensor = ConcreteSensor(mock_coordinator, mock_entry)
        sensor.hass = MagicMock()

        await sensor.async_added_to_hass()

        # Verify listener was registered
        mock_coordinator.async_add_listener.assert_called_once()
        assert sensor._unsub == "unsubscribe_fn"

    @pytest.mark.asyncio
    async def test_async_will_remove_from_hass_with_unsub(self):
        """Test async_will_remove_from_hass calls unsubscribe if set."""
        mock_coordinator = MagicMock()
        mock_entry = MagicMock()

        sensor = ConcreteSensor(mock_coordinator, mock_entry)
        sensor._unsub = MagicMock()

        await sensor.async_will_remove_from_hass()

        sensor._unsub.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_will_remove_from_hass_without_unsub(self):
        """Test async_will_remove_from_hass does nothing if no unsub."""
        mock_coordinator = MagicMock()
        mock_entry = MagicMock()

        sensor = ConcreteSensor(mock_coordinator, mock_entry)
        sensor._unsub = None

        # Should not raise
        await sensor.async_will_remove_from_hass()

    def test_handle_coordinator_update(self):
        """Test _handle_coordinator_update calls update and writes state."""
        mock_coordinator = MagicMock()
        mock_entry = MagicMock()

        sensor = ConcreteSensor(mock_coordinator, mock_entry)
        sensor.hass = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_coordinator_update()

        # Verify native_value was set by _update_from_coordinator
        assert sensor._attr_native_value == "test_value"
        sensor.async_write_ha_state.assert_called_once()

    def test_update_from_coordinator_override(self):
        """Test that subclasses can override _update_from_coordinator."""
        mock_coordinator = MagicMock()
        mock_entry = MagicMock()

        class CustomSensor(LocalShiftSensorBase):
            _attr_unique_id = "custom"
            _attr_name = "Custom"

            def _update_from_coordinator(self) -> None:
                self._attr_native_value = 42

        sensor = CustomSensor(mock_coordinator, mock_entry)
        sensor._update_from_coordinator()

        assert sensor._attr_native_value == 42
