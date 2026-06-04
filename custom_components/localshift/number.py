"""Number platform for the LocalShift integration.

Provides user-configurable thresholds as NumberEntity instances.
These replace the YAML input_number entities and are backed by
config entry options for persistence.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_BATTERY_TARGET,
    CONF_CHEAP_PRICE_PERCENTILE,
    CONF_MAX_PRECHARGE_PRICE,
    CONF_MIN_CYCLE_SAVING,
    CONF_MINIMUM_TARGET_SOC,
    CONF_STALE_SOLAR_CONFIDENCE_CEILING,
    CONF_SWITCHING_PENALTY,
    CONF_TARGET_PENALTY,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_MAX_PRECHARGE_PRICE,
    DEFAULT_MIN_CYCLE_SAVING,
    DEFAULT_MINIMUM_TARGET_SOC,
    DEFAULT_STALE_SOLAR_CONFIDENCE_CEILING,
    DEFAULT_SWITCHING_PENALTY,
    DEFAULT_TARGET_PENALTY,
    DOMAIN,
    THRESHOLD_RANGES,
)
from .coordinator import LocalShiftCoordinator

# Map of (config_key, name, default) for each number entity
# Simplified per Issue #214 - removed rarely-tuned parameters with sensible defaults
NUMBER_DEFINITIONS: list[tuple[str, str, float]] = [
    (
        CONF_CHEAP_PRICE_PERCENTILE,
        "Cheap Price Percentile",
        DEFAULT_CHEAP_PRICE_PERCENTILE,
    ),
    (CONF_MAX_PRECHARGE_PRICE, "Max Pre-charge Price", DEFAULT_MAX_PRECHARGE_PRICE),
    (
        CONF_MIN_CYCLE_SAVING,
        "Min Cycle Saving",
        DEFAULT_MIN_CYCLE_SAVING,
    ),
    (CONF_BATTERY_TARGET, "Battery Target", DEFAULT_BATTERY_TARGET),
    (
        CONF_MINIMUM_TARGET_SOC,
        "Minimum Target SOC",
        DEFAULT_MINIMUM_TARGET_SOC,
    ),
    (CONF_TARGET_PENALTY, "Target Shortfall Penalty", DEFAULT_TARGET_PENALTY),
    (
        CONF_STALE_SOLAR_CONFIDENCE_CEILING,
        "Stale Solar Confidence Ceiling",
        DEFAULT_STALE_SOLAR_CONFIDENCE_CEILING,
    ),
    (CONF_SWITCHING_PENALTY, "Switching Penalty", DEFAULT_SWITCHING_PENALTY),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LocalShift number entities."""
    coordinator: LocalShiftCoordinator = entry.runtime_data

    entities = [
        LocalShiftNumber(coordinator, entry, conf_key, name, default)
        for conf_key, name, default in NUMBER_DEFINITIONS
    ]

    async_add_entities(entities)


class LocalShiftNumber(NumberEntity):
    """A user-configurable threshold backed by config entry options."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
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
        self._attr_unique_id = f"localshift_{conf_key}"
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
            name="LocalShift",
            manufacturer="Custom",
            model="Solar Battery Automation",
            sw_version="0.0.2",
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
        await self.coordinator.async_recompute_and_evaluate()
