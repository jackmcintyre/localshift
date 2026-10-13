"""Entries written before #983 still set up (issue #983).

Four option keys had no reader and no entity: ``cheap_price_deadband``,
``forecast_lookahead_hours``, ``export_min_spread`` and
``spike_price_percentile``. Setup used to write the first two into every new
entry, so existing installs have them stored. ``teslemetry_solar_energy`` was
a default entity id nothing resolved.

The constants are gone; the stored keys are not, and there is deliberately no
migration that strips them. These tests pin the two halves of that:

- an entry that still carries the retired keys sets up, builds its planner
  options and saves its options flow exactly as before, with the leftover
  keys ignored and left in place;
- a new entry no longer gets them written.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.localshift import const
from custom_components.localshift.computation_engine import ComputationEngine
from custom_components.localshift.config_flow import LocalShiftConfigFlow
from custom_components.localshift.const import (
    CONF_BATTERY_TARGET,
    CONF_COMPARISON_MODE,
    CONF_NOTIFY_SERVICE,
    CONF_PRICING_DATA_SOURCE,
    CONF_PRICING_GENERAL_PRICE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_TESLEMETRY_OPERATION_MODE,
    DEFAULT_ENTITY_IDS,
    PLATFORMS,
    THRESHOLD_RANGES,
)
from custom_components.localshift.coordinator import LocalShiftCoordinator

# Literal strings on purpose: the constants that named them no longer exist.
RETIRED_OPTIONS = {
    "cheap_price_deadband": 0.03,
    "forecast_lookahead_hours": 2.0,
    "export_min_spread": 0.05,
    "spike_price_percentile": 75,
}
RETIRED_ENTITY_KEY = "teslemetry_solar_energy"


def _legacy_options() -> dict:
    return {
        CONF_NOTIFY_SERVICE: "notify.mobile_app_test",
        CONF_BATTERY_TARGET: 90,
        **RETIRED_OPTIONS,
    }


def _legacy_entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "legacy_entry"
    entry.data = {RETIRED_ENTITY_KEY: "sensor.my_home_solar_energy"}
    entry.options = _legacy_options()
    return entry


def test_retired_keys_have_no_constant_and_no_table_row():
    """Nothing in const.py still names a retired key."""
    conf_values = {
        value
        for name, value in vars(const).items()
        if name.startswith("CONF_") and isinstance(value, str)
    }
    for key in (*RETIRED_OPTIONS, RETIRED_ENTITY_KEY):
        assert key not in conf_values, f"{key} still has a CONF_ constant"
        assert key not in THRESHOLD_RANGES, f"{key} still has a THRESHOLD_RANGES row"
        assert key not in DEFAULT_ENTITY_IDS, f"{key} still has a default entity id"


@pytest.mark.asyncio
async def test_every_platform_sets_up_with_retired_keys_stored():
    """All six platforms set up from an entry that still carries the keys."""
    hass = MagicMock()
    entry = _legacy_entry()
    entry.runtime_data = MagicMock()

    unique_ids: set[str] = set()
    for platform in PLATFORMS:
        module = __import__(
            f"custom_components.localshift.{platform}",
            fromlist=["async_setup_entry"],
        )
        add_entities = MagicMock()
        await module.async_setup_entry(hass, entry, add_entities)
        add_entities.assert_called_once()
        unique_ids.update(e._attr_unique_id for e in add_entities.call_args[0][0])

    assert unique_ids, "no entities were created"
    for key in RETIRED_OPTIONS:
        assert f"localshift_{key}" not in unique_ids
    # No migration: setup leaves the stored entry exactly as it found it.
    assert entry.options == _legacy_options()
    assert entry.data == {RETIRED_ENTITY_KEY: "sensor.my_home_solar_energy"}


def test_coordinator_builds_planner_options_with_retired_keys_stored():
    """Coordinator and engine construct; the planner options ignore the keys."""
    hass = MagicMock()
    hass.states = {}
    hass.data = {}
    entry = _legacy_entry()

    coordinator = LocalShiftCoordinator(hass, entry)
    # async_start builds the engine with exactly these arguments.
    engine = ComputationEngine(
        hass, entry, coordinator.get_entity_id, coordinator.get_switch_state
    )
    config_options = engine._build_optimizer_config_options()

    assert config_options[CONF_BATTERY_TARGET] == 90
    for key in RETIRED_OPTIONS:
        assert key not in config_options
        # Still readable as a plain unknown key; nothing strips it.
        assert coordinator.get_option(key) == RETIRED_OPTIONS[key]
    assert entry.options == _legacy_options()


@pytest.mark.asyncio
async def test_options_flow_save_keeps_retired_keys_untouched():
    """Saving the options flow neither shows nor strips the retired keys."""
    hass = MagicMock()
    entry = _legacy_entry()
    flow = LocalShiftConfigFlow.async_get_options_flow(entry)
    flow.handler = entry.entry_id
    hass.config_entries.async_get_known_entry = MagicMock(return_value=entry)
    flow.hass = hass
    type(flow)._config_entry_id = property(lambda self: entry.entry_id)

    form = await flow.async_step_advanced(None)
    shown = {key.schema for key in form["data_schema"].schema}
    assert shown.isdisjoint(RETIRED_OPTIONS)

    result = await flow.async_step_advanced({CONF_BATTERY_TARGET: 87})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {**_legacy_options(), CONF_BATTERY_TARGET: 87}


@pytest.mark.asyncio
async def test_new_entry_is_not_given_retired_keys():
    """Setup no longer writes the two keys it used to seed."""
    hass = MagicMock()
    hass.services.async_services.return_value = {
        "notify": {"mobile_app_test": MagicMock()}
    }

    def _state(entity_id):
        state = MagicMock()
        state.entity_id = entity_id
        state.state = "10"
        state.domain = entity_id.split(".")[0]
        return state

    hass.states.get = _state

    flow = LocalShiftConfigFlow()
    flow.hass = hass
    flow._teslemetry_data = {
        CONF_TESLEMETRY_OPERATION_MODE: "select.tesla_powerwall_operation_mode",
    }
    flow._pricing_source_data = {
        CONF_PRICING_DATA_SOURCE: "amber",
        CONF_COMPARISON_MODE: "disabled",
    }
    flow._pricing_data = {CONF_PRICING_GENERAL_PRICE: "sensor.amber_general_price"}

    result = await flow.async_step_solcast({
        CONF_SOLCAST_FORECAST_TODAY: "sensor.solcast_today",
        CONF_SOLCAST_FORECAST_TOMORROW: "sensor.solcast_tomorrow",
        CONF_NOTIFY_SERVICE: "notify.mobile_app_test",
    })

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert CONF_BATTERY_TARGET in result["options"]
    for key in RETIRED_OPTIONS:
        assert key not in result["options"]
