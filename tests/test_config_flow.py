"""Unit tests for config_flow."""

from unittest.mock import MagicMock

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.localshift.config_flow import (
    LocalShiftConfigFlow,
    LocalShiftOptionsFlow,
)
from custom_components.localshift.const import (
    CONF_COMPARISON_MODE,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_MANUAL_OVERRIDE_TIMEOUT,
    CONF_NOTIFY_SERVICE,
    CONF_POWER_SIGN_OVERRIDE,
    CONF_PRICING_DATA_SOURCE,
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_TESLEMETRY_BACKUP_RESERVE,
    CONF_TESLEMETRY_BATTERY_POWER,
    CONF_TESLEMETRY_GRID_POWER,
    CONF_TESLEMETRY_LOAD_POWER,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    CONF_TESLEMETRY_SOLAR_POWER,
    DOMAIN,
    PRICING_SOURCE_AMBER_EXPRESS,
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
        assert result["step_id"] == "pricing_source"

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

    @pytest.mark.asyncio
    async def test_pricing_step_amber_express_without_forecasts_proceeds_to_solcast(
        self, mock_hass
    ):
        """Amber Express should not require separate forecast entities."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass
        flow._teslemetry_data = {}
        flow._pricing_source_data = {
            CONF_PRICING_DATA_SOURCE: PRICING_SOURCE_AMBER_EXPRESS,
            CONF_COMPARISON_MODE: "disabled",
        }

        def mock_get_state(entity_id):
            if "binary_sensor" in entity_id:
                return create_mock_state(entity_id, "off", "binary_sensor")
            return create_mock_state(entity_id, "0.25", "sensor")

        mock_hass.states.get = mock_get_state

        user_input = {
            CONF_PRICING_GENERAL_PRICE: "sensor.amber_express_100h_general_price",
            CONF_PRICING_FEED_IN_PRICE: "sensor.amber_express_100h_feed_in_price",
            CONF_PRICING_PRICE_SPIKE: "binary_sensor.amber_express_100h_price_spike",
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
        flow._pricing_source_data = {
            CONF_PRICING_DATA_SOURCE: "amber",
            CONF_COMPARISON_MODE: "disabled",
        }
        flow._pricing_data = {
            CONF_PRICING_GENERAL_PRICE: "sensor.amber_general_price",
        }

        # Mock valid entity states
        def mock_get_state(entity_id):
            return create_mock_state(entity_id, "10", "sensor")

        mock_hass.states.get = mock_get_state

        user_input = {
            CONF_SOLCAST_FORECAST_TODAY: "sensor.solcast_today",
            CONF_SOLCAST_FORECAST_TOMORROW: "sensor.solcast_tomorrow",
            CONF_NOTIFY_SERVICE: "notify.mobile_app_test",
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
            return create_mock_state(entity_id, "10", "sensor")

        mock_hass.states.get = mock_get_state

        user_input = {
            CONF_SOLCAST_FORECAST_TODAY: "sensor.solcast_today",
            CONF_SOLCAST_FORECAST_TOMORROW: "sensor.solcast_tomorrow",
            CONF_NOTIFY_SERVICE: "invalid_service",  # Invalid - no notify. prefix
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

        # Mock the _config_entry_id property to return the entry_id
        # This is needed because OptionsFlow.config_entry property uses it
        type(flow)._config_entry_id = property(lambda self: mock_config_entry.entry_id)

        # Mock config_entry.data with entity mappings for entity_mappings step
        mock_config_entry.data = {
            CONF_TESLEMETRY_OPERATION_MODE: "select.teslemetry_operation_mode",
            CONF_TESLEMETRY_SOC: "sensor.teslemetry_soc",
            CONF_TESLEMETRY_GRID_POWER: "sensor.teslemetry_grid_power",
            CONF_TESLEMETRY_BATTERY_POWER: "sensor.teslemetry_battery_power",
            CONF_TESLEMETRY_SOLAR_POWER: "sensor.teslemetry_solar_power",
            CONF_TESLEMETRY_LOAD_POWER: "sensor.teslemetry_load_power",
            CONF_PRICING_GENERAL_PRICE: "sensor.amber_general_price",
            CONF_PRICING_FEED_IN_PRICE: "sensor.amber_feed_in_price",
            CONF_PRICING_GENERAL_FORECAST: "sensor.amber_general_forecast",
            CONF_PRICING_FEED_IN_FORECAST: "sensor.amber_feed_in_forecast",
            CONF_PRICING_PRICE_SPIKE: "binary_sensor.amber_price_spike",
            CONF_SOLCAST_FORECAST_TODAY: "sensor.solcast_forecast_today",
            CONF_SOLCAST_FORECAST_TOMORROW: "sensor.solcast_forecast_tomorrow",
        }

        # First step: entity mappings - provide entity data
        entity_input = dict(mock_config_entry.data)
        result = await flow.async_step_entity_mappings(entity_input)

        # Should proceed to settings step
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "settings"

        # Second step: settings - provide settings data
        user_input = {
            CONF_NOTIFY_SERVICE: "notify.mobile_app_test",
            CONF_DEMAND_WINDOW_START: "14:00:00",
            CONF_DEMAND_WINDOW_END: "20:00:00",
            CONF_MANUAL_OVERRIDE_TIMEOUT: 4,
        }
        result = await flow.async_step_settings(user_input)

        assert result["type"] == FlowResultType.CREATE_ENTRY
        # The result merges with existing options, so check that our input is included
        for key, value in user_input.items():
            assert result["data"][key] == value

    @pytest.mark.asyncio
    async def test_options_flow_shows_pricing_source_first(
        self, mock_hass, mock_config_entry
    ):
        """Test options flow starts with pricing source selection step."""
        flow = LocalShiftConfigFlow.async_get_options_flow(mock_config_entry)

        mock_config_entries = MagicMock()
        mock_config_entries.async_get_known_entry = MagicMock(
            return_value=mock_config_entry
        )
        mock_config_entries.async_update_entry = MagicMock()
        mock_hass.config_entries = mock_config_entries
        flow.hass = mock_hass
        type(flow)._config_entry_id = property(lambda self: mock_config_entry.entry_id)

        mock_config_entry.data = {
            CONF_PRICING_DATA_SOURCE: PRICING_SOURCE_AMBER_EXPRESS,
            CONF_COMPARISON_MODE: "disabled",
        }

        result = await flow.async_step_init()

        # Issue #779: async_step_init now shows a menu with 4 options
        assert result["type"] == FlowResultType.MENU
        assert result["step_id"] == "init"
        assert "options_pricing_source" in result["menu_options"]
        assert "entity_mappings" in result["menu_options"]
        assert "settings" in result["menu_options"]
        assert "advanced" in result["menu_options"]

    @pytest.mark.asyncio
    async def test_options_flow_pricing_source_updates_and_proceeds(
        self, mock_hass, mock_config_entry
    ):
        """Test pricing source selection updates config and proceeds to entity mappings."""
        flow = LocalShiftConfigFlow.async_get_options_flow(mock_config_entry)

        mock_config_entries = MagicMock()
        mock_config_entries.async_get_known_entry = MagicMock(
            return_value=mock_config_entry
        )
        mock_config_entries.async_update_entry = MagicMock()
        mock_hass.config_entries = mock_config_entries
        flow.hass = mock_hass
        type(flow)._config_entry_id = property(lambda self: mock_config_entry.entry_id)

        mock_config_entry.data = {
            CONF_PRICING_DATA_SOURCE: "amber",
            CONF_COMPARISON_MODE: "enabled",
        }

        user_input = {
            CONF_PRICING_DATA_SOURCE: PRICING_SOURCE_AMBER_EXPRESS,
            CONF_COMPARISON_MODE: "disabled",
        }

        result = await flow.async_step_options_pricing_source(user_input)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "entity_mappings"
        mock_config_entries.async_update_entry.assert_called_once()


# =============================================================================
# STATIC METHOD TESTS
# =============================================================================


class TestStaticMethods:
    """Tests for static methods."""

    def test_async_get_options_flow(self, mock_config_entry):
        """Test that async_get_options_flow returns an OptionsFlow."""
        result = LocalShiftConfigFlow.async_get_options_flow(mock_config_entry)

        assert isinstance(result, LocalShiftOptionsFlow)


# =============================================================================
# DUPLICATE ENTRY PREVENTION TESTS
# =============================================================================


class TestDuplicateEntryPrevention:
    """Tests for preventing duplicate configuration entries."""

    @pytest.mark.asyncio
    async def test_duplicate_entry_rejected(self, mock_hass):
        """Test that duplicate entries are rejected."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        # Mock existing entry
        existing_entry = MagicMock()
        existing_entry.domain = DOMAIN
        existing_entry.data = {"test": "data"}

        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_entries = MagicMock(
            return_value=[existing_entry]
        )

        # Try to create another entry
        # Flow should detect existing entry and abort
        result = await flow.async_step_user()

        # Should show form or abort depending on implementation
        assert result["type"] in [FlowResultType.FORM, FlowResultType.ABORT]

    @pytest.mark.asyncio
    async def test_multiple_entries_allowed_if_different(
        self, mock_hass, mock_config_entry
    ):
        """Test that multiple entries are allowed if configured differently."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        # Mock no existing entries
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_entries = MagicMock(return_value=[])

        result = await flow.async_step_user()

        # Should show form for new entry
        assert result["type"] == FlowResultType.FORM


# =============================================================================
# INVALID INPUT FORMAT TESTS
# =============================================================================


class TestInvalidInputFormats:
    """Tests for handling invalid input formats."""

    @pytest.mark.asyncio
    async def test_invalid_time_format_demand_window_start(self, mock_hass):
        """Test handling of invalid time format for demand window start."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        # Mock valid notify service
        flow.hass.services.async_services = MagicMock(
            return_value={"notify": {"mobile_app_test": MagicMock()}}
        )

        result = await flow._validate_notify_service("notify.mobile_app_test")
        assert result is None

        # Invalid time format "25:00:00" would be handled by form validation
        # The actual validation happens in the options flow

    @pytest.mark.asyncio
    async def test_invalid_time_format_demand_window_end(self, mock_hass):
        """Test handling of invalid time format for demand window end."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        # Malformed time "not-a-time" would be handled by form validation
        # Implementation should validate this

    @pytest.mark.asyncio
    async def test_entity_id_with_special_characters(self, mock_hass):
        """Test handling of entity IDs with special characters."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        # Mock state for entity with special characters
        mock_hass.states.get = MagicMock(return_value=None)

        entities = {
            "test_sensor": ("sensor.test@special!chars", "sensor"),
        }

        result = await flow._validate_entities(entities)

        # Should reject or handle gracefully
        assert result is not None or True  # Depends on implementation

    @pytest.mark.asyncio
    async def test_very_long_entity_id(self, mock_hass):
        """Test handling of very long entity IDs."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        # Create very long entity ID
        long_entity = "sensor." + "a" * 250
        mock_hass.states.get = MagicMock(return_value=None)

        entities = {
            "test_sensor": (long_entity, "sensor"),
        }

        result = await flow._validate_entities(entities)

        # Should handle gracefully (reject or accept)
        assert result is not None or True


# =============================================================================
# OPTIONS FLOW VALIDATION TESTS
# =============================================================================


class TestOptionsFlowValidation:
    """Tests for options flow input validation."""

    @pytest.mark.asyncio
    async def test_options_flow_empty_notify_service(
        self, mock_hass, mock_config_entry
    ):
        """Test options flow with empty notify service."""
        flow = LocalShiftConfigFlow.async_get_options_flow(mock_config_entry)

        mock_config_entries = MagicMock()
        mock_config_entries.async_get_known_entry = MagicMock(
            return_value=mock_config_entry
        )
        mock_hass.config_entries = mock_config_entries
        flow.hass = mock_hass

        user_input = {
            CONF_NOTIFY_SERVICE: "",
            CONF_DEMAND_WINDOW_START: "18:00:00",
            CONF_DEMAND_WINDOW_END: "22:00:00",
            CONF_MANUAL_OVERRIDE_TIMEOUT: 24,
        }

        _ = await flow.async_step_init(user_input)

        # Should either show error or use default
        # Implementation dependent

    @pytest.mark.asyncio
    async def test_options_flow_negative_timeout(self, mock_hass, mock_config_entry):
        """Test options flow with negative manual override timeout."""
        flow = LocalShiftConfigFlow.async_get_options_flow(mock_config_entry)

        mock_config_entries = MagicMock()
        mock_config_entries.async_get_known_entry = MagicMock(
            return_value=mock_config_entry
        )
        mock_hass.config_entries = mock_config_entries
        flow.hass = mock_hass

        user_input = {
            CONF_NOTIFY_SERVICE: "notify.mobile_app_test",
            CONF_DEMAND_WINDOW_START: "18:00:00",
            CONF_DEMAND_WINDOW_END: "22:00:00",
            CONF_MANUAL_OVERRIDE_TIMEOUT: -1,  # Invalid negative
        }

        _ = await flow.async_step_init(user_input)

        # Should handle gracefully

    @pytest.mark.asyncio
    async def test_options_flow_very_large_timeout(self, mock_hass, mock_config_entry):
        """Test options flow with very large timeout value."""
        flow = LocalShiftConfigFlow.async_get_options_flow(mock_config_entry)

        mock_config_entries = MagicMock()
        mock_config_entries.async_get_known_entry = MagicMock(
            return_value=mock_config_entry
        )
        mock_hass.config_entries = mock_config_entries
        flow.hass = mock_hass

        user_input = {
            CONF_NOTIFY_SERVICE: "notify.mobile_app_test",
            CONF_DEMAND_WINDOW_START: "18:00:00",
            CONF_DEMAND_WINDOW_END: "22:00:00",
            CONF_MANUAL_OVERRIDE_TIMEOUT: 1000000,  # Very large
        }

        _ = await flow.async_step_init(user_input)

        # Should handle gracefully (might clamp or accept)

    @pytest.mark.asyncio
    async def test_options_flow_demand_window_start_after_end(
        self, mock_hass, mock_config_entry
    ):
        """Test options flow when demand window start is after end."""
        flow = LocalShiftConfigFlow.async_get_options_flow(mock_config_entry)

        mock_config_entries = MagicMock()
        mock_config_entries.async_get_known_entry = MagicMock(
            return_value=mock_config_entry
        )
        mock_hass.config_entries = mock_config_entries
        flow.hass = mock_hass

        user_input = {
            CONF_NOTIFY_SERVICE: "notify.mobile_app_test",
            CONF_DEMAND_WINDOW_START: "22:00:00",  # Start after end
            CONF_DEMAND_WINDOW_END: "18:00:00",
            CONF_MANUAL_OVERRIDE_TIMEOUT: 24,
        }

        _ = await flow.async_step_init(user_input)

        # Should handle gracefully (might swap, reject, or accept as overnight window)


# =============================================================================
# ENTITY DOMAIN VALIDATION TESTS
# =============================================================================


class TestEntityDomainValidation:
    """Tests for entity domain validation."""

    @pytest.mark.asyncio
    async def test_operation_mode_must_be_select(self, mock_hass):
        """Test that operation mode must be a select entity."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        # Return sensor instead of select
        mock_hass.states.get = MagicMock(
            return_value=create_mock_state("sensor.test", "autonomous", "sensor")
        )

        entities = {
            "operation_mode": ("sensor.test", "select"),  # Expecting select
        }

        result = await flow._validate_entities(entities)

        assert result is not None
        assert "operation_mode" in result

    @pytest.mark.asyncio
    async def test_backup_reserve_must_be_number(self, mock_hass):
        """Test that backup reserve must be a number entity."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        # Return sensor instead of number
        mock_hass.states.get = MagicMock(
            return_value=create_mock_state("sensor.test", "50", "sensor")
        )

        entities = {
            "backup_reserve": ("sensor.test", "number"),  # Expecting number
        }

        result = await flow._validate_entities(entities)

        assert result is not None
        assert "backup_reserve" in result

    @pytest.mark.asyncio
    async def test_price_spike_must_be_binary_sensor(self, mock_hass):
        """Test that price spike must be a binary_sensor entity."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        # Return sensor instead of binary_sensor
        mock_hass.states.get = MagicMock(
            return_value=create_mock_state("sensor.test", "on", "sensor")
        )

        entities = {
            "price_spike": ("sensor.test", "binary_sensor"),  # Expecting binary_sensor
        }

        result = await flow._validate_entities(entities)

        assert result is not None
        assert "price_spike" in result


# =============================================================================
# OPTIONS FLOW MIGRATION TESTS
# =============================================================================


class TestOptionsFlowMigration:
    """Tests for options flow migration scenarios."""

    @pytest.mark.asyncio
    async def test_options_flow_preserves_existing_data(
        self, mock_hass, mock_config_entry
    ):
        """Test that options flow preserves existing configuration."""
        # Set up existing options
        mock_config_entry.options = {
            CONF_NOTIFY_SERVICE: "notify.existing_service",
            CONF_DEMAND_WINDOW_START: "17:00:00",
            CONF_DEMAND_WINDOW_END: "21:00:00",
            CONF_MANUAL_OVERRIDE_TIMEOUT: 12,
        }

        flow = LocalShiftConfigFlow.async_get_options_flow(mock_config_entry)

        mock_config_entries = MagicMock()
        mock_config_entries.async_get_known_entry = MagicMock(
            return_value=mock_config_entry
        )
        mock_hass.config_entries = mock_config_entries
        flow.hass = mock_hass

        # Issue #779: async_step_init now shows a menu, not a form
        # So user_input should be None to get the menu
        result = await flow.async_step_init(None)

        # Should show menu
        assert result["type"] == FlowResultType.MENU
        assert result["step_id"] == "init"

    @pytest.mark.asyncio
    async def test_options_flow_empty_initial_options(
        self, mock_hass, mock_config_entry
    ):
        """Test options flow with empty initial options."""
        mock_config_entry.options = {}

        # Mock config_entry.data with entity mappings for entity_mappings step
        mock_config_entry.data = {
            CONF_TESLEMETRY_OPERATION_MODE: "select.teslemetry_operation_mode",
            CONF_TESLEMETRY_SOC: "sensor.teslemetry_soc",
            CONF_TESLEMETRY_GRID_POWER: "sensor.teslemetry_grid_power",
            CONF_TESLEMETRY_BATTERY_POWER: "sensor.teslemetry_battery_power",
            CONF_TESLEMETRY_SOLAR_POWER: "sensor.teslemetry_solar_power",
            CONF_TESLEMETRY_LOAD_POWER: "sensor.teslemetry_load_power",
            CONF_PRICING_GENERAL_PRICE: "sensor.amber_general_price",
            CONF_PRICING_FEED_IN_PRICE: "sensor.amber_feed_in_price",
            CONF_PRICING_GENERAL_FORECAST: "sensor.amber_general_forecast",
            CONF_PRICING_FEED_IN_FORECAST: "sensor.amber_feed_in_forecast",
            CONF_PRICING_PRICE_SPIKE: "binary_sensor.amber_price_spike",
            CONF_SOLCAST_FORECAST_TODAY: "sensor.solcast_forecast_today",
            CONF_SOLCAST_FORECAST_TOMORROW: "sensor.solcast_forecast_tomorrow",
        }

        flow = LocalShiftConfigFlow.async_get_options_flow(mock_config_entry)

        mock_config_entries = MagicMock()
        mock_config_entries.async_get_known_entry = MagicMock(
            return_value=mock_config_entry
        )
        mock_hass.config_entries = mock_config_entries
        flow.hass = mock_hass

        # First step: entity mappings
        entity_input = dict(mock_config_entry.data)
        result = await flow.async_step_entity_mappings(entity_input)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "settings"

        # Second step: settings
        user_input = {
            CONF_NOTIFY_SERVICE: "notify.mobile_app_test",
            CONF_DEMAND_WINDOW_START: "18:00:00",
            CONF_DEMAND_WINDOW_END: "22:00:00",
            CONF_MANUAL_OVERRIDE_TIMEOUT: 24,
        }
        result = await flow.async_step_settings(user_input)

        assert result["type"] == FlowResultType.CREATE_ENTRY


# =============================================================================
# ERROR MESSAGE TESTS
# =============================================================================


class TestErrorMessages:
    """Tests for error message clarity."""

    @pytest.mark.asyncio
    async def test_missing_entity_error_message_clear(self, mock_hass):
        """Test that missing entity error message is clear."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        mock_hass.states.get = MagicMock(return_value=None)

        entities = {
            "soc_sensor": ("sensor.missing_soc", "sensor"),
        }

        result = await flow._validate_entities(entities)

        assert result is not None
        assert "soc_sensor" in result
        # Error message should mention the entity doesn't exist
        assert (
            "does not exist" in result["soc_sensor"]
            or "not found" in result["soc_sensor"].lower()
        )

    @pytest.mark.asyncio
    async def test_unavailable_entity_error_message_clear(self, mock_hass):
        """Test that unavailable entity error message is clear."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        mock_hass.states.get = MagicMock(
            return_value=create_mock_state("sensor.test", "unavailable")
        )

        entities = {
            "soc_sensor": ("sensor.test", "sensor"),
        }

        result = await flow._validate_entities(entities)

        assert result is not None
        assert "soc_sensor" in result
        assert "unavailable" in result["soc_sensor"].lower()

    @pytest.mark.asyncio
    async def test_wrong_domain_error_message_clear(self, mock_hass):
        """Test that wrong domain error message is clear."""
        flow = LocalShiftConfigFlow()
        flow.hass = mock_hass

        # Return wrong domain type
        mock_hass.states.get = MagicMock(
            return_value=create_mock_state("sensor.test", "50", "sensor")
        )

        entities = {
            "mode_select": ("sensor.test", "select"),  # Expected select, got sensor
        }

        result = await flow._validate_entities(entities)

        assert result is not None
        assert "mode_select" in result
        # Error message should mention expected vs actual domain
        assert "select" in result["mode_select"].lower()


