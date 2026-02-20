"""Unit tests for config_flow."""

from unittest.mock import MagicMock

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.localshift.config_flow import (
    LocalShiftConfigFlow,
    LocalShiftOptionsFlow,
)
from custom_components.localshift.const import (
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_MANUAL_OVERRIDE_TIMEOUT,
    CONF_NOTIFY_SERVICE,
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_SUN_ENTITY,
    CONF_TESLEMETRY_BACKUP_RESERVE,
    CONF_TESLEMETRY_BATTERY_POWER,
    CONF_TESLEMETRY_GRID_POWER,
    CONF_TESLEMETRY_LOAD_POWER,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    CONF_TESLEMETRY_SOLAR_POWER,
    DOMAIN,
)


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.states = MagicMock()
    hass.services = MagicMock()

    # Mock services for notify service discovery
    hass.services.async_services.return_value = {
        "notify": {
            "mobile_app_test": MagicMock(),
            "persistent_notification": MagicMock(),
        }
    }

    return hass


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {}
    entry.options = {}
    entry.domain = DOMAIN
    return entry


def create_mock_state(entity_id: str, state: str, domain: str | None = None):
    """Create a mock state object."""
    mock_state = MagicMock()
    mock_state.entity_id = entity_id
    mock_state.state = state
    mock_state.domain = domain if domain is not None else entity_id.split(".")[0]
    return mock_state


# =============================================================================
# ENTITY VALIDATION TESTS
# =============================================================================


class TestValidateEntities:
    """Tests for _validate_entities method."""

    @pytest.mark.asyncio
    async def test_validate_entities_all_valid(self, mock_hass):
        """Test validation with all valid entities."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        # Mock valid states
        def mock_get_state(entity_id):
            if "select" in entity_id:
                return create_mock_state(entity_id, "autonomous", "select")
            elif "number" in entity_id:
                return create_mock_state(entity_id, "50", "number")
            elif "sensor" in entity_id:
                return create_mock_state(entity_id, "100", "sensor")
            return None

        mock_hass.states.get = mock_get_state

        entities = {
            "test_select": ("select.test", "select"),
            "test_number": ("number.test", "number"),
            "test_sensor": ("sensor.test", "sensor"),
        }

        result = await flow._validate_entities(entities)

        assert result is None  # No errors

    @pytest.mark.asyncio
    async def test_validate_entities_missing(self, mock_hass):
        """Test validation with missing entity."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        mock_hass.states.get.return_value = None

        entities = {
            "test_sensor": ("sensor.missing", "sensor"),
        }

        result = await flow._validate_entities(entities)

        assert result is not None
        assert "test_sensor" in result
        assert "does not exist" in result["test_sensor"]

    @pytest.mark.asyncio
    async def test_validate_entities_unavailable(self, mock_hass):
        """Test validation with unavailable entity."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        mock_hass.states.get.return_value = create_mock_state(
            "sensor.test", "unavailable"
        )

        entities = {
            "test_sensor": ("sensor.test", "sensor"),
        }

        result = await flow._validate_entities(entities)

        assert result is not None
        assert "test_sensor" in result
        assert "unavailable" in result["test_sensor"]

    @pytest.mark.asyncio
    async def test_validate_entities_wrong_domain(self, mock_hass):
        """Test validation with wrong domain."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        # Return sensor domain when select is expected
        mock_hass.states.get.return_value = create_mock_state(
            "sensor.test", "50", "sensor"
        )

        entities = {
            "test_select": ("sensor.test", "select"),  # Expecting select, got sensor
        }

        result = await flow._validate_entities(entities)

        assert result is not None
        assert "test_select" in result
        assert "Expected select" in result["test_select"]


# =============================================================================
# NOTIFY SERVICE VALIDATION TESTS
# =============================================================================


