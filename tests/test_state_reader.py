"""Unit tests for StateReader."""

from unittest.mock import MagicMock

import pytest

from custom_components.localshift.const import (
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_PRICE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_TESLEMETRY_GRID_POWER,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    DEFAULT_ENTITY_IDS,
)
from custom_components.localshift.coordinator_data import CoordinatorData
from custom_components.localshift.state_reader import StateReader


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
    from custom_components.localshift.entity_validator import IntegrationStatus

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
