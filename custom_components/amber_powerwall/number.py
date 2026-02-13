"""Number platform for the Amber Powerwall integration.

Provides user-configurable thresholds as NumberEntity instances.
These replace the YAML input_number entities and are backed by
config entry options for persistence.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_BATTERY_TARGET,
    CONF_CHEAP_PRICE_DEADBAND,
    CONF_CHEAP_PRICE_PERCENTILE,
    CONF_FORECAST_LOOKAHEAD_HOURS,
    CONF_HOLD_ABSOLUTE_CHEAP_THRESHOLD,
    CONF_HOLD_MIN_SAVINGS_PERCENT,
    CONF_MAX_PRECHARGE_PRICE,
    CONF_PRECHARGE_BATTERY_THRESHOLD,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_DEADBAND,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_HOLD_ABSOLUTE_CHEAP_THRESHOLD,
    DEFAULT_HOLD_MIN_SAVINGS_PERCENT,
    DEFAULT_MAX_PRECHARGE_PRICE,
    DEFAULT_PRECHARGE_BATTERY_THRESHOLD,
    DOMAIN,
    THRESHOLD_RANGES,
)
from .coordinator import AmberPowerwallCoordinator

# Map of (config_key, name, default) for each number entity
NUMBER_DEFINITIONS: list[tuple[str, str, float]] = [
    (
        CONF_CHEAP_PRICE_PERCENTILE,
        "Cheap Price Percentile",
        DEFAULT_CHEAP_PRICE_PERCENTILE,
    ),
    (CONF_MAX_PRECHARGE_PRICE, "Max Pre-charge Price", DEFAULT_MAX_PRECHARGE_PRICE),
    (CONF_CHEAP_PRICE_DEADBAND, "Price Deadband", DEFAULT_CHEAP_PRICE_DEADBAND),
    (
        CONF_FORECAST_LOOKAHEAD_HOURS,
        "Forecast Lookahead",
        DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    ),
    (
        CONF_PRECHARGE_BATTERY_THRESHOLD,
        "Pre-charge Battery Threshold",
        DEFAULT_PRECHARGE_BATTERY_THRESHOLD,
    ),
    (CONF_BATTERY_TARGET, "Battery Target", DEFAULT_BATTERY_TARGET),
    (
        CONF_HOLD_MIN_SAVINGS_PERCENT,
        "Hold Min Savings Percent",
        DEFAULT_HOLD_MIN_SAVINGS_PERCENT,
    ),
    (
        CONF_HOLD_ABSOLUTE_CHEAP_THRESHOLD,
        "Hold Absolute Cheap Threshold",
        DEFAULT_HOLD_ABSOLUTE_CHEAP_THRESHOLD,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Amber Powerwall number entities."""
    coordinator: AmberPowerwallCoordinator = entry.runtime_data

    entities = [
        AmberPowerwallNumber(coordinator, entry, conf_key, name, default)
        for conf_key, name, default in NUMBER_DEFINITIONS
    ]

    async_add_entities(entities)


class AmberPowerwallNumber(NumberEntity):
    """A user-configurable threshold backed by config entry options."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AmberPowerwallCoordinator,
        entry: ConfigEntry,
        conf_key: str,
        name: str,
        default: float,
    ) -> None:
        """Initialise the number entity."""
        self.coordinator = coordinator
        self._entry = entry
        self._conf_key = conf_key
        self._default = default

        spec = THRESHOLD_RANGES[conf_key]
        self._attr_unique_id = f"amber_powerwall_{conf_key}"
        self._attr_name = name
        self._attr_icon = spec["icon"]
        self._attr_native_min_value = spec["min"]
        self._attr_native_max_value = spec["max"]
        self._attr_native_step = spec["step"]
        self._attr_native_unit_of_measurement = spec["unit"]
        self._attr_mode = NumberMode.SLIDER

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information to link all entities under one device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Amber Powerwall",
            manufacturer="Custom",
            model="Solar Battery Automation",
            sw_version="0.1.0",
        )

    @property
    def native_value(self) -> float:
        """Return the current value from options."""
        return self._entry.options.get(self._conf_key, self._default)

    async def async_set_native_value(self, value: float) -> None:
        """Update the value in config entry options and trigger re-evaluation."""
        new_options = {**self._entry.options, self._conf_key: value}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        self.async_write_ha_state()

        # Trigger immediate re-evaluation with new threshold values
        # This fixes the issue where threshold changes only took effect on next periodic tick (up to 1 min delay)
        self.coordinator._compute_derived_values()
        self.coordinator._notify_listeners()
        await self.coordinator.async_evaluate_state_machine()