class TestValidateNotifyService:
    """Tests for _validate_notify_service method."""

    @pytest.mark.asyncio
    async def test_validate_notify_service_valid(self, mock_hass):
        """Test validation of valid notify service."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        result = await flow._validate_notify_service("notify.mobile_app_test")

        assert result is None

    @pytest.mark.asyncio
    async def test_validate_notify_service_empty(self, mock_hass):
        """Test validation of empty notify service."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        result = await flow._validate_notify_service("")

        assert result is not None
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_validate_notify_service_wrong_prefix(self, mock_hass):
        """Test validation of service without notify prefix."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        result = await flow._validate_notify_service("mobile_app_test")

        assert result is not None
        assert "notify." in result

    @pytest.mark.asyncio
    async def test_validate_notify_service_not_found(self, mock_hass):
        """Test validation of non-existent notify service."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        result = await flow._validate_notify_service("notify.nonexistent")

        assert result is not None
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_get_notify_services(self, mock_hass):
        """Test getting list of notify services."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        services = await flow._get_notify_services()

        assert isinstance(services, list)
        assert "notify.mobile_app_test" in services
        assert "notify.persistent_notification" in services


# =============================================================================
# USER STEP TESTS
# =============================================================================


class TestUserStep:
    """Tests for async_step_user method."""

    @pytest.mark.asyncio
    async def test_user_step_show_form(self, mock_hass):
        """Test user step shows form on initial load."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        result = await flow.async_step_user()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

    @pytest.mark.asyncio
    async def test_user_step_valid_input_proceeds_to_pricing(self, mock_hass):
        """Test user step proceeds to pricing with valid input."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        # Mock valid entity states
        def mock_get_state(entity_id):
            if "select" in entity_id:
                return create_mock_state(entity_id, "autonomous", "select")
            elif "number" in entity_id:
                return create_mock_state(entity_id, "50", "number")
            else:
                return create_mock_state(entity_id, "100", "sensor")

        mock_hass.states.get = mock_get_state

        user_input = {
            CONF_TESLEMETRY_OPERATION_MODE: "select.tesla_powerwall_operation_mode",
            CONF_TESLEMETRY_BACKUP_RESERVE: "number.tesla_powerwall_backup_reserve",
            CONF_TESLEMETRY_SOC: "sensor.tesla_powerwall_soc",
            CONF_TESLEMETRY_GRID_POWER: "sensor.tesla_powerwall_grid_power",
            CONF_TESLEMETRY_BATTERY_POWER: "sensor.tesla_powerwall_battery_power",
            CONF_TESLEMETRY_SOLAR_POWER: "sensor.tesla_powerwall_solar_power",
            CONF_TESLEMETRY_LOAD_POWER: "sensor.tesla_powerwall_load_power",
        }

        result = await flow.async_step_user(user_input)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "pricing"

    @pytest.mark.asyncio
    async def test_user_step_invalid_input_shows_errors(self, mock_hass):
        """Test user step shows errors with invalid input."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        # Mock missing entity
        mock_hass.states.get.return_value = None

        user_input = {
            CONF_TESLEMETRY_OPERATION_MODE: "select.missing",
            CONF_TESLEMETRY_BACKUP_RESERVE: "number.missing",
            CONF_TESLEMETRY_SOC: "sensor.missing",
            CONF_TESLEMETRY_GRID_POWER: "sensor.missing",
            CONF_TESLEMETRY_BATTERY_POWER: "sensor.missing",
            CONF_TESLEMETRY_SOLAR_POWER: "sensor.missing",
            CONF_TESLEMETRY_LOAD_POWER: "sensor.missing",
        }

        result = await flow.async_step_user(user_input)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert "errors" in result


# =============================================================================
# PRICING STEP TESTS
# =============================================================================


class TestPricingStep:
    """Tests for async_step_pricing method."""

    @pytest.mark.asyncio
    async def test_pricing_step_show_form(self, mock_hass):
        """Test pricing step shows form on initial load."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        result = await flow.async_step_pricing()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "pricing"

    @pytest.mark.asyncio
    async def test_pricing_step_valid_input_proceeds_to_solcast(self, mock_hass):
        """Test pricing step proceeds to solcast with valid input."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass
        flow._teslemetry_data = {}

        # Mock valid entity states
        def mock_get_state(entity_id):
            if "binary_sensor" in entity_id:
                return create_mock_state(entity_id, "off", "binary_sensor")
            return create_mock_state(entity_id, "0.25", "sensor")

        mock_hass.states.get = mock_get_state

        user_input = {
            CONF_PRICING_GENERAL_PRICE: "sensor.amber_general_price",
            CONF_PRICING_FEED_IN_PRICE: "sensor.amber_feed_in_price",
            CONF_PRICING_GENERAL_FORECAST: "sensor.amber_general_forecast",
            CONF_PRICING_FEED_IN_FORECAST: "sensor.amber_feed_in_forecast",
            CONF_PRICING_PRICE_SPIKE: "binary_sensor.amber_price_spike",
        }

        result = await flow.async_step_pricing(user_input)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "solcast"


