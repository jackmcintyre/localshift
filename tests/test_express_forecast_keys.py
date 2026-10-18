"""Amber Express: the two forecast mappings are not health-checked (issue #1210).

Under the Amber Express pricing source the forecast comes from the price
entity's ``detailedForecast`` attribute (``pricing/provider.py``), so the
``pricing_general_forecast`` / ``pricing_feed_in_forecast`` mappings are never
read. They used to be validated as RECOMMENDED anyway, so once the entities
they point at were removed the integration status sat at "degraded" for good.

These tests pin:
- under Express the validator does not track the two keys, and an otherwise
  healthy install reports "ok" with them missing;
- under the base "amber" source (and with no source given) nothing changes;
- the options flow writes "" for the two keys under Express and keeps them
  under "amber";
- the optimizer summary reports the pricing source from ``entry.data``, where
  the config flow stores it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util

from custom_components.localshift.computation_engine import ComputationEngine
from custom_components.localshift.config_flow import LocalShiftConfigFlow
from custom_components.localshift.const import (
    CONF_PRICING_DATA_SOURCE,
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_TESLEMETRY_BATTERY_POWER,
    CONF_TESLEMETRY_GRID_POWER,
    CONF_TESLEMETRY_LOAD_POWER,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    CONF_TESLEMETRY_SOLAR_POWER,
    PRICING_SOURCE_AMBER,
    PRICING_SOURCE_AMBER_EXPRESS,
)
from custom_components.localshift.coordinator import LocalShiftCoordinator
from custom_components.localshift.utils.entity_configs import ENTITY_CONFIG
from custom_components.localshift.utils.validation import (
    EntityStatus,
    EntityValidator,
    IntegrationStatus,
)
from tests.fixtures.ha_entities import MockState, MockStates

FORECAST_KEYS = (CONF_PRICING_GENERAL_FORECAST, CONF_PRICING_FEED_IN_FORECAST)


def _entity_id(config_key: str) -> str:
    return f"sensor.test_{config_key}"


def _healthy_state(config_key: str, config: dict[str, Any]) -> MockState:
    """A state the validator accepts for this config entry, updated just now."""
    expected = config.get("expected_type")
    attributes: dict[str, Any] = {}
    if config.get("valid_values"):
        value = config["valid_values"][0]
    elif expected is bool:
        value = "off"
    elif expected == list:
        value = "ok"
        attributes = {"forecasts": [], "detailedForecast": []}
    elif expected in ((int, float), int, float):
        value = "50"
    else:
        value = "ok"
    now = dt_util.now()
    return MockState(
        entity_id=_entity_id(config_key),
        state=value,
        attributes=attributes,
        last_changed=now,
        last_updated=now,
    )


def _validator_with_forecast_entities_removed(
    also_missing: tuple[str, ...] = (), **kwargs: Any
) -> EntityValidator:
    """Every mapped entity healthy except the two forecast ones, which are gone.

    That is a live install after the core Amber integration is removed.
    """
    missing = (*FORECAST_KEYS, *also_missing)
    states = MockStates({
        _entity_id(key): _healthy_state(key, config)
        for key, config in ENTITY_CONFIG.items()
        if key not in missing
    })
    hass = MagicMock()
    hass.states.get = states.get
    hass.states.async_all = states.async_all
    return EntityValidator(hass, _entity_id, **kwargs)


# =============================================================================
# Validator
# =============================================================================


class TestValidatorUnderAmberExpress:
    def test_status_stays_ok_with_forecast_entities_removed(self) -> None:
        validator = _validator_with_forecast_entities_removed(
            pricing_source=PRICING_SOURCE_AMBER_EXPRESS
        )

        validator.check_all_entities()

        assert validator.status == IntegrationStatus.OK
        assert validator.warnings == []
        assert validator.errors == []

    def test_forecast_keys_are_not_tracked(self) -> None:
        validator = _validator_with_forecast_entities_removed(
            pricing_source=PRICING_SOURCE_AMBER_EXPRESS
        )

        results = validator.check_all_entities()
        summary = validator.get_health_summary()

        for key in FORECAST_KEYS:
            assert key not in results
            assert key not in summary["entities"]

    def test_price_entities_are_still_validated(self) -> None:
        """Only the two unused keys drop out: a missing price still errors."""
        validator = _validator_with_forecast_entities_removed(
            also_missing=(CONF_PRICING_GENERAL_PRICE,),
            pricing_source=PRICING_SOURCE_AMBER_EXPRESS,
        )

        results = validator.check_all_entities()

        assert results[CONF_PRICING_GENERAL_PRICE].status == EntityStatus.MISSING
        assert validator.status == IntegrationStatus.ERROR


class TestValidatorUnderAmberUnchanged:
    @pytest.mark.parametrize(
        "kwargs",
        [{"pricing_source": PRICING_SOURCE_AMBER}, {}],
        ids=["amber", "no-source"],
    )
    def test_removed_forecast_entities_still_degrade(self, kwargs) -> None:
        validator = _validator_with_forecast_entities_removed(**kwargs)

        results = validator.check_all_entities()

        for key in FORECAST_KEYS:
            assert results[key].status == EntityStatus.MISSING
        assert validator.status == IntegrationStatus.DEGRADED
        assert len(validator.warnings) == 2


# =============================================================================
# Options flow
# =============================================================================


def _entity_mappings_flow(entry: MagicMock) -> Any:
    hass = MagicMock()
    hass.services.async_services.return_value = {
        "notify": {"mobile_app_test": MagicMock()}
    }
    flow = LocalShiftConfigFlow.async_get_options_flow(entry)
    flow.handler = entry.entry_id
    hass.config_entries.async_get_known_entry = MagicMock(return_value=entry)
    hass.config_entries.async_update_entry = MagicMock()
    flow.hass = hass
    type(flow)._config_entry_id = property(lambda self: entry.entry_id)
    return flow


def _stored_mappings(pricing_source: str) -> dict[str, str]:
    return {
        CONF_PRICING_DATA_SOURCE: pricing_source,
        CONF_TESLEMETRY_OPERATION_MODE: "select.test_operation_mode",
        CONF_TESLEMETRY_SOC: "sensor.test_soc",
        CONF_TESLEMETRY_GRID_POWER: "sensor.test_grid_power",
        CONF_TESLEMETRY_BATTERY_POWER: "sensor.test_battery_power",
        CONF_TESLEMETRY_SOLAR_POWER: "sensor.test_solar_power",
        CONF_TESLEMETRY_LOAD_POWER: "sensor.test_load_power",
        CONF_PRICING_GENERAL_PRICE: "sensor.test_general_price",
        CONF_PRICING_FEED_IN_PRICE: "sensor.test_feed_in_price",
        CONF_PRICING_GENERAL_FORECAST: "sensor.test_general_forecast",
        CONF_PRICING_FEED_IN_FORECAST: "sensor.test_feed_in_forecast",
        CONF_PRICING_PRICE_SPIKE: "binary_sensor.test_price_spike",
        CONF_SOLCAST_FORECAST_TODAY: "sensor.test_solcast_today",
        CONF_SOLCAST_FORECAST_TOMORROW: "sensor.test_solcast_tomorrow",
    }


def _entry(data: dict[str, str]) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = data
    entry.options = {}
    return entry


class TestOptionsFlowEntityMappings:
    @pytest.mark.asyncio
    async def test_express_save_writes_empty_forecast_keys(self) -> None:
        """The Express form hides the two fields, so the save clears them."""
        entry = _entry(_stored_mappings(PRICING_SOURCE_AMBER_EXPRESS))
        flow = _entity_mappings_flow(entry)
        submitted = {
            k: v
            for k, v in entry.data.items()
            if k not in (*FORECAST_KEYS, CONF_PRICING_DATA_SOURCE)
        }

        result = await flow.async_step_entity_mappings(submitted)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "settings"
        saved = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
        for key in FORECAST_KEYS:
            assert saved[key] == ""
        assert saved[CONF_PRICING_GENERAL_PRICE] == "sensor.test_general_price"

    @pytest.mark.asyncio
    async def test_amber_save_keeps_forecast_keys(self) -> None:
        entry = _entry(_stored_mappings(PRICING_SOURCE_AMBER))
        flow = _entity_mappings_flow(entry)
        submitted = {
            k: v for k, v in entry.data.items() if k != CONF_PRICING_DATA_SOURCE
        }

        await flow.async_step_entity_mappings(submitted)

        saved = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
        assert saved[CONF_PRICING_GENERAL_FORECAST] == "sensor.test_general_forecast"
        assert saved[CONF_PRICING_FEED_IN_FORECAST] == "sensor.test_feed_in_forecast"


# =============================================================================
# Optimizer summary
# =============================================================================


@pytest.mark.parametrize(
    "pricing_source", [PRICING_SOURCE_AMBER_EXPRESS, PRICING_SOURCE_AMBER]
)
def test_optimizer_config_reports_pricing_source_from_entry_data(
    pricing_source: str,
) -> None:
    """The config flow stores the source in entry.data, never entry.options."""
    hass = MagicMock()
    hass.states = {}
    hass.data = {}
    entry = _entry({CONF_PRICING_DATA_SOURCE: pricing_source})
    coordinator = LocalShiftCoordinator(hass, entry)
    engine = ComputationEngine(
        hass, entry, coordinator.get_entity_id, coordinator.get_switch_state
    )

    config_options = engine._build_optimizer_config_options()

    assert config_options["pricing_source"] == pricing_source
