"""Button platform for the LocalShift integration.

Provides utility buttons:
- Update Forecast
- Reset Learning Data

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