# =============================================================================
# SOLCAST STEP TESTS
# =============================================================================


class TestSolcastStep:
    """Tests for async_step_solcast method."""

    @pytest.mark.asyncio
    async def test_solcast_step_show_form(self, mock_hass):
        """Test solcast step shows form on initial load."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        result = await flow.async_step_solcast()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "solcast"

    @pytest.mark.asyncio
    async def test_solcast_step_creates_entry(self, mock_hass):
        """Test solcast step creates entry with valid input."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass
        flow._teslemetry_data = {
            CONF_TESLEMETRY_OPERATION_MODE: "select.tesla_powerwall_operation_mode",
        }
        flow._pricing_data = {
            CONF_PRICING_GENERAL_PRICE: "sensor.amber_general_price",
        }

        # Mock valid entity states
        def mock_get_state(entity_id):
            if "sun" in entity_id:
                return create_mock_state(entity_id, "above_horizon", "sun")
            return create_mock_state(entity_id, "10", "sensor")

        mock_hass.states.get = mock_get_state

        user_input = {
            CONF_SOLCAST_FORECAST_TODAY: "sensor.solcast_today",
            CONF_SOLCAST_FORECAST_TOMORROW: "sensor.solcast_tomorrow",
            CONF_NOTIFY_SERVICE: "notify.mobile_app_test",
            CONF_SUN_ENTITY: "sun.sun",
        }

        result = await flow.async_step_solcast(user_input)

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "LocalShift"

    @pytest.mark.asyncio
    async def test_solcast_step_invalid_notify_shows_error(self, mock_hass):
        """Test solcast step shows error with invalid notify service."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass
        flow._teslemetry_data = {}
        flow._pricing_data = {}

        # Mock valid entity states for sensors
        def mock_get_state(entity_id):
            if "sun" in entity_id:
                return create_mock_state(entity_id, "above_horizon", "sun")
            return create_mock_state(entity_id, "10", "sensor")

        mock_hass.states.get = mock_get_state

        user_input = {
            CONF_SOLCAST_FORECAST_TODAY: "sensor.solcast_today",
            CONF_SOLCAST_FORECAST_TOMORROW: "sensor.solcast_tomorrow",
            CONF_NOTIFY_SERVICE: "invalid_service",  # Invalid - no notify. prefix
            CONF_SUN_ENTITY: "sun.sun",
        }

        result = await flow.async_step_solcast(user_input)

        assert result["type"] == FlowResultType.FORM
        assert "errors" in result
        assert CONF_NOTIFY_SERVICE in result["errors"]


# =============================================================================
# OPTIONS FLOW TESTS
# =============================================================================


class TestOptionsFlow:
    """Tests for LocalShiftOptionsFlow."""

    @pytest.mark.asyncio
    async def test_options_flow_creates_entry(self, mock_hass, mock_config_entry):
        """Test options flow creates entry with user input."""
        flow = LocalShiftConfigFlow.async_get_options_flow(mock_config_entry)

        # Mock hass.config_entries to return our mock config entry
        mock_config_entries = MagicMock()
        mock_config_entries.async_get_known_entry = MagicMock(
            return_value=mock_config_entry
        )
        mock_hass.config_entries = mock_config_entries
        flow.hass = mock_hass

        user_input = {
            CONF_DEMAND_WINDOW_START: "14:00:00",
            CONF_DEMAND_WINDOW_END: "20:00:00",
            CONF_MANUAL_OVERRIDE_TIMEOUT: 4,
        }

        result = await flow.async_step_init(user_input)

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"] == user_input


# =============================================================================
# STATIC METHOD TESTS
# =============================================================================


class TestStaticMethods:
    """Tests for static methods."""

    def test_async_get_options_flow(self, mock_config_entry):
        """Test that async_get_options_flow returns an OptionsFlow."""
        result = LocalShiftConfigFlow.async_get_options_flow(mock_config_entry)

        assert isinstance(result, LocalShiftOptionsFlow)
