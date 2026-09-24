"""Tests for the optional away entity (holiday mode).

Docs: docs/holiday-away/plan.md "What to build" item 1. The away entity is any
on/off entity that is on while the house is empty (the house points it at
``input_boolean.holiday_mode``). Unset means today's behaviour, exactly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import FlowResultType

from custom_components.localshift.config_flow import (
    LocalShiftConfigFlow,
    LocalShiftOptionsFlow,
)
from custom_components.localshift.config_flow.schemas import (
    build_away_entity_schema,
    build_solcast_schema,
)
from custom_components.localshift.config_flow.validators import validate_away_entity
from custom_components.localshift.const import (
    CONF_AWAY_ENTITY,
    CONF_NOTIFY_SERVICE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    DEFAULT_AWAY_ENTITY,
    DEFAULT_ENTITY_IDS,
    DOMAIN,
)
from custom_components.localshift.utils.away import (
    away_state_unknown,
    get_away_entity_id,
    is_away_active,
)
from tests.test_config_flow import create_mock_state, make_options_flow

# =============================================================================
# FIXTURES (mirrors of tests/test_config_flow.py's)
# =============================================================================


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.states = MagicMock()
    hass.services = MagicMock()
    hass.services.async_services.return_value = {
        "notify": {"mobile_app_test": MagicMock()}
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


def _hass_with_state(state):
    """Build a mock hass whose states.get returns the given state object."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=state)
    return hass


def _entry_with_options(options):
    """Build a mock config entry with the given options."""
    entry = MagicMock()
    entry.options = options
    entry.data = {}
    return entry


def _away_marker(schema):
    """Return the away entity marker from a voluptuous schema."""
    return next(
        k for k in schema.schema if getattr(k, "schema", None) == CONF_AWAY_ENTITY
    )


# =============================================================================
# A. CONSTANTS
# =============================================================================


class TestAwayConstants:
    """Tests for the away entity constants."""

    def test_away_entity_constant_values(self):
        """The config key and its default are stable public constants."""
        assert CONF_AWAY_ENTITY == "away_entity"
        assert DEFAULT_AWAY_ENTITY == ""

    def test_away_entity_not_a_default_entity_id(self):
        """Away is an option, never a default entity mapping."""
        assert CONF_AWAY_ENTITY not in DEFAULT_ENTITY_IDS


# =============================================================================
# B. SCHEMA
# =============================================================================


class TestBuildAwayEntitySchema:
    """Tests for build_away_entity_schema."""

    def test_unset_key_is_optional_with_no_default(self):
        """Issue #955 guard: a blank form must submit cleanly."""
        schema = build_away_entity_schema()

        marker = _away_marker(schema)
        assert isinstance(marker, vol.Optional)
        assert marker.default is vol.UNDEFINED

        assert schema({}) == {}

    def test_current_value_becomes_suggested_value(self):
        """A stored entity is offered back, but the key stays optional."""
        schema = build_away_entity_schema("input_boolean.holiday_mode")

        marker = _away_marker(schema)
        assert marker.description == {"suggested_value": "input_boolean.holiday_mode"}

        assert schema({}) == {}

    @pytest.mark.parametrize(
        "entity_id", ["input_boolean.x", "binary_sensor.x", "switch.x"]
    )
    def test_accepts_any_on_off_domain(self, entity_id):
        """Any on/off domain may be chosen; the selector is not domain-locked."""
        schema = build_away_entity_schema(entity_id)

        assert schema({CONF_AWAY_ENTITY: entity_id}) == {CONF_AWAY_ENTITY: entity_id}


# =============================================================================
# C. VALIDATOR
# =============================================================================


