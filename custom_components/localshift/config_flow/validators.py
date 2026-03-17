"""Entity validation functions for config flow.

This module contains functions to validate entities, check domains,
and validate notify services.
"""

from __future__ import annotations

import re

from homeassistant.core import HomeAssistant

from ..const import (
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
    PRICING_SOURCE_AMBER,
    PRICING_SOURCE_AMBER_EXPRESS,
)


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


async def discover_pricing_entities(
    hass: HomeAssistant, pricing_source: str
) -> dict[str, str | None]:
    """Discover Amber pricing entities by suffix.

    Searches for entities matching known suffixes that are consistent
    across all Amber Electric installations.

    Args:
        hass: Home Assistant instance
        pricing_source: Either PRICING_SOURCE_AMBER or PRICING_SOURCE_AMBER_EXPRESS

    Returns:
        Dict mapping config_key to discovered entity_id (or None if not found)
    """
    # Define suffixes for each pricing entity type
    suffixes = {
        CONF_PRICING_GENERAL_PRICE: "_general_price",
        CONF_PRICING_FEED_IN_PRICE: "_feed_in_price",
        CONF_PRICING_GENERAL_FORECAST: "_general_forecast",
        CONF_PRICING_FEED_IN_FORECAST: "_feed_in_forecast",
        CONF_PRICING_PRICE_SPIKE: "_price_spike",
    }

    # For Amber Express, we need to handle the prefixed variants
    prefix = "amber_express_" if pricing_source == PRICING_SOURCE_AMBER_EXPRESS else ""

    discovered = {}

    # Get all sensor and binary_sensor states
    sensor_states = [
        state
        for state in hass.states.async_all()
        if state.domain in ("sensor", "binary_sensor")
    ]

    for config_key, suffix in suffixes.items():
        # Build the expected entity ID pattern
        if pricing_source == PRICING_SOURCE_AMBER:
            # Standard Amber: sensor.100h_* or sensor.* (we match by suffix)
            patterns = [
                f"sensor.*{suffix}",
                f"binary_sensor.*{suffix}" if "_price_spike" in suffix else None,
            ]
            # Filter out None patterns
            patterns = [p for p in patterns if p is not None]
        else:
            # Amber Express: sensor.amber_express_100h_* or sensor.*amber_express_*
            patterns = [
                f"sensor.{prefix}.*{suffix}",
                f"binary_sensor.{prefix}.*{suffix}"
                if "_price_spike" in suffix
                else None,
            ]
            patterns = [p for p in patterns if p is not None]

        # Find matching entities
        matches = []
        for state in sensor_states:
            entity_id = state.entity_id
            # Check if entity matches any of our patterns
            for pattern in patterns:
                # Convert glob-like pattern to regex
                regex_pattern = pattern.replace(".", r"\.").replace("*", ".*")
                if re.match(regex_pattern, entity_id):
                    matches.append((entity_id, state))
                    break

        # Select best match: prefer entities with 'amber' in name
        if matches:
            # Sort by preference: entities containing 'amber' first, then alphabetical
            matches.sort(
                key=lambda x: (
                    "amber"
                    not in x[0].lower(),  # False (0) comes first if contains 'amber'
                    x[0],  # Then sort by entity ID
                )
            )
            discovered[config_key] = matches[0][0]  # Entity ID of best match
        else:
            discovered[config_key] = None

    return discovered
