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


class TestResetEntityTrackingOnOptionsChange:
    """Test reset_entity_tracking_on_options_change method."""

    def test_reset_with_validator(self) -> None:
        """Test reset entity tracking when validator exists."""
        # ARRANGE
        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_validator = MagicMock()
        mock_coordinator._entity_validator = mock_validator
        monitor = EntityMonitor(mock_coordinator)

        # ACT
        monitor.reset_entity_tracking_on_options_change()

        # ASSERT
        mock_validator.reset_entity_tracking.assert_called_once()

    def test_reset_without_validator(self) -> None:
        """Test reset entity tracking when validator is None."""
        # ARRANGE
        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_coordinator._entity_validator = None
        monitor = EntityMonitor(mock_coordinator)

        # ACT
        monitor.reset_entity_tracking_on_options_change()

        # ASSERT - should return early without error
        # No assertions needed, test passes if no exception raised


class TestRefreshWeatherForecast:
    """Test refresh_weather_forecast method."""

    @pytest.mark.asyncio
    async def test_refresh_success(self) -> None:
        """Test successful weather forecast refresh."""
        # ARRANGE
        from unittest.mock import AsyncMock

        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_coordinator._computation_engine = MagicMock()
        mock_coordinator.data = MagicMock()
        mock_coordinator.data.weather_temperature_forecast = {}

        # Mock forecast data
        mock_forecast = MagicMock()
        mock_forecast.slot_time.hour = 14
        mock_forecast.temperature = 25.5
        mock_coordinator._computation_engine.async_refresh_weather_forecast = AsyncMock(
            return_value=[mock_forecast]
        )

        monitor = EntityMonitor(mock_coordinator)

        # ACT
        await monitor.refresh_weather_forecast()

        # ASSERT
        mock_coordinator._computation_engine.async_refresh_weather_forecast.assert_called_once()
        assert mock_coordinator.data.weather_temperature_forecast == {14: 25.5}

    @pytest.mark.asyncio
    async def test_refresh_without_computation_engine(self) -> None:
        """Test weather forecast refresh when computation engine is None."""
        # ARRANGE
        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_coordinator._computation_engine = None
        monitor = EntityMonitor(mock_coordinator)

        # ACT
        await monitor.refresh_weather_forecast()

        # ASSERT - should return early without error
        # No assertions needed, test passes if no exception raised

    @pytest.mark.asyncio
    async def test_refresh_no_forecasts(self) -> None:
        """Test weather forecast refresh when no forecasts returned."""
        # ARRANGE
        from unittest.mock import AsyncMock

        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_coordinator._computation_engine = MagicMock()
        mock_coordinator.data = MagicMock()
        mock_coordinator.data.weather_temperature_forecast = {}
        mock_coordinator._computation_engine.async_refresh_weather_forecast = AsyncMock(
            return_value=None
        )

        monitor = EntityMonitor(mock_coordinator)

        # ACT
        await monitor.refresh_weather_forecast()

        # ASSERT - data should not be updated
        assert mock_coordinator.data.weather_temperature_forecast == {}

    @pytest.mark.asyncio
    async def test_refresh_multiple_forecasts(self) -> None:
        """Test weather forecast refresh with multiple hours."""
        # ARRANGE
        from unittest.mock import AsyncMock

        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_coordinator._computation_engine = MagicMock()
        mock_coordinator.data = MagicMock()
        mock_coordinator.data.weather_temperature_forecast = {}

        # Mock multiple forecast entries
        forecasts = []
        for hour, temp in [(10, 20.0), (11, 21.5), (12, 23.0)]:
            mock_forecast = MagicMock()
            mock_forecast.slot_time.hour = hour
            mock_forecast.temperature = temp
            forecasts.append(mock_forecast)

        mock_coordinator._computation_engine.async_refresh_weather_forecast = AsyncMock(
            return_value=forecasts
        )

        monitor = EntityMonitor(mock_coordinator)

        # ACT
        await monitor.refresh_weather_forecast()

        # ASSERT
        assert mock_coordinator.data.weather_temperature_forecast == {
            10: 20.0,
            11: 21.5,
            12: 23.0,
        }

    @pytest.mark.asyncio
    async def test_refresh_skip_none_temperature(self) -> None:
        """Test weather forecast refresh skips None temperature values."""
        # ARRANGE
        from unittest.mock import AsyncMock

        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_coordinator._computation_engine = MagicMock()
        mock_coordinator.data = MagicMock()
        mock_coordinator.data.weather_temperature_forecast = {}

        # Mock forecast with None temperature
        forecasts = []
        for hour, temp in [(10, 20.0), (11, None), (12, 23.0)]:
            mock_forecast = MagicMock()
            mock_forecast.slot_time.hour = hour
            mock_forecast.temperature = temp
            forecasts.append(mock_forecast)

        mock_coordinator._computation_engine.async_refresh_weather_forecast = AsyncMock(
            return_value=forecasts
        )

        monitor = EntityMonitor(mock_coordinator)

        # ACT
        await monitor.refresh_weather_forecast()

        # ASSERT - hour 11 should be skipped
        assert mock_coordinator.data.weather_temperature_forecast == {
            10: 20.0,
            12: 23.0,
        }


class TestParseTimeOption:
    """Test parse_time_option method."""

    def test_parse_valid_time_hms(self) -> None:
        """Test parsing valid time string with hours, minutes, seconds."""
        # ARRANGE
        from datetime import time

        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_coordinator.get_option.return_value = "14:30:00"
        monitor = EntityMonitor(mock_coordinator)

        # ACT
        result = monitor.parse_time_option("test_key", "12:00:00")

        # ASSERT
        mock_coordinator.get_option.assert_called_once_with("test_key", "12:00:00")
        assert result == time(14, 30, 0)

    def test_parse_valid_time_hm(self) -> None:
        """Test parsing valid time string with hours and minutes only."""
        # ARRANGE
        from datetime import time

        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_coordinator.get_option.return_value = "09:15"
        monitor = EntityMonitor(mock_coordinator)

        # ACT
        result = monitor.parse_time_option("test_key", "12:00:00")

        # ASSERT
        assert result == time(9, 15, 0)

    def test_parse_invalid_format_uses_default(self) -> None:
        """Test parsing invalid time format falls back to default."""
        # ARRANGE
        from datetime import time

        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_coordinator.get_option.return_value = "invalid"
        monitor = EntityMonitor(mock_coordinator)

        # ACT
        result = monitor.parse_time_option("test_key", "08:00:00")

        # ASSERT - should use default
        assert result == time(8, 0, 0)

    def test_parse_malformed_parts_uses_default(self) -> None:
        """Test parsing malformed time parts falls back to default."""
        # ARRANGE
        from datetime import time

        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_coordinator.get_option.return_value = "99:99:99"
        monitor = EntityMonitor(mock_coordinator)

        # ACT
        result = monitor.parse_time_option("test_key", "10:00:00")

        # ASSERT - should use default due to ValueError
        assert result == time(10, 0, 0)

    def test_parse_empty_string_uses_default(self) -> None:
        """Test parsing empty string falls back to default."""
        # ARRANGE
        from datetime import time

        mock_coordinator = MagicMock()
        mock_coordinator.hass = MagicMock()
        mock_coordinator.get_option.return_value = ""
        monitor = EntityMonitor(mock_coordinator)

        # ACT
        result = monitor.parse_time_option("test_key", "06:30:00")

        # ASSERT - should use default
        assert result == time(6, 30, 0)