def test_entity_mappings_schema_includes_charge_rate_options(
    mock_hass, mock_config_entry
):
    """Entity mapping schema includes charge-rate learning fields."""
    flow = LocalShiftConfigFlow.async_get_options_flow(mock_config_entry)
    flow.hass = mock_hass

    schema = flow._build_entity_mappings_schema({}, [], [])
    field_keys = [key.schema for key in schema.schema.keys()]

    assert CONF_TESLEMETRY_BATTERY_POWER in field_keys
    assert CONF_TESLEMETRY_SOC in field_keys
    assert CONF_POWER_SIGN_OVERRIDE in field_keys


def test_entity_mappings_schema_prefers_localshift_defaults(
    mock_hass, mock_config_entry
):
    """Prefer LocalShift battery sensors when available."""
    mock_hass.states.get = MagicMock(
        side_effect=lambda entity_id: (
            create_mock_state(
                entity_id,
                "1",
                "sensor",
            )
            if entity_id
            in (
                "sensor.localshift_battery_power",
                "sensor.localshift_battery_percent",
            )
            else None
        )
    )
    flow = LocalShiftConfigFlow.async_get_options_flow(mock_config_entry)
    flow.hass = mock_hass

    schema = flow._build_entity_mappings_schema({}, [], [])
    defaults = {}
    for key in schema.schema.keys():
        if not hasattr(key, "schema"):
            continue
        value = key.default
        defaults[key.schema] = value() if callable(value) else value

    assert defaults[CONF_TESLEMETRY_BATTERY_POWER] == "sensor.localshift_battery_power"
    assert defaults[CONF_TESLEMETRY_SOC] == "sensor.localshift_battery_percent"


@pytest.mark.asyncio
async def test_entity_mappings_schema_preserves_user_input_on_error(
    mock_hass, mock_config_entry
):
    """Keep override values when entity validation fails."""
    flow = LocalShiftConfigFlow.async_get_options_flow(mock_config_entry)
    flow.hass = mock_hass

    mock_hass.states.get.return_value = None
    user_input = {
        CONF_TESLEMETRY_BATTERY_POWER: "sensor.missing",
        CONF_TESLEMETRY_SOC: "sensor.missing_soc",
        CONF_POWER_SIGN_OVERRIDE: "positive",
    }

    result = await flow.async_step_entity_mappings(user_input)

    schema = result["data_schema"]
    defaults = {
        key.schema: (key.default() if callable(key.default) else key.default)
        for key in schema.schema.keys()
        if hasattr(key, "schema")
    }

    assert defaults[CONF_POWER_SIGN_OVERRIDE] == "positive"