class TestValidateAwayEntity:
    """Tests for validate_away_entity."""

    async def test_unset_none_skips_state_lookup(self):
        """A None away entity never touches the state machine."""
        hass = MagicMock()
        hass.states.get = MagicMock(side_effect=AssertionError("should not call"))

        assert await validate_away_entity(hass, None) is None
        hass.states.get.assert_not_called()

    async def test_unset_empty_skips_state_lookup(self):
        """An empty away entity never touches the state machine."""
        hass = MagicMock()
        hass.states.get = MagicMock(side_effect=AssertionError("should not call"))

        assert await validate_away_entity(hass, "") is None
        hass.states.get.assert_not_called()

    @pytest.mark.parametrize("state", ["on", "off"])
    async def test_on_and_off_are_valid(self, state):
        """Both rest states of an on/off entity pass."""
        hass = _hass_with_state(create_mock_state("input_boolean.away", state))

        assert await validate_away_entity(hass, "input_boolean.away") is None

    @pytest.mark.parametrize(
        "entity_id", ["input_boolean.x", "binary_sensor.x", "switch.x"]
    )
    async def test_any_on_off_domain_is_valid(self, entity_id):
        """Domain is not restricted to input_boolean."""
        hass = _hass_with_state(create_mock_state(entity_id, "off"))

        assert await validate_away_entity(hass, entity_id) is None

    async def test_missing_entity_reports_does_not_exist(self):
        """An entity the state machine has never seen is reported."""
        hass = _hass_with_state(None)

        result = await validate_away_entity(hass, "input_boolean.gone")

        assert result == "Entity 'input_boolean.gone' does not exist"

    @pytest.mark.parametrize("state", ["unavailable", "unknown"])
    async def test_unavailable_or_unknown_passes(self, state):
        """#1084: a temporarily unreadable entity doesn't block Settings; the
        engine holds the last known away state through the gap."""
        hass = _hass_with_state(create_mock_state("input_boolean.away", state))

        assert await validate_away_entity(hass, "input_boolean.away") is None

    async def test_non_on_off_state_reported(self):
        """A sensor holding a numeric state is not an on/off entity."""
        hass = _hass_with_state(create_mock_state("sensor.load", "23.4"))

        result = await validate_away_entity(hass, "sensor.load")

        assert result == (
            "Entity 'sensor.load' is not an on/off entity (state is '23.4')"
        )


# =============================================================================
# D. HELPER
# =============================================================================


class TestAwayHelper:
    """Tests for utils/away.py."""

    async def test_option_absent_reports_not_away(self):
        """No away entity configured means no change and no state read."""
        entry = _entry_with_options({})
        hass = MagicMock()
        hass.states.get = MagicMock(side_effect=AssertionError("should not call"))

        assert get_away_entity_id(entry) is None
        assert is_away_active(hass, entry) is False
        hass.states.get.assert_not_called()

    async def test_option_empty_reports_not_away(self):
        """An empty away entity option means no change and no state read."""
        entry = _entry_with_options({CONF_AWAY_ENTITY: ""})
        hass = MagicMock()
        hass.states.get = MagicMock(side_effect=AssertionError("should not call"))

        assert get_away_entity_id(entry) is None
        assert is_away_active(hass, entry) is False
        hass.states.get.assert_not_called()

    async def test_on_means_away(self):
        """An on entity reports away."""
        entry = _entry_with_options({CONF_AWAY_ENTITY: "input_boolean.holiday_mode"})
        hass = _hass_with_state(create_mock_state("input_boolean.holiday_mode", "on"))

        assert get_away_entity_id(entry) == "input_boolean.holiday_mode"
        assert is_away_active(hass, entry) is True

    async def test_off_means_home(self):
        """An off entity reports home."""
        entry = _entry_with_options({CONF_AWAY_ENTITY: "input_boolean.holiday_mode"})
        hass = _hass_with_state(create_mock_state("input_boolean.holiday_mode", "off"))

        assert is_away_active(hass, entry) is False

    async def test_missing_entity_is_not_away(self):
        """A vanished entity fails home rather than raising."""
        entry = _entry_with_options({CONF_AWAY_ENTITY: "input_boolean.gone"})
        hass = _hass_with_state(None)

        assert is_away_active(hass, entry) is False

    @pytest.mark.parametrize("state", ["unavailable", "unknown"])
    async def test_unavailable_entity_is_not_away(self, state):
        """Unavailable/unknown fails home."""
        entry = _entry_with_options({CONF_AWAY_ENTITY: "input_boolean.holiday_mode"})
        hass = _hass_with_state(create_mock_state("input_boolean.holiday_mode", state))

        assert is_away_active(hass, entry) is False

    @pytest.mark.parametrize(
        ("state", "expected"),
        [(None, True), ("unavailable", True), ("unknown", True), ("on", False), ("off", False)],
    )
    async def test_away_state_unknown(self, state, expected):
        """Missing, unavailable or unknown can't be read; on and off can."""
        entry = _entry_with_options({CONF_AWAY_ENTITY: "input_boolean.holiday_mode"})
        hass = _hass_with_state(
            None
            if state is None
            else create_mock_state("input_boolean.holiday_mode", state)
        )

        assert away_state_unknown(hass, entry) is expected

    async def test_away_state_unknown_false_when_unset(self):
        """No away entity configured is not 'unknown': it is plainly home."""
        entry = _entry_with_options({})
        hass = MagicMock()
        hass.states.get = MagicMock(side_effect=AssertionError("should not call"))

        assert away_state_unknown(hass, entry) is False

    async def test_options_are_the_single_source(self):
        """A value only in entry.data is ignored."""
        entry = MagicMock()
        entry.options = {}
        entry.data = {CONF_AWAY_ENTITY: "input_boolean.holiday_mode"}
        hass = _hass_with_state(create_mock_state("input_boolean.holiday_mode", "on"))

        assert get_away_entity_id(entry) is None
        assert is_away_active(hass, entry) is False


