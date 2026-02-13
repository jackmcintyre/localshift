"""The Amber Powerwall integration.

Automated Tesla Powerwall battery control based on Amber Electric spot pricing,
Solcast solar forecasts, and configurable thresholds.
"""

from __future__ import annotations

import logging
from typing import TypeAlias

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .coordinator import AmberPowerwallCoordinator

_LOGGER = logging.getLogger(__name__)

AmberPowerwallConfigEntry: TypeAlias = ConfigEntry[AmberPowerwallCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: AmberPowerwallConfigEntry
) -> bool:
    """Set up Amber Powerwall from a config entry."""
    coordinator = AmberPowerwallCoordinator(hass, entry)
    entry.runtime_data = coordinator

    # Start listening to external entities
    await coordinator.async_start()

    # Forward setup to all platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    _LOGGER.info("Amber Powerwall integration set up successfully")
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AmberPowerwallConfigEntry
) -> bool:
    """Unload an Amber Powerwall config entry."""
    # Stop the coordinator
    coordinator: AmberPowerwallCoordinator = entry.runtime_data
    await coordinator.async_stop()

    # Unload platforms
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(
    hass: HomeAssistant, entry: AmberPowerwallConfigEntry
) -> None:
    """Handle options update — trigger a re-evaluation with new thresholds."""
    coordinator: AmberPowerwallCoordinator = entry.runtime_data
    coordinator._compute_derived_values()
    coordinator._notify_listeners()
    await coordinator.async_evaluate_state_machine()
    _LOGGER.info("Amber Powerwall options updated, re-evaluating")
