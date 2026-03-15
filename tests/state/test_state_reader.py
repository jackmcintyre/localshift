"""Unit tests for StateReader."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.localshift.const import (
    CONF_PRICING_DATA_SOURCE,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_TESLEMETRY_GRID_POWER,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    DEFAULT_ENTITY_IDS,
    PRICING_SOURCE_AMBER_EXPRESS,
)
from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.state.reader import StateReader


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.states = MagicMock()
    return hass


@pytest.fixture
def mock_entry():
    """Create a mock config entry with entity IDs."""
    entry = MagicMock()
    entry.data = {
        CONF_TESLEMETRY_GRID_POWER: "sensor.tesla_powerwall_grid_power",
        CONF_TESLEMETRY_SOC: "sensor.tesla_powerwall_soc",
        CONF_TESLEMETRY_OPERATION_MODE: "select.tesla_powerwall_operation_mode",
        CONF_PRICING_GENERAL_PRICE: "sensor.amber_general_price",
        CONF_PRICING_FEED_IN_PRICE: "sensor.amber_feed_in_price",
        CONF_SOLCAST_FORECAST_TODAY: "sensor.solcast_forecast_today",
    }
    return entry


@pytest.fixture
def mock_entity_validator():
    """Create a mock EntityValidator for testing."""
    from custom_components.localshift.utils.validation import IntegrationStatus

    validator = MagicMock()
    validator.should_allow_automation = MagicMock(return_value=True)
    validator.status = IntegrationStatus.OK
    validator.errors = []
    validator.warnings = []
    return validator


@pytest.fixture
def state_reader(mock_hass, mock_entry, mock_entity_validator):
    """Create a StateReader instance."""
    return StateReader(mock_hass, mock_entry, mock_entity_validator)


@pytest.fixture
def coordinator_data():
    """Create a fresh CoordinatorData instance."""
    return CoordinatorData()


# =============================================================================
# HELPER METHOD TESTS
# =============================================================================


class TestReadFloat:
    """Tests for _read_float method."""

    def test_read_float_valid(self, state_reader, mock_hass):
        """Test reading a valid float value."""
        state = MagicMock()
        state.state = "42.5"
        mock_hass.states.get.return_value = state

        result = state_reader._read_float("sensor.test")

        assert result == 42.5

    def test_read_float_integer(self, state_reader, mock_hass):
        """Test reading an integer value as float."""
        state = MagicMock()
        state.state = "100"
        mock_hass.states.get.return_value = state

        result = state_reader._read_float("sensor.test")

        assert result == 100.0

    def test_read_float_negative(self, state_reader, mock_hass):
        """Test reading a negative float value."""
        state = MagicMock()
        state.state = "-2.5"
        mock_hass.states.get.return_value = state

        result = state_reader._read_float("sensor.test")

        assert result == -2.5

    def test_read_float_unavailable(self, state_reader, mock_hass):
        """Test reading from unavailable entity returns default."""
        state = MagicMock()
        state.state = "unavailable"
        mock_hass.states.get.return_value = state

        result = state_reader._read_float("sensor.test", default=10.0)

        assert result == 10.0

    def test_read_float_unknown(self, state_reader, mock_hass):
        """Test reading from unknown state returns default."""
        state = MagicMock()
        state.state = "unknown"
        mock_hass.states.get.return_value = state

        result = state_reader._read_float("sensor.test", default=5.0)

        assert result == 5.0

    def test_read_float_none_entity(self, state_reader, mock_hass):
        """Test reading from non-existent entity returns default."""
        mock_hass.states.get.return_value = None

        result = state_reader._read_float("sensor.test", default=7.5)

        assert result == 7.5

    def test_read_float_invalid_value(self, state_reader, mock_hass):
        """Test reading invalid value returns default."""
        state = MagicMock()
        state.state = "invalid"
        mock_hass.states.get.return_value = state

        result = state_reader._read_float("sensor.test", default=3.0)

        assert result == 3.0

    def test_read_float_default_zero(self, state_reader, mock_hass):
        """Test default is 0.0 when not specified."""
        mock_hass.states.get.return_value = None

        result = state_reader._read_float("sensor.test")

        assert result == 0.0


class TestReadState:
    """Tests for _read_state method."""

    def test_read_state_valid(self, state_reader, mock_hass):
        """Test reading a valid state string."""
        state = MagicMock()
        state.state = "autonomous"
        mock_hass.states.get.return_value = state

        result = state_reader._read_state("select.test")

        assert result == "autonomous"

    def test_read_state_unavailable(self, state_reader, mock_hass):
        """Test reading from unavailable entity returns default."""
        state = MagicMock()
        state.state = "unavailable"
        mock_hass.states.get.return_value = state

        result = state_reader._read_state("select.test", default="default_mode")

        assert result == "default_mode"

    def test_read_state_none_entity(self, state_reader, mock_hass):
        """Test reading from non-existent entity returns default."""
        mock_hass.states.get.return_value = None

        result = state_reader._read_state("select.test", default="fallback")

        assert result == "fallback"

    def test_read_state_default_empty(self, state_reader, mock_hass):
        """Test default is empty string when not specified."""
        mock_hass.states.get.return_value = None

        result = state_reader._read_state("select.test")

        assert result == ""


class TestReadBool:
    """Tests for _read_bool method."""

    def test_read_bool_on(self, state_reader, mock_hass):
        """Test reading 'on' state returns True."""
        state = MagicMock()
        state.state = "on"
        mock_hass.states.get.return_value = state

        result = state_reader._read_bool("binary_sensor.test")

        assert result is True

    def test_read_bool_off(self, state_reader, mock_hass):
        """Test reading 'off' state returns False."""
        state = MagicMock()
        state.state = "off"
        mock_hass.states.get.return_value = state

        result = state_reader._read_bool("binary_sensor.test")

        assert result is False

    def test_read_bool_unavailable(self, state_reader, mock_hass):
        """Test reading unavailable entity returns False."""
        state = MagicMock()
        state.state = "unavailable"
        mock_hass.states.get.return_value = state

        result = state_reader._read_bool("binary_sensor.test")

        assert result is False

    def test_read_bool_none_entity(self, state_reader, mock_hass):
        """Test reading non-existent entity returns False."""
        mock_hass.states.get.return_value = None

        result = state_reader._read_bool("binary_sensor.test")

        assert result is False


class TestReadAttribute:
    """Tests for _read_attribute method."""

    def test_read_attribute_valid(self, state_reader, mock_hass):
        """Test reading a valid attribute."""
        state = MagicMock()
        state.attributes = {"forecasts": [{"price": 0.25}]}
        mock_hass.states.get.return_value = state

        result = state_reader._read_attribute("sensor.test", "forecasts")

        assert result == [{"price": 0.25}]

    def test_read_attribute_missing(self, state_reader, mock_hass):
        """Test reading missing attribute returns default."""
        state = MagicMock()
        state.attributes = {}
        mock_hass.states.get.return_value = state

        result = state_reader._read_attribute("sensor.test", "missing", default=[])

        assert result == []

    def test_read_attribute_none_entity(self, state_reader, mock_hass):
        """Test reading attribute from non-existent entity returns default."""
        mock_hass.states.get.return_value = None

        result = state_reader._read_attribute("sensor.test", "attr", default=None)

        assert result is None


# =============================================================================
# GET_ENTITY_ID TESTS
# =============================================================================


class TestGetEntityId:
    """Tests for _get_entity_id method."""

    def test_get_entity_id_from_config(self, state_reader, mock_entry):
        """Test getting entity ID from config entry data."""
        result = state_reader._get_entity_id(CONF_TESLEMETRY_GRID_POWER)

        assert result == "sensor.tesla_powerwall_grid_power"

    def test_get_entity_id_fallback_to_default(self, mock_hass, mock_entity_validator):
        """Test fallback to default when key not in entry data."""
        entry = MagicMock()
        entry.data = {}  # Empty config

        reader = StateReader(mock_hass, entry, mock_entity_validator)
        result = reader._get_entity_id(CONF_TESLEMETRY_GRID_POWER)

        assert result == DEFAULT_ENTITY_IDS.get(CONF_TESLEMETRY_GRID_POWER, "")

    def test_get_entity_id_unknown_key(self, state_reader):
        """Test unknown key returns empty string."""
        result = state_reader._get_entity_id("unknown_key")

        assert result == ""


# =============================================================================
# SOLCAST FORECAST TESTS
# =============================================================================


class TestReadSolcastForecastList:
    """Tests for _read_solcast_forecast_list method."""

    def test_read_solcast_detailed_forecast(self, state_reader, mock_hass):
        """Test reading Solcast forecast with detailedForecast attribute."""
        forecast_data = [{"period_start": "2026-02-20T06:00:00", "pv_estimate": 1.5}]
        state = MagicMock()
        state.state = "10.5"
        state.attributes = {"detailedForecast": forecast_data}
        mock_hass.states.get.return_value = state

        result = state_reader._read_solcast_forecast_list("sensor.solcast_today")

        assert result == forecast_data

    def test_read_solcast_detailed_hourly(self, state_reader, mock_hass):
        """Test reading Solcast forecast with detailedHourly attribute."""
        forecast_data = [{"start": "2026-02-20T06:00:00", "pv_estimate10": 2.0}]
        state = MagicMock()
        state.state = "10.5"
        state.attributes = {"detailedHourly": forecast_data}
        mock_hass.states.get.return_value = state

        result = state_reader._read_solcast_forecast_list("sensor.solcast_today")

        assert result == forecast_data

    def test_read_solcast_forecast_attribute(self, state_reader, mock_hass):
        """Test reading Solcast forecast with forecast attribute."""
        forecast_data = [{"period_start": "2026-02-20T06:00:00", "pv_estimate": 1.0}]
        state = MagicMock()
        state.state = "10.5"
        state.attributes = {"forecast": forecast_data}
        mock_hass.states.get.return_value = state

        result = state_reader._read_solcast_forecast_list("sensor.solcast_today")

        assert result == forecast_data

    def test_read_solcast_no_forecast_attribute(self, state_reader, mock_hass):
        """Test reading Solcast entity without forecast attributes."""
        state = MagicMock()
        state.state = "10.5"
        state.attributes = {"some_other_attr": "value"}
        mock_hass.states.get.return_value = state

        result = state_reader._read_solcast_forecast_list("sensor.solcast_today")

        assert result == []

    def test_read_solcast_unavailable(self, state_reader, mock_hass):
        """Test reading from unavailable Solcast entity."""
        state = MagicMock()
        state.state = "unavailable"
        mock_hass.states.get.return_value = state

        result = state_reader._read_solcast_forecast_list("sensor.solcast_today")

        assert result == []

    def test_read_solcast_none_entity(self, state_reader, mock_hass):
        """Test reading from non-existent Solcast entity."""
        mock_hass.states.get.return_value = None

        # Mock async_all to return empty list (no discovery)
        mock_hass.states.async_all.return_value = []

        result = state_reader._read_solcast_forecast_list("sensor.solcast_today")

        assert result == []


# =============================================================================
# READ_ALL_EXTERNAL_STATE TESTS
# =============================================================================


class TestReadAllExternalState:
    """Tests for read_all_external_state method."""

    def test_read_all_external_state_teslemetry(
        self, state_reader, mock_hass, coordinator_data
    ):
        """Test reading all Teslemetry states."""

        def mock_get_state(entity_id):
            state = MagicMock()
            if "grid_power" in entity_id:
                state.state = "2.5"
            elif "soc" in entity_id:
                state.state = "75.5"
            elif "operation_mode" in entity_id:
                state.state = "autonomous"
            elif "backup_reserve" in entity_id:
                state.state = "20"
            elif "battery_power" in entity_id:
                state.state = "-1.5"
            elif "solar_power" in entity_id:
                state.state = "3.0"
            elif "load_power" in entity_id:
                state.state = "4.0"
            else:
                state.state = "0"
            return state

        mock_hass.states.get = mock_get_state

        state_reader.read_all_external_state(coordinator_data)

        assert coordinator_data.grid_power_kw == 2.5
        assert coordinator_data.soc == 75.5
        assert coordinator_data.operation_mode == "autonomous"
        assert coordinator_data.backup_reserve == 20.0
        assert coordinator_data.battery_power_kw == -1.5
        assert coordinator_data.solar_power_kw == 3.0
        assert coordinator_data.load_power_kw == 4.0

    def test_read_all_external_state_pricing(
        self, state_reader, mock_hass, coordinator_data
    ):
        """Test reading pricing states."""

        def mock_get_state(entity_id):
            state = MagicMock()
            if "general_price" in entity_id:
                state.state = "0.25"
            elif "feed_in_price" in entity_id:
                state.state = "0.08"
            elif "price_spike" in entity_id:
                state.state = "on"
            else:
                state.state = "0"
            return state

        mock_hass.states.get = mock_get_state

        state_reader.read_all_external_state(coordinator_data)

        assert coordinator_data.general_price == 0.25
        assert coordinator_data.feed_in_price == 0.08
        assert coordinator_data.price_spike is True

    def test_read_all_external_state_with_forecasts(
        self, state_reader, mock_hass, coordinator_data
    ):
        """Test reading forecast attributes."""

        def mock_get_state(entity_id):
            state = MagicMock()
            state.state = "0"
            if "general_forecast" in entity_id:
                state.attributes = {"forecasts": [{"price": 0.30}]}
            elif "feed_in_forecast" in entity_id:
                state.attributes = {"forecasts": [{"price": 0.05}]}
            else:
                state.attributes = {}
            return state

        mock_hass.states.get = mock_get_state

        state_reader.read_all_external_state(coordinator_data)

        assert coordinator_data.general_forecast == [{"price": 0.30}]
        assert coordinator_data.feed_in_forecast == [{"price": 0.05}]

    def test_read_all_external_state_handles_missing_entities(
        self, state_reader, mock_hass, coordinator_data
    ):
        """Test that missing entities use defaults."""
        mock_hass.states.get.return_value = None

        state_reader.read_all_external_state(coordinator_data)

        # All values should be defaults
        assert coordinator_data.grid_power_kw == 0.0
        assert coordinator_data.soc == 0.0
        assert coordinator_data.operation_mode == ""
        assert coordinator_data.general_price == 0.0
        assert coordinator_data.feed_in_price == 0.0
        assert coordinator_data.price_spike is False

    def test_read_all_external_state_resets_amber_demand_window_when_entity_missing(
        self, state_reader, mock_hass, coordinator_data
    ):
        """Amber demand window should reset when configured source has no entity."""
        state_reader.entry.data[CONF_PRICING_DATA_SOURCE] = PRICING_SOURCE_AMBER_EXPRESS
        state_reader.entry.data[CONF_PRICING_PRICE_SPIKE] = ""
        coordinator_data.demand_window_amber = True

        def mock_get_state(_entity_id):
            state = MagicMock()
            state.state = "0"
            state.attributes = {}
            return state

        mock_hass.states.get = mock_get_state

        state_reader.read_all_external_state(coordinator_data)

        assert coordinator_data.demand_window_amber is False


class TestPriceAvailability:
    """Tests for price availability tracking (Issue #330)."""

    def test_prices_available_when_both_available(
        self, state_reader, mock_hass, coordinator_data
    ):
        """Test prices_available is True when both prices are available."""

        def mock_get_state(entity_id):
            state = MagicMock()
            state.state = "0"
            if "general_price" in entity_id:
                state.state = "0.25"
            elif "feed_in_price" in entity_id:
                state.state = "0.08"
            state.attributes = {}
            return state

        mock_hass.states.get = mock_get_state

        state_reader.read_all_external_state(coordinator_data)

        assert coordinator_data.prices_available is True
        assert coordinator_data.general_price == 0.25
        assert coordinator_data.feed_in_price == 0.08

    def test_prices_unavailable_when_general_price_missing(
        self, state_reader, mock_hass, coordinator_data
    ):
        """Test prices_available is False when general_price is unavailable."""

        def mock_get_state(entity_id):
            state = MagicMock()
            state.state = "0"
            if "general_price" in entity_id:
                state.state = "unavailable"  # Simulate unavailable
            elif "feed_in_price" in entity_id:
                state.state = "0.08"
            state.attributes = {}
            return state

        mock_hass.states.get = mock_get_state

        state_reader.read_all_external_state(coordinator_data)

        assert coordinator_data.prices_available is False
        assert coordinator_data.general_price == 0.0  # Falls back to 0
        assert coordinator_data.feed_in_price == 0.08

    def test_prices_unavailable_when_feed_in_price_missing(
        self, state_reader, mock_hass, coordinator_data
    ):
        """Test prices_available is False when feed_in_price is unavailable."""

        def mock_get_state(entity_id):
            state = MagicMock()
            state.state = "0"
            if "general_price" in entity_id:
                state.state = "0.25"
            elif "feed_in_price" in entity_id:
                state.state = "unknown"  # Simulate unknown
            state.attributes = {}
            return state

        mock_hass.states.get = mock_get_state

        state_reader.read_all_external_state(coordinator_data)

        assert coordinator_data.prices_available is False
        assert coordinator_data.general_price == 0.25
        assert coordinator_data.feed_in_price == 0.0  # Falls back to 0

    def test_prices_unavailable_when_both_missing(
        self, state_reader, mock_hass, coordinator_data
    ):
        """Test prices_available is False when both prices are unavailable."""

        def mock_get_state(entity_id):
            state = MagicMock()
            state.state = "0"
            if "general_price" in entity_id:
                state.state = "unavailable"
            elif "feed_in_price" in entity_id:
                state.state = "unavailable"
            state.attributes = {}
            return state

        mock_hass.states.get = mock_get_state

        state_reader.read_all_external_state(coordinator_data)

        assert coordinator_data.prices_available is False
        assert coordinator_data.general_price == 0.0
        assert coordinator_data.feed_in_price == 0.0

    def test_prices_available_with_zero_prices(
        self, state_reader, mock_hass, coordinator_data
    ):
        """Test prices_available is True even when prices are legitimately $0."""

        def mock_get_state(entity_id):
            state = MagicMock()
            state.state = "0"
            if "general_price" in entity_id:
                state.state = "0"  # Legitimate $0 price
            elif "feed_in_price" in entity_id:
                state.state = "0"  # Legitimate $0 price
            state.attributes = {}
            return state

        mock_hass.states.get = mock_get_state

        state_reader.read_all_external_state(coordinator_data)

        # Both prices are $0 but entities are available
        assert coordinator_data.prices_available is True
        assert coordinator_data.general_price == 0.0
        assert coordinator_data.feed_in_price == 0.0


class TestCheckAutomationReady:
    """Tests for check_automation_ready method (Issue #349)."""

    def test_automation_ready_all_valid(self, state_reader):
        """Test automation is ready when all inputs are valid."""
        data = CoordinatorData()
        data.soc = 50.0
        data.prices_available = True
        data.operation_mode = "autonomous"
        data.backup_reserve = 20.0
        data.forecast_ready = True
        data.forecast_status = "ready"

        is_ready, status, missing = state_reader.check_automation_ready(data)

        assert is_ready is True
        assert status["soc"] is True
        assert status["prices_available"] is True
        assert status["operation_mode"] is True
        assert status["backup_reserve"] is True
        assert status["forecast"] is True
        assert len(missing) == 0
        assert data.automation_ready is True

    def test_automation_not_ready_soc_zero(self, state_reader):
        """Test automation not ready when SOC is zero."""
        data = CoordinatorData()
        data.soc = 0.0
        data.prices_available = True
        data.operation_mode = "autonomous"
        data.backup_reserve = 20.0
        data.forecast_ready = True

        is_ready, status, missing = state_reader.check_automation_ready(data)

        assert is_ready is False
        assert status["soc"] is False
        assert "SOC" in missing[0]

    def test_automation_not_ready_soc_none(self, state_reader):
        """Test automation not ready when SOC is None."""
        data = CoordinatorData()
        data.soc = None  # type: ignore[assignment]
        data.prices_available = True
        data.operation_mode = "autonomous"
        data.backup_reserve = 20.0
        data.forecast_ready = True

        is_ready, status, missing = state_reader.check_automation_ready(data)

        assert is_ready is False
        assert status["soc"] is False

    def test_automation_not_ready_prices_unavailable(self, state_reader):
        """Test automation not ready when prices are unavailable."""
        data = CoordinatorData()
        data.soc = 50.0
        data.prices_available = False
        data.operation_mode = "autonomous"
        data.backup_reserve = 20.0
        data.forecast_ready = True

        is_ready, status, missing = state_reader.check_automation_ready(data)

        assert is_ready is False
        assert status["prices_available"] is False
        assert "Price entities" in missing[0]

    def test_automation_not_ready_operation_mode_empty(self, state_reader):
        """Test automation not ready when operation mode is empty."""
        data = CoordinatorData()
        data.soc = 50.0
        data.prices_available = True
        data.operation_mode = ""
        data.backup_reserve = 20.0
        data.forecast_ready = True

        is_ready, status, missing = state_reader.check_automation_ready(data)

        assert is_ready is False
        assert status["operation_mode"] is False
        assert "Operation mode" in missing[0]

    def test_automation_not_ready_operation_mode_unknown(self, state_reader):
        """Test automation not ready when operation mode is unknown."""
        data = CoordinatorData()
        data.soc = 50.0
        data.prices_available = True
        data.operation_mode = "unknown"
        data.backup_reserve = 20.0
        data.forecast_ready = True

        is_ready, status, missing = state_reader.check_automation_ready(data)

        assert is_ready is False
        assert status["operation_mode"] is False

    def test_automation_not_ready_backup_reserve_none(self, state_reader):
        """Test automation not ready when backup reserve is None."""
        data = CoordinatorData()
        data.soc = 50.0
        data.prices_available = True
        data.operation_mode = "autonomous"
        data.backup_reserve = None  # type: ignore[assignment]
        data.forecast_ready = True

        is_ready, status, missing = state_reader.check_automation_ready(data)

        assert is_ready is False
        assert status["backup_reserve"] is False
        assert "Backup reserve" in missing[0]

    def test_automation_not_ready_backup_reserve_negative(self, state_reader):
        """Test automation not ready when backup reserve is negative."""
        data = CoordinatorData()
        data.soc = 50.0
        data.prices_available = True
        data.operation_mode = "autonomous"
        data.backup_reserve = -1.0
        data.forecast_ready = True

        is_ready, status, missing = state_reader.check_automation_ready(data)

        assert is_ready is False
        assert status["backup_reserve"] is False

    def test_automation_not_ready_forecast_stale(self, state_reader):
        """Test automation not ready when forecast is stale/unavailable."""
        data = CoordinatorData()
        data.soc = 50.0
        data.prices_available = True
        data.operation_mode = "autonomous"
        data.backup_reserve = 20.0
        data.forecast_ready = False
        data.forecast_status = "stale"

        is_ready, status, missing = state_reader.check_automation_ready(data)

        assert is_ready is False
        assert status["forecast"] is False

    def test_automation_ready_with_partial_forecast(self, state_reader):
        """Test automation is ready with partial forecast."""
        data = CoordinatorData()
        data.soc = 50.0
        data.prices_available = True
        data.operation_mode = "autonomous"
        data.backup_reserve = 20.0
        data.forecast_ready = False
        data.forecast_status = "partial"

        is_ready, status, missing = state_reader.check_automation_ready(data)

        assert is_ready is True
        assert status["forecast"] is True

    def test_automation_ready_suppress_warning(self, state_reader):
        """Test suppress_warning parameter logs at DEBUG level."""
        data = CoordinatorData()
        data.soc = 0.0
        data.prices_available = True
        data.operation_mode = "autonomous"
        data.backup_reserve = 20.0
        data.forecast_ready = True

        is_ready, status, missing = state_reader.check_automation_ready(
            data, suppress_warning=True
        )

        assert is_ready is False
        assert data.automation_ready is False


class TestWeatherEntityEdgeCases:
    """Tests for weather entity edge cases."""

    def test_weather_entity_unknown_state(
        self, state_reader, mock_hass, coordinator_data
    ):
        """Test weather entity in unknown state is handled gracefully."""
        from custom_components.localshift.const import CONF_WEATHER_ENTITY

        state_reader.entry.data[CONF_WEATHER_ENTITY] = "weather.test"
        state = MagicMock()
        state.state = "unknown"
        state.attributes = {"temperature": 25}
        mock_hass.states.get.return_value = state

        state_reader.read_all_external_state(coordinator_data)

        assert coordinator_data.weather_temperature_current == 0.0

    def test_weather_entity_unavailable_state(
        self, state_reader, mock_hass, coordinator_data
    ):
        """Test weather entity in unavailable state is handled gracefully."""
        from custom_components.localshift.const import CONF_WEATHER_ENTITY

        state_reader.entry.data[CONF_WEATHER_ENTITY] = "weather.test"
        state = MagicMock()
        state.state = "unavailable"
        state.attributes = {"temperature": 25}
        mock_hass.states.get.return_value = state

        state_reader.read_all_external_state(coordinator_data)

        assert coordinator_data.weather_temperature_current == 0.0

    def test_weather_entity_invalid_temperature(
        self, state_reader, mock_hass, coordinator_data
    ):
        """Test weather entity with invalid temperature value."""
        from custom_components.localshift.const import CONF_WEATHER_ENTITY

        state_reader.entry.data[CONF_WEATHER_ENTITY] = "weather.test"
        state = MagicMock()
        state.state = "sunny"
        state.attributes = {"temperature": "invalid"}
        mock_hass.states.get.return_value = state

        state_reader.read_all_external_state(coordinator_data)

        assert coordinator_data.weather_temperature_current == 0.0


# =============================================================================
# FORECAST EXTENSION TESTS (Issue #632)
# =============================================================================


class TestCalculateCurrentDayAveragePrice:
    """Tests for _calculate_current_day_average_price method."""

    def test_calculate_average_with_valid_forecast(self, state_reader):
        """Test calculating average price from valid forecast."""
        from datetime import datetime, timedelta

        now = datetime(2026, 3, 10, 14, 0)
        forecast = [
            {
                "start_time": (now - timedelta(hours=2)).isoformat(),
                "duration": 30,
                "per_kwh": 0.10,
            },
            {
                "start_time": (now - timedelta(hours=1)).isoformat(),
                "duration": 30,
                "per_kwh": 0.20,
            },
        ]

        result = state_reader._calculate_current_day_average_price(forecast, now)

        assert abs(result - 0.15) < 0.001

    def test_calculate_average_with_empty_forecast(self, state_reader):
        """Test calculating average from empty forecast returns default."""
        from datetime import datetime

        now = datetime(2026, 3, 10, 14, 0)
        result = state_reader._calculate_current_day_average_price([], now)

        assert result == 0.20

    def test_calculate_average_filters_future_slots(self, state_reader):
        """Test that future slots are excluded from average calculation."""
        from datetime import datetime, timedelta

        now = datetime(2026, 3, 10, 14, 0)
        forecast = [
            {
                "start_time": (now - timedelta(hours=1)).isoformat(),
                "duration": 30,
                "per_kwh": 0.10,
            },
            {
                "start_time": (now + timedelta(hours=1)).isoformat(),
                "duration": 30,
                "per_kwh": 0.50,
            },
        ]

        result = state_reader._calculate_current_day_average_price(forecast, now)

        assert result == 0.10

    def test_calculate_average_with_missing_price_field(self, state_reader):
        """Test average calculation with missing price field."""
        from datetime import datetime, timedelta

        now = datetime(2026, 3, 10, 14, 0)
        forecast = [
            {
                "start_time": (now - timedelta(hours=2)).isoformat(),
                "duration": 30,
            },
        ]

        result = state_reader._calculate_current_day_average_price(forecast, now)

        assert result == 0.20

    def test_calculate_average_with_non_dict_entry(self, state_reader):
        """Test average calculation skips non-dict entries."""
        from datetime import datetime, timedelta

        now = datetime(2026, 3, 10, 14, 0)
        forecast = [
            "not a dict",
            {
                "start_time": (now - timedelta(hours=1)).isoformat(),
                "duration": 30,
                "per_kwh": 0.20,
            },
        ]

        result = state_reader._calculate_current_day_average_price(forecast, now)

        assert result == 0.20

    def test_calculate_average_with_missing_start_time(self, state_reader):
        """Test average calculation skips entries without start_time."""
        from datetime import datetime, timedelta

        now = datetime(2026, 3, 10, 14, 0)
        forecast = [
            {
                "per_kwh": 0.15,
                "duration": 30,
            },
            {
                "start_time": (now - timedelta(hours=1)).isoformat(),
                "duration": 30,
                "per_kwh": 0.20,
            },
        ]

        result = state_reader._calculate_current_day_average_price(forecast, now)

        assert result == 0.20

    def test_calculate_average_with_invalid_start_time(self, state_reader):
        """Test average calculation skips entries with invalid start_time."""
        from datetime import datetime, timedelta

        now = datetime(2026, 3, 10, 14, 0)
        forecast = [
            {
                "start_time": "invalid-iso-format",
                "duration": 30,
                "per_kwh": 0.15,
            },
            {
                "start_time": (now - timedelta(hours=1)).isoformat(),
                "duration": 30,
                "per_kwh": 0.20,
            },
        ]

        result = state_reader._calculate_current_day_average_price(forecast, now)

        assert result == 0.20


class TestExtendForecastWithAssumedPrices:
    """Tests for _extend_forecast_with_assumed_prices method."""

    def test_extend_forecast_to_24_hours(self, state_reader):
        """Test extending a short forecast to 24 hours."""
        from datetime import datetime, timedelta

        now = datetime(2026, 3, 10, 14, 0)
        forecast = [
            {
                "start_time": now.isoformat(),
                "duration": 30,
                "per_kwh": 0.15,
            }
        ]

        result = state_reader._extend_forecast_with_assumed_prices(
            forecast, now, assumed_price=0.20
        )

        assert len(result) > len(forecast)
        last_entry = result[-1]
        last_time = datetime.fromisoformat(last_entry["start_time"])
        assert (last_time - now) >= timedelta(hours=23)

    def test_no_extension_when_already_24_hours(self, state_reader):
        """Test no extension when forecast already covers 24 hours."""
        from datetime import datetime, timedelta

        now = datetime(2026, 3, 10, 14, 0)
        forecast = []
        for i in range(48):
            forecast.append({
                "start_time": (now + timedelta(minutes=30 * i)).isoformat(),
                "duration": 30,
                "per_kwh": 0.10,
            })

        result = state_reader._extend_forecast_with_assumed_prices(
            forecast, now, assumed_price=0.20
        )

        assert len(result) == len(forecast)

    def test_extend_with_correct_price(self, state_reader):
        """Test that extended entries use the assumed price."""
        from datetime import datetime

        now = datetime(2026, 3, 10, 14, 0)
        forecast = [
            {
                "start_time": now.isoformat(),
                "duration": 30,
                "per_kwh": 0.15,
            }
        ]

        result = state_reader._extend_forecast_with_assumed_prices(
            forecast, now, assumed_price=0.25
        )

        for entry in result[1:]:
            assert entry["per_kwh"] == 0.25

    def test_extend_with_30_minute_slots(self, state_reader):
        """Test that extended entries use 30-minute duration."""
        from datetime import datetime

        now = datetime(2026, 3, 10, 14, 0)
        forecast = [
            {
                "start_time": now.isoformat(),
                "duration": 30,
                "per_kwh": 0.15,
            }
        ]

        result = state_reader._extend_forecast_with_assumed_prices(
            forecast, now, assumed_price=0.20
        )

        for entry in result[1:]:
            assert entry["duration"] == 30

    def test_extend_with_invalid_last_time(self, state_reader):
        """Test extension returns original forecast if last_time is invalid."""
        from datetime import datetime

        now = datetime(2026, 3, 10, 14, 0)
        forecast = [
            {
                "start_time": "invalid-iso-format",
                "duration": 30,
                "per_kwh": 0.15,
            }
        ]

        result = state_reader._extend_forecast_with_assumed_prices(
            forecast, now, assumed_price=0.20
        )

        assert result == forecast

    def test_extend_with_non_dict_last_entry(self, state_reader):
        """Test extension returns original forecast if last entry is not a dict."""
        from datetime import datetime

        now = datetime(2026, 3, 10, 14, 0)
        forecast = ["not a dict"]

        result = state_reader._extend_forecast_with_assumed_prices(
            forecast, now, assumed_price=0.20
        )

        assert result == forecast

    def test_extend_with_missing_start_time_in_last_entry(self, state_reader):
        """Test extension returns original forecast if last entry has no start_time."""
        from datetime import datetime

        now = datetime(2026, 3, 10, 14, 0)
        forecast = [
            {
                "duration": 30,
                "per_kwh": 0.15,
            }
        ]

        result = state_reader._extend_forecast_with_assumed_prices(
            forecast, now, assumed_price=0.20
        )

        assert result == forecast

    def test_extend_empty_forecast_returns_empty(self, state_reader):
        """Test that extending empty forecast returns empty."""
        from datetime import datetime

        now = datetime(2026, 3, 10, 14, 0)
        result = state_reader._extend_forecast_with_assumed_prices(
            [], now, assumed_price=0.20
        )

        assert result == []

    def test_extended_entries_have_required_fields(self, state_reader):
        """Test that extended entries have all required fields."""
        from datetime import datetime

        now = datetime(2026, 3, 10, 14, 0)
        forecast = [
            {
                "start_time": now.isoformat(),
                "duration": 30,
                "per_kwh": 0.15,
            }
        ]

        result = state_reader._extend_forecast_with_assumed_prices(
            forecast, now, assumed_price=0.20
        )

        for entry in result[1:]:
            assert "start_time" in entry
            assert "duration" in entry
            assert "per_kwh" in entry


class TestForecastExtensionIntegration:
    """Tests for forecast extension in read_all_external_state (Issue #632)."""

    def test_forecast_extended_to_24_hours(
        self, state_reader, mock_hass, coordinator_data
    ):
        """Test that short forecast is extended to 24 hours."""
        tz = ZoneInfo("Australia/Sydney")
        now = datetime(2026, 3, 10, 14, 0, tzinfo=tz)

        def mock_get_state(entity_id):
            state = MagicMock()
            state.state = "0"
            if "general_price" in entity_id:
                state.state = "0.25"
            elif "feed_in_price" in entity_id:
                state.state = "0.08"
            if "general_forecast" in entity_id:
                state.attributes = {
                    "forecasts": [
                        {
                            "start_time": now.isoformat(),
                            "duration": 30,
                            "per_kwh": 0.15,
                        }
                    ]
                }
            elif "feed_in_forecast" in entity_id:
                state.attributes = {
                    "forecasts": [
                        {
                            "start_time": now.isoformat(),
                            "duration": 30,
                            "per_kwh": 0.05,
                        }
                    ]
                }
            else:
                state.attributes = {}
            return state

        mock_hass.states.get = mock_get_state
        with patch("custom_components.localshift.state.reader.dt_util") as mock_dt_util:
            mock_dt_util.now.return_value = now
            state_reader.read_all_external_state(coordinator_data)

        last_general = coordinator_data.general_forecast[-1]
        last_time = datetime.fromisoformat(last_general["start_time"])
        assert (last_time - now) >= timedelta(hours=23)

    def test_forecast_not_extended_when_already_24_hours(
        self, state_reader, mock_hass, coordinator_data
    ):
        """Test that forecast is not extended when already 24+ hours."""
        tz = ZoneInfo("Australia/Sydney")
        now = datetime(2026, 3, 10, 14, 0, tzinfo=tz)
        forecast_24h = []
        for i in range(48):
            forecast_24h.append({
                "start_time": (now + timedelta(minutes=30 * i)).isoformat(),
                "duration": 30,
                "per_kwh": 0.10,
            })

        def mock_get_state(entity_id):
            state = MagicMock()
            state.state = "0"
            if "general_price" in entity_id:
                state.state = "0.25"
            elif "feed_in_price" in entity_id:
                state.state = "0.08"
            if "general_forecast" in entity_id:
                state.attributes = {"forecasts": forecast_24h}
            elif "feed_in_forecast" in entity_id:
                state.attributes = {"forecasts": forecast_24h}
            else:
                state.attributes = {}
            return state

        mock_hass.states.get = mock_get_state
        with patch("custom_components.localshift.state.reader.dt_util") as mock_dt_util:
            mock_dt_util.now.return_value = now
            state_reader.read_all_external_state(coordinator_data)

        assert len(coordinator_data.general_forecast) == 48
