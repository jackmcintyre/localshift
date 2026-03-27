"""The LocalShift integration.

Automated Tesla Powerwall battery control based on pricing data,
solar forecasts, and demand window timing.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .coordinator import LocalShiftCoordinator

_LOGGER = logging.getLogger(__name__)

type LocalShiftConfigEntry = ConfigEntry[LocalShiftCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: LocalShiftConfigEntry) -> bool:
    """Set up LocalShift from a config entry."""
    coordinator = LocalShiftCoordinator(hass, entry)
    entry.runtime_data = coordinator

    # Start listening to external entities
    await coordinator.async_start()

    # Forward setup to all platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    _LOGGER.info("LocalShift integration set up successfully")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LocalShiftConfigEntry) -> bool:
    """Unload a LocalShift config entry."""
    # Stop the coordinator
    coordinator: LocalShiftCoordinator = entry.runtime_data
    await coordinator.async_stop()

    # Unload platforms
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(
    hass: HomeAssistant, entry: LocalShiftConfigEntry
) -> None:
    """Handle options update — trigger a re-evaluation with new thresholds."""
    coordinator: LocalShiftCoordinator = entry.runtime_data
    # Reschedule daily summary timer in case demand_window_end changed
    coordinator.reschedule_daily_summary_timer()
    # Reset entity tracking for any changed entities (e.g., weather_entity)
    # This clears broken status and allows recovery without restart
    coordinator.reset_entity_tracking_on_options_change()
    await coordinator.async_invalidate_charge_rate_curves()
    await coordinator.async_recompute_and_evaluate()
    _LOGGER.info("LocalShift options updated, re-evaluating")
