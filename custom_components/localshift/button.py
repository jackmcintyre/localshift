"""Button platform for the LocalShift integration.

Provides utility buttons:
- Update Forecast
- Reset Learning Data
- Learn HVAC Power

Issue #382: Mode control buttons removed - replaced by select entity.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BUTTON_ICONS,
    BUTTON_LEARN_HVAC_POWER,
    BUTTON_NAMES,
    BUTTON_RESET_LEARNING,
    BUTTON_UPDATE_FORECAST,
    DOMAIN,
)
from .coordinator import LocalShiftCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LocalShift button entities."""
    coordinator: LocalShiftCoordinator = entry.runtime_data

    entities = [
        UpdateForecastButton(coordinator, entry),
        ResetLearningDataButton(coordinator, entry),
        LearnHVACPowerButton(coordinator, entry),
    ]

    _LOGGER.info("Setting up %d LocalShift button entities", len(entities))
    for entity in entities:
        _LOGGER.debug("Registering button entity: %s", entity.unique_id)

    async_add_entities(entities)


class LocalShiftButtonBase(ButtonEntity):
    """Base class for utility buttons."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialise the button."""
        self.coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"localshift_{key}"
        self._attr_name = BUTTON_NAMES[key]
        self._attr_icon = BUTTON_ICONS[key]

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


class UpdateForecastButton(LocalShiftButtonBase):
    """Force forecast update and clear historical load cache."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, BUTTON_UPDATE_FORECAST)

    async def async_press(self) -> None:
        """Handle button press."""
        # Clear historical cache to force refresh
        await self.coordinator.async_clear_historical_cache()

        # Trigger coordinator refresh to regenerate forecast
        await self.coordinator.async_evaluate_state_machine()

        if self.coordinator._notification_service is not None:
            await (
                self.coordinator._notification_service.send_manual_action_notification(
                    "Forecast Update", self.coordinator.data
                )
            )


class ResetLearningDataButton(LocalShiftButtonBase):
    """Reset all learning system data and start fresh.

    Clears decision tracker data and resets learning status to 'observing'.
    Used when the user wants to start the learning process over.
    """

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, BUTTON_RESET_LEARNING)

    async def async_press(self) -> None:
        """Handle button press - reset all learning data."""
        _LOGGER.info("Resetting learning system data")

        # Clear decision tracker data
        tracker = getattr(self.coordinator, "decision_tracker", None)
        if tracker is not None:
            tracker._pending_decisions.clear()
            tracker._completed_decisions.clear()
            await tracker.async_save()

        # Reset learning status
        self.coordinator.data.learning_status = "observing"

        # Clear recent decision log
        self.coordinator.data.recent_decision_log.clear()

        # Reset performance metrics to defaults
        from .coordinator_data import PerformanceMetrics

        self.coordinator.data.performance_metrics = PerformanceMetrics()

        _LOGGER.info("Learning system data reset complete")

        if self.coordinator._notification_service is not None:
            await (
                self.coordinator._notification_service.send_manual_action_notification(
                    "Reset Learning Data", self.coordinator.data
                )
            )


class LearnHVACPowerButton(LocalShiftButtonBase):
    """Proactively learn HVAC power consumption.

    Runs each controlled climate entity briefly to measure its power
    consumption. This provides immediate learning instead of waiting
    for natural HVAC operation cycles.

    The learning process:
    1. Measures baseline load
    2. Turns on each climate entity in cool/heat mode
    3. Waits for power to stabilize
    4. Records the power delta
    5. Restores original state
    """

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, BUTTON_LEARN_HVAC_POWER)

    async def async_press(self) -> None:
        """Handle button press - run HVAC power learning."""
        _LOGGER.info("Starting HVAC power learning")

        # Get thermal manager
        thermal_manager = getattr(self.coordinator, "_thermal_manager", None)
        if thermal_manager is None:
            _LOGGER.warning("Thermal manager not available for HVAC learning")
            return

        # Check if thermal management is enabled
        if not thermal_manager.is_enabled():
            _LOGGER.warning("Thermal management is disabled - cannot learn HVAC power")
            return

        # Get control entities and load entity
        control_entities = self.coordinator.data.climate_control_entities
        load_entity_id = self.coordinator._get_entity_id("teslemetry_load_power")

        if not control_entities:
            _LOGGER.warning("No controlled climate entities configured for learning")
            return

        if not load_entity_id:
            _LOGGER.warning("No load power entity configured for learning")
            return

        # Run the learning process
        result = await thermal_manager.async_learn_hvac_power_now(
            control_entities=control_entities,
            load_entity_id=load_entity_id,
        )

        if result.get("success"):
            _LOGGER.info(
                "HVAC power learning complete: %d/%d entities learned",
                result.get("entities_learned", 0),
                result.get("entities_total", 0),
            )
        else:
            _LOGGER.warning(
                "HVAC power learning failed: %s",
                result.get("error", "Unknown error"),
            )

        if self.coordinator._notification_service is not None:
            await (
                self.coordinator._notification_service.send_manual_action_notification(
                    "Learn HVAC Power", self.coordinator.data
                )
            )
