"""Select platform for the LocalShift integration.

Provides select entities for:
- Battery mode manual control
- Optimizer objective mode (self-consumption vs arbitrage)
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_OPTIMIZATION_MODE,
    DEFAULT_OPTIMIZATION_MODE,
    DOMAIN,
    SELECT_BATTERY_MODE,
    SELECT_ICONS,
    SELECT_NAMES,
    SELECT_OPTIMIZATION_MODE,
    SELECT_OPTIONS,
    SWITCH_AUTOMATION_ENABLED,
    BatteryMode,
)
from .coordinator import LocalShiftCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LocalShift select entities."""
    coordinator: LocalShiftCoordinator = entry.runtime_data

    entities = [
        BatteryModeSelect(coordinator, entry),
        OptimizationModeSelect(coordinator, entry),
    ]

    _LOGGER.info("Setting up %d LocalShift select entities", len(entities))
    async_add_entities(entities)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    """Build device info for LocalShift entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="LocalShift",
        manufacturer="Custom",
        model="Solar Battery Automation",
        sw_version="0.0.2",
    )


class BatteryModeSelect(SelectEntity):
    """Select entity for battery mode control."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize battery mode select."""
        self.coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"localshift_{SELECT_BATTERY_MODE}"
        self._attr_name = SELECT_NAMES[SELECT_BATTERY_MODE]
        self._attr_icon = SELECT_ICONS[SELECT_BATTERY_MODE]
        self._attr_options = SELECT_OPTIONS[SELECT_BATTERY_MODE]
        self._manual_mode: str = entry.options.get(
            "manual_battery_mode", "self_consumption"
        )
        self._internal_update: bool = False
        self._last_committed_mode: str | None = None
        self._update_count: int = 0
        self._change_count: int = 0

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return _device_info(self._entry)

    @property
    def current_option(self) -> str | None:
        """Return the current selected option.

        When automation is ON, shows the actual mode from the optimizer
        (SELF_CONSUMPTION, GRID_CHARGING, DEMAND_BLOCK, HOLD, etc.).
        When automation is OFF, shows the user's manual mode selection.
        """
        if self.coordinator.get_switch_state(SWITCH_AUTOMATION_ENABLED):
            mode = self.coordinator.data.active_mode
            if mode is not None:
                return mode.value
            return "self_consumption"
        return self._manual_mode

    def _is_non_user_reassertion(self, option: str) -> bool:
        """Classify a write as internal restoration rather than a user pick.

        Provenance + idempotence (Issue #934): HA stamps ``self._context``
        from the service call before dispatch, and a frontend dropdown pick
        always carries a ``user_id``; a restore, automation, or internal
        re-assertion does not. Combined with "the requested option matches
        the PERSISTED manual mode" (``entry.options['manual_battery_mode']``
        — what an options-reload / entity-recreation restore write re-asserts,
        not ``current_option``, which reflects the optimizer's live mode
        whenever automation is ON and so is frequently a different value from
        the one actually being restored) and "automation is currently ON"
        (the entity is not already parked in manual), this identifies exactly
        the options-reload / entity-recreation echo without touching a real
        re-pick of the displayed mode, which the automation-ON precondition
        still permits when it carries a user context.

        Comparing against ``current_option`` instead of the persisted value
        was tried and found unsound: it only caught the echo by coincidence,
        when the optimizer's live mode happened to equal the persisted one.
        Any other live mode (grid_charging, demand_block, hold, ...) let the
        echo straight through into manual override.
        """
        persisted_mode = self._entry.options.get(
            "manual_battery_mode", self._manual_mode
        )
        if option != persisted_mode:
            return False
        if not self.coordinator.get_switch_state(SWITCH_AUTOMATION_ENABLED):
            return False
        context = getattr(self, "_context", None)
        user_id = getattr(context, "user_id", None) if context is not None else None
        return user_id is None

    async def async_select_option(self, option: str) -> None:
        """Handle selection of a new battery mode."""
        _LOGGER.info("Battery mode select changed to: %s", option)
        if option not in self._attr_options:
            _LOGGER.error("Invalid battery mode selected: %s", option)
            return

        if option != "automatic" and self._is_non_user_reassertion(option):
            _LOGGER.warning(
                "Ignoring non-user battery mode re-assertion of %s (context=%s) "
                "— not entering manual override",
                option,
                getattr(self, "_context", None),
            )
            return

        old_mode = self.current_option

        if option == "automatic":
            self.coordinator.set_switch_state(SWITCH_AUTOMATION_ENABLED, True)
            new_options = {
                **self._entry.options,
                "switch_state_automation_enabled": True,
            }
            self.coordinator.set_manual_override(
                False, reason="user_selected_automatic"
            )
            self.hass.config_entries.async_update_entry(
                self._entry, options=new_options
            )
            await self.coordinator.async_recompute_and_evaluate()
            self.async_write_ha_state()
            return

        # Manual mode
        self.coordinator.set_switch_state(SWITCH_AUTOMATION_ENABLED, False)
        new_options = {
            **self._entry.options,
            "switch_state_automation_enabled": False,
            "manual_battery_mode": option,
        }
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        self.coordinator.set_manual_override(True, reason="user_selection")

        try:
            target_mode = BatteryMode(option)
        except ValueError:
            _LOGGER.error("Invalid battery mode selected: %s", option)
            return

        success = await self.coordinator.async_set_battery_mode(target_mode)
        if not success:
            _LOGGER.warning(
                "Failed to apply battery mode %s, reverting select to %s",
                option,
                old_mode,
            )
            self.async_write_ha_state()
            return

        self._manual_mode = option
        if self.coordinator._notification_service is not None:
            await (
                self.coordinator._notification_service.send_manual_action_notification(
                    f"Manual {target_mode.display_name}", self.coordinator.data
                )
            )
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )
        # Sync manual_override flag with automation switch state. Issue #934:
        # a restart with automation already off (a genuine manual pick
        # persisted across restart) re-enters manual STAMPED, so the 4-hour
        # timeout applies to it too — it is not exempt just because no select
        # write happened this session.
        if not self.coordinator.get_switch_state(SWITCH_AUTOMATION_ENABLED):
            self.coordinator.set_manual_override(True, reason="startup_automation_off")
        else:
            self.coordinator.set_manual_override(False, reason="startup_automation_on")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        if self._internal_update:
            return

        self._update_count += 1
        current_mode = self.current_option

        if current_mode != self._last_committed_mode:
            self._change_count += 1
            _LOGGER.debug(
                "Battery mode select changed: %s → %s (update #%d, change #%d)",
                self._last_committed_mode,
                current_mode,
                self._update_count,
                self._change_count,
            )
            self._last_committed_mode = current_mode
            self.async_write_ha_state()
        else:
            if self._update_count % 60 == 0:
                _LOGGER.debug(
                    "Battery mode select: %d updates, %d changes (mode stable at %s)",
                    self._update_count,
                    self._change_count,
                    current_mode,
                )


