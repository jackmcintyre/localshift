"""Entity validation functions for config flow.

This module contains functions to validate entities, check domains,
and validate notify services.
"""

from __future__ import annotations


async def validate_entity_exists(hass, entity_id: str) -> str | None:
    """Validate that an entity exists in Home Assistant.

    Args:
        hass: Home Assistant instance
        entity_id: Entity ID to validate

    Returns:
        None if valid, or error message string
    """
    state = hass.states.get(entity_id)
    if state is None:
        return f"Entity '{entity_id}' does not exist"
    return None


async def validate_entity_available(hass, entity_id: str) -> str | None:
    """Validate that an entity is available (not unavailable/unknown).

    Args:
        hass: Home Assistant instance
        entity_id: Entity ID to validate

    Returns:
        None if available, or error message string
    """
    state = hass.states.get(entity_id)
    if state is None:
        return f"Entity '{entity_id}' does not exist"
    if state.state in ("unavailable", "unknown"):
        return f"Entity '{entity_id}' is {state.state}"
    return None


async def validate_entity_domain(
    hass, entity_id: str, expected_domain: str
) -> str | None:
    """Validate that an entity has the expected domain.

    Args:
        hass: Home Assistant instance
        entity_id: Entity ID to validate
        expected_domain: Expected domain (e.g., "sensor", "select")

    Returns:
        None if valid, or error message string
    """
    state = hass.states.get(entity_id)
    if state is None:
        return f"Entity '{entity_id}' does not exist"
    if state.domain != expected_domain:
        return f"Expected {expected_domain} entity, got {state.domain}"
    return None


async def validate_all_entities(
    hass, entities: dict[str, tuple[str, str]]
) -> dict[str, str] | None:
    """Validate multiple entities exist, are available, and have correct domains.

    Args:
        hass: Home Assistant instance
        entities: Dictionary of {config_key: (entity_id, expected_domain)}

    Returns:
        None if all valid, or dict of {config_key: error_message}
    """
    errors = {}
    for config_key, (entity_id, expected_domain) in entities.items():
        state = hass.states.get(entity_id)
        if state is None:
            errors[config_key] = f"Entity '{entity_id}' does not exist"
        elif state.state in ("unavailable", "unknown"):
            errors[config_key] = f"Entity '{entity_id}' is {state.state}"
        elif state.domain != expected_domain:
            errors[config_key] = (
                f"Expected {expected_domain} entity, got {state.domain}"
            )

    return errors if errors else None


async def validate_notify_service(hass, notify_service: str) -> str | None:
    """Validate that a notify service exists.

    Args:
        hass: Home Assistant instance
        notify_service: Service string like "notify.mobile_app_xxx"

    Returns:
        None if valid, or error message string
    """
    if not notify_service:
        return "Notify service is required"

    if not notify_service.startswith("notify."):
        return "Notify service must start with 'notify.'"

    # Parse domain and service name
    # Format: "notify.mobile_app_xxx" -> domain="notify", service="mobile_app_xxx"
    parts = notify_service.split(".", 1)
    if len(parts) != 2:
        return "Invalid notify service format"

    domain, service_name = parts

    # Check if notify service exists
    services = hass.services.async_services()
    if domain not in services:
        return f"Notify domain '{domain}' not found"

    if service_name not in services[domain]:
        return f"Notify service '{service_name}' not found in {domain}"

    return None


async def get_notify_services(hass) -> list[str]:
    """Get list of available notify services.

    Args:
        hass: Home Assistant instance

    Returns:
        List of notify service strings like ["notify.mobile_app"]
    """
    services = hass.services.async_services()
    notify_services = []

    if "notify" in services:
        for service_name in services["notify"].keys():
            notify_services.append(f"notify.{service_name}")

    return sorted(notify_services)


async def get_weather_entities(hass) -> list[str]:
    """Get list of available weather entities.

    Args:
        hass: Home Assistant instance

    Returns:
        List of weather entity IDs like ["weather.home", "weather.forecast"]
    """
    weather_entities = []
    for state in hass.states.async_all():
        if state.domain == "weather":
            weather_entities.append(state.entity_id)

    return sorted(weather_entities)


def get_current_notify_service(config_entry) -> str:
    """Get the current notify service from options or data (for backward compatibility).

    Args:
        config_entry: Config entry instance

    Returns:
        Current notify service string, or empty string if not set.
    """
    from ..const import CONF_NOTIFY_SERVICE

    # Check options first (new location)
    if CONF_NOTIFY_SERVICE in config_entry.options:
        return config_entry.options[CONF_NOTIFY_SERVICE]
    # Fall back to data (old location for existing entries)
    if CONF_NOTIFY_SERVICE in config_entry.data:
        return config_entry.data[CONF_NOTIFY_SERVICE]
    return ""