# =============================================================================
# E. OPTIONS FLOW: SETTINGS STEP
# =============================================================================


def _settings_input(**overrides):
    """Build a submit payload for the settings step."""
    payload = {
        CONF_NOTIFY_SERVICE: "notify.mobile_app_test",
        "demand_window_start": "18:00:00",
        "demand_window_end": "22:00:00",
        "manual_override_timeout": 4,
    }
    payload.update(overrides)
    return payload


class TestSettingsStepAwayEntity:
    """Tests for the settings step of the options flow."""

    async def test_render_prefills_stored_away_entity(
        self, mock_hass, mock_config_entry
    ):
        """The stored entity comes back as a suggested value."""
        mock_config_entry.options = {
            CONF_NOTIFY_SERVICE: "notify.mobile_app_test",
            CONF_AWAY_ENTITY: "input_boolean.holiday_mode",
        }
        flow = make_options_flow(mock_hass, mock_config_entry)

        result = await flow.async_step_settings(None)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "settings"
        marker = _away_marker(result["data_schema"])
        assert marker.description == {"suggested_value": "input_boolean.holiday_mode"}

    async def test_render_without_stored_entity_has_no_suggestion(
        self, mock_hass, mock_config_entry
    ):
        """With nothing stored the marker carries no suggested value."""
        mock_config_entry.options = {}
        flow = make_options_flow(mock_hass, mock_config_entry)

        result = await flow.async_step_settings(None)

        marker = _away_marker(result["data_schema"])
        assert marker.description is None

    async def test_submit_valid_entity_saves_option(self, mock_hass, mock_config_entry):
        """A valid away entity is stored in options."""
        flow = make_options_flow(mock_hass, mock_config_entry)
        mock_config_entry.options = {CONF_NOTIFY_SERVICE: "notify.mobile_app_test"}
        mock_hass.states.get = MagicMock(
            return_value=create_mock_state("input_boolean.holiday_mode", "off")
        )

        result = await flow.async_step_settings(
            _settings_input(**{CONF_AWAY_ENTITY: "input_boolean.holiday_mode"})
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_AWAY_ENTITY] == "input_boolean.holiday_mode"

    async def test_submit_missing_entity_reshows_form_with_error(
        self, mock_hass, mock_config_entry
    ):
        """A bad away entity keeps the user on the form with the value kept."""
        flow = make_options_flow(mock_hass, mock_config_entry)
        mock_config_entry.options = {CONF_NOTIFY_SERVICE: "notify.mobile_app_test"}
        mock_hass.states.get = MagicMock(return_value=None)

        result = await flow.async_step_settings(
            _settings_input(**{CONF_AWAY_ENTITY: "input_boolean.gone"})
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "settings"
        assert result["errors"][CONF_AWAY_ENTITY] == (
            "Entity 'input_boolean.gone' does not exist"
        )
        marker = _away_marker(result["data_schema"])
        assert marker.description == {"suggested_value": "input_boolean.gone"}

    async def test_clearing_the_entity_writes_the_default(
        self, mock_hass, mock_config_entry
    ):
        """Omitting the key clears a previously stored away entity."""
        flow = make_options_flow(mock_hass, mock_config_entry)
        mock_config_entry.options = {
            CONF_NOTIFY_SERVICE: "notify.mobile_app_test",
            CONF_AWAY_ENTITY: "input_boolean.holiday_mode",
            "battery_target": 90,
        }
        mock_hass.states.get = MagicMock(
            side_effect=AssertionError("away unset; states not read")
        )

        result = await flow.async_step_settings(_settings_input())

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_AWAY_ENTITY] == DEFAULT_AWAY_ENTITY
        assert result["data"]["battery_target"] == 90


# =============================================================================
# F. INITIAL CONFIG FLOW: SOLCAST STEP
# =============================================================================


def _solcast_input(**overrides):
    """Build a submit payload for the solcast step."""
    payload = {
        CONF_SOLCAST_FORECAST_TODAY: "sensor.solcast_today",
        CONF_SOLCAST_FORECAST_TOMORROW: "sensor.solcast_tomorrow",
        CONF_NOTIFY_SERVICE: "notify.mobile_app_test",
    }
    payload.update(overrides)
    return payload


