"""Away (holiday mode) helpers for the LocalShift integration.

The away entity is an optional on/off entity (any domain) that is on while the
house is empty. It is stored only in ``entry.options`` under
``CONF_AWAY_ENTITY``; unset means today's behaviour, exactly.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant

from ..const import CONF_AWAY_ENTITY


def get_away_entity_id(entry: ConfigEntry) -> str | None:
    """Return the configured away entity id, or None when unset.

    Options are the single source: a value left over in ``entry.data`` is
    ignored, so clearing the option really clears the behaviour.

    Args:
        entry: LocalShift config entry

    Returns:
        The away entity id, or None if no away entity is configured.

    """
    return entry.options.get(CONF_AWAY_ENTITY) or None


def is_away_active(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Report whether the house is currently away.

    Unset, missing, unavailable or unknown all report "not away", which keeps
    today's behaviour on any failure.

    Args:
        hass: Home Assistant instance
        entry: LocalShift config entry

    Returns:
        True only when an away entity is configured and its state is "on".

    """
    entity_id = get_away_entity_id(entry)
    if not entity_id:
        return False

    state = hass.states.get(entity_id)
    return state is not None and state.state == STATE_ON