class OptimizationModeSelect(SelectEntity):
    """Select entity for optimizer objective mode."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize optimization mode select."""
        self.coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"localshift_{SELECT_OPTIMIZATION_MODE}"
        self._attr_name = SELECT_NAMES[SELECT_OPTIMIZATION_MODE]
        self._attr_icon = SELECT_ICONS[SELECT_OPTIMIZATION_MODE]
        self._attr_options = SELECT_OPTIONS[SELECT_OPTIMIZATION_MODE]

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return _device_info(self._entry)

    @property
    def current_option(self) -> str | None:
        """Return the current optimization mode option."""
        option = self._entry.options.get(CONF_OPTIMIZATION_MODE)
        if option in self._attr_options:
            return option
        return DEFAULT_OPTIMIZATION_MODE

    async def async_select_option(self, option: str) -> None:
        """Persist selected optimization mode and trigger recompute."""
        if option not in self._attr_options:
            _LOGGER.error("Invalid optimization mode selected: %s", option)
            return

        _LOGGER.info("Optimization mode select changed to: %s", option)
        new_options = {**self._entry.options, CONF_OPTIMIZATION_MODE: option}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)

        self.async_write_ha_state()
        # Persisting the option above fires the entry update listener
        # (_async_options_updated in __init__.py), which is the single
        # recompute trigger for entity writes (issue #975).

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        self.async_write_ha_state()