def _make_config_flow(hass):
    """Build a config flow positioned at the solcast step."""
    flow = LocalShiftConfigFlow()
    flow.hass = hass
    flow._teslemetry_data = {}
    flow._pricing_source_data = {}
    flow._pricing_data = {}
    return flow


class TestSolcastStepAwayEntity:
    """Tests for the initial config flow's solcast step."""

    async def test_rendered_form_contains_away_key(self, mock_hass):
        """The rendered solcast form offers the away entity."""
        flow = _make_config_flow(mock_hass)
        mock_hass.states.async_all = MagicMock(return_value=[])

        result = await flow.async_step_solcast()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "solcast"
        keys = {getattr(k, "schema", None) for k in result["data_schema"].schema}
        assert CONF_AWAY_ENTITY in keys

    async def test_valid_entity_saved_in_options_not_data(self, mock_hass):
        """The away entity lands in options, leaving data untouched."""
        flow = _make_config_flow(mock_hass)

        def mock_get_state(entity_id):
            if entity_id == "input_boolean.holiday_mode":
                return create_mock_state(entity_id, "on", "input_boolean")
            return create_mock_state(entity_id, "10", "sensor")

        mock_hass.states.get = mock_get_state

        result = await flow.async_step_solcast(
            _solcast_input(**{CONF_AWAY_ENTITY: "input_boolean.holiday_mode"})
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["options"][CONF_AWAY_ENTITY] == "input_boolean.holiday_mode"
        assert CONF_AWAY_ENTITY not in result["data"]

    async def test_no_away_entity_writes_default(self, mock_hass):
        """Leaving the field blank stores the default, not a stale value."""
        flow = _make_config_flow(mock_hass)
        mock_hass.states.get = MagicMock(
            return_value=create_mock_state("sensor.x", "10", "sensor")
        )

        result = await flow.async_step_solcast(_solcast_input())

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["options"][CONF_AWAY_ENTITY] == DEFAULT_AWAY_ENTITY

    async def test_invalid_entity_reshows_form_with_error(self, mock_hass):
        """An away entity that does not exist blocks the step."""
        flow = _make_config_flow(mock_hass)

        def mock_get_state(entity_id):
            if entity_id == "input_boolean.gone":
                return None
            return create_mock_state(entity_id, "10", "sensor")

        mock_hass.states.get = mock_get_state

        result = await flow.async_step_solcast(
            _solcast_input(**{CONF_AWAY_ENTITY: "input_boolean.gone"})
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "solcast"
        assert result["errors"][CONF_AWAY_ENTITY] == (
            "Entity 'input_boolean.gone' does not exist"
        )


# =============================================================================
# G. ISOLATION
# =============================================================================


class TestAwayEntityIsolation:
    """The away entity must not leak into other schemas or mappings."""

    def test_entity_mappings_schema_has_no_away_key(self):
        """The entity mappings step never validates or stores away."""
        flow = LocalShiftOptionsFlow()
        schema = flow._build_entity_mappings_schema(
            {
                "teslemetry_operation_mode": "select.teslemetry_operation_mode",
                "teslemetry_backup_reserve": "number.teslemetry_backup_reserve",
                "teslemetry_soc": "sensor.teslemetry_soc",
                "teslemetry_grid_power": "sensor.teslemetry_grid_power",
                "teslemetry_battery_power": "sensor.teslemetry_battery_power",
                "teslemetry_solar_power": "sensor.teslemetry_solar_power",
                "teslemetry_load_power": "sensor.teslemetry_load_power",
                "pricing_general_price": "sensor.100h_general_price",
                "pricing_feed_in_price": "sensor.100h_feed_in_price",
                "pricing_general_forecast": "sensor.100h_general_forecast",
                "pricing_feed_in_forecast": "sensor.100h_feed_in_forecast",
                "pricing_price_spike": "binary_sensor.100h_price_spike",
                "solcast_forecast_today": "sensor.solcast_forecast_today",
                "solcast_forecast_tomorrow": "sensor.solcast_forecast_tomorrow",
            },
            ["notify.mobile_app_test"],
            [],
        )

        keys = {getattr(k, "schema", None) for k in schema.schema}
        assert CONF_AWAY_ENTITY not in keys

    def test_build_solcast_schema_unchanged(self):
        """build_solcast_schema itself has no away key; the flow extends it."""
        schema = build_solcast_schema([], [])

        keys = {getattr(k, "schema", None) for k in schema.schema}
        assert CONF_AWAY_ENTITY not in keys
