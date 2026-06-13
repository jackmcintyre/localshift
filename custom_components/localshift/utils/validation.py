"""Entity validation and health tracking for LocalShift integration.

Provides centralized error handling for missing/invalid entities,
tracking entity health status, and generating user-friendly error messages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
)
from .entity_configs import (
    ENTITY_CONFIG,
    FAILURE_THRESHOLD_ERROR,
    FAILURE_THRESHOLD_WARNING,
    LOCALSHIFT_ENTITY_CONFIG,
    STALENESS_THRESHOLDS,
    EntityCategory,
)

_LOGGER = logging.getLogger(__name__)


class EntityStatus(Enum):
    """Health status of an entity."""

    OK = "ok"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    MISSING = "missing"
    INVALID_VALUE = "invalid_value"
    STALE = "stale"


class IntegrationStatus(Enum):
    """Overall integration health status."""

    OK = "ok"  # All required entities healthy
    DEGRADED = "degraded"  # Some entities missing/unavailable but can function
    ERROR = "error"  # Critical entities missing, cannot function properly


@dataclass
class EntityHealth:
    """Health status for a single entity."""

    entity_id: str
    config_key: str
    category: EntityCategory
    status: EntityStatus
    last_check: datetime
    last_valid_value: Any = None
    last_valid_time: datetime | None = None
    error_message: str = ""
    consecutive_failures: int = 0
    is_broken: bool = False  # Marked broken after FAILURE_THRESHOLD_ERROR failures

    @property
    def is_healthy(self) -> bool:
        """Return True if entity is healthy."""
        return self.status == EntityStatus.OK and not self.is_broken

    @property
    def is_available(self) -> bool:
        """Return True if entity has a usable value."""
        return (
            self.status in (EntityStatus.OK, EntityStatus.STALE) and not self.is_broken
        )


@dataclass
class ValidationResult:
    """Result of validating an entity's value."""

    is_valid: bool
    value: Any
    error_message: str = ""
    warning_message: str = ""


class EntityValidator:
    """Centralized entity validation and health tracking.

    Provides:
    - Entity existence and availability checking
    - Value validation (type, range, constraints)
    - Staleness detection
    - Aggregate health status
    - User-friendly error messages
    """

    def __init__(self, hass: HomeAssistant, get_entity_id_func: callable) -> None:
        """Initialize the entity validator.

        Args:
            hass: Home Assistant instance
            get_entity_id_func: Function to get entity IDs by config key

        """
        self.hass = hass
        self._get_entity_id = get_entity_id_func
        self._entity_health: dict[str, EntityHealth] = {}
        self._localshift_entity_health: dict[str, EntityHealth] = {}
        self._last_full_check: datetime | None = None
        self._cached_status: IntegrationStatus = IntegrationStatus.OK
        self._cached_errors: list[str] = []
        self._cached_warnings: list[str] = []

        # Initialize health tracking for all known entities
        for config_key, config in ENTITY_CONFIG.items():
            entity_id = self._get_entity_id(config_key)
            self._entity_health[config_key] = EntityHealth(
                entity_id=entity_id,
                config_key=config_key,
                category=config["category"],
                status=EntityStatus.OK,
                last_check=dt_util.now(),
            )

    def check_entity(self, config_key: str) -> EntityHealth:
        """Check health of a single entity."""
        entity_id = self._get_entity_id(config_key)
        config = ENTITY_CONFIG.get(config_key, {})
        health = self._entity_health.get(config_key)

        if health is None:
            health = EntityHealth(
                entity_id=entity_id,
                config_key=config_key,
                category=config.get("category", EntityCategory.OPTIONAL),
                status=EntityStatus.OK,
                last_check=dt_util.now(),
            )
            self._entity_health[config_key] = health

        health.entity_id = entity_id
        health.last_check = dt_util.now()

        if not entity_id and health.category == EntityCategory.OPTIONAL:
            health.status = EntityStatus.OK
            health.error_message = ""
            health.consecutive_failures = 0
            health.is_broken = False
            return health

        state = self.hass.states.get(entity_id)
        if state is None:
            return self._record_entity_failure(
                health,
                config_key,
                EntityStatus.MISSING,
                f"Entity '{entity_id}' does not exist",
            )

        failure_result = self._check_entity_state(entity_id, state)
        if failure_result:
            return self._record_entity_failure(health, config_key, *failure_result)

        validation = self._validate_entity_value(
            config_key, state.state, state.attributes
        )
        if not validation.is_valid:
            return self._record_entity_failure(
                health, config_key, EntityStatus.INVALID_VALUE, validation.error_message
            )

        staleness_result = self._check_entity_staleness(entity_id, config_key, state)
        if staleness_result:
            health.status = EntityStatus.STALE
            health.error_message = staleness_result
            return health

        if health.is_broken:
            _LOGGER.info(
                "Entity '%s' (%s) recovered from BROKEN status - now healthy",
                health.entity_id,
                config_key,
            )
            health.is_broken = False

        health.status = EntityStatus.OK
        health.error_message = ""
        health.last_valid_value = validation.value
        health.last_valid_time = dt_util.now()
        health.consecutive_failures = 0

        return health

    def _check_entity_state(
        self, entity_id: str, state: Any
    ) -> tuple[EntityStatus, str] | None:
        """Check if entity state indicates a failure condition.

        Returns:
            Tuple of (status, error_message) if failure, None if OK.
        """
        if state.state == "unavailable":
            return (EntityStatus.UNAVAILABLE, f"Entity '{entity_id}' is unavailable")
        if state.state == "unknown":
            return (EntityStatus.UNKNOWN, f"Entity '{entity_id}' has unknown state")
        return None

    def _check_entity_staleness(
        self, entity_id: str, config_key: str, state: Any
    ) -> str | None:
        """Check if entity data is stale.

        Returns:
            Error message string if stale, None if OK.
        """
        if entity_id.startswith("select."):
            return None

        staleness_threshold = STALENESS_THRESHOLDS.get(config_key)
        if not staleness_threshold:
            return None

        freshness_timestamp = self._get_freshness_timestamp(state)
        if freshness_timestamp is None:
            return None

        try:
            time_since_update = dt_util.now() - freshness_timestamp
        except TypeError:
            _LOGGER.debug(
                "Skipping staleness check for '%s' due to incompatible timestamp",
                entity_id,
            )
            return None

        if time_since_update > staleness_threshold:
            return (
                f"Entity '{entity_id}' data is stale "
                f"({time_since_update.total_seconds():.0f}s old)"
            )
        return None

    def _record_entity_failure(
        self,
        health: EntityHealth,
        config_key: str,
        status: EntityStatus,
        error_message: str,
    ) -> EntityHealth:
        """Record an entity failure and check thresholds."""
        health.status = status
        health.error_message = error_message
        health.consecutive_failures += 1
        self._check_failure_thresholds(health, config_key)
        return health

    def _get_freshness_timestamp(self, state: Any) -> datetime | None:
        """Return the best timestamp for staleness checks.

        Prefer ``last_reported`` when available because Home Assistant updates
        it when telemetry is received even if value does not change. Fall back
        to ``last_updated`` for compatibility.
        """
        last_reported = getattr(state, "last_reported", None)
        if last_reported is not None:
            return last_reported

        return getattr(state, "last_updated", None)

    def _check_failure_thresholds(self, health: EntityHealth, config_key: str) -> None:
        """Check failure thresholds and escalate status if needed.

        Logs warnings at FAILURE_THRESHOLD_WARNING failures.
        Marks entity as broken at FAILURE_THRESHOLD_ERROR failures.
        Uses WARNING level for optional entities instead of ERROR.

        Args:
            health: EntityHealth to check
            config_key: Configuration key for logging

        """
        # Log warning at threshold
        if health.consecutive_failures == FAILURE_THRESHOLD_WARNING:
            _LOGGER.warning(
                "Entity '%s' (%s) has failed %d times consecutively - monitoring closely",
                health.entity_id,
                config_key,
                health.consecutive_failures,
            )

        # Mark as broken at error threshold
        if health.consecutive_failures >= FAILURE_THRESHOLD_ERROR:
            if not health.is_broken:
                health.is_broken = True
                # Use WARNING for optional entities, ERROR for required/recommended
                log_level = (
                    logging.WARNING
                    if health.category == EntityCategory.OPTIONAL
                    else logging.ERROR
                )
                _LOGGER.log(
                    log_level,
                    "Entity '%s' (%s) marked as BROKEN after %d consecutive failures - "
                    "check entity configuration or sensor availability",
                    health.entity_id,
                    config_key,
                    health.consecutive_failures,
                )

    def _validate_entity_value(
        self, config_key: str, state_value: str, attributes: dict
    ) -> ValidationResult:
        """Validate an entity's value against expected constraints."""
        config = ENTITY_CONFIG.get(config_key, {})
        expected_type = config.get("expected_type")

        if expected_type is None:
            return ValidationResult(is_valid=True, value=state_value)

        if expected_type == list:
            return self._validate_forecast_entity(config_key, attributes)

        if expected_type == bool:
            return self._validate_boolean_entity(state_value)

        if expected_type in ((int, float), int, float):
            return self._validate_numeric_entity(config_key, state_value, config)

        if expected_type == str:
            return self._validate_string_entity(config_key, state_value, config)

        return ValidationResult(is_valid=True, value=state_value)

    def _validate_forecast_entity(
        self, config_key: str, attributes: dict
    ) -> ValidationResult:
        """Validate forecast-type entities that store data in attributes."""
        if config_key in (CONF_PRICING_GENERAL_FORECAST, CONF_PRICING_FEED_IN_FORECAST):
            forecast = attributes.get("forecasts", [])
            if isinstance(forecast, list):
                return ValidationResult(is_valid=True, value=forecast)
            return ValidationResult(
                is_valid=False,
                value=[],
                error_message=f"'{config_key}' forecast attribute is not a list",
            )

        if config_key in (CONF_SOLCAST_FORECAST_TODAY, CONF_SOLCAST_FORECAST_TOMORROW):
            for attr_name in ("detailedForecast", "detailedHourly", "forecast"):
                forecast = attributes.get(attr_name)
                if isinstance(forecast, list):
                    return ValidationResult(is_valid=True, value=forecast)
            return ValidationResult(is_valid=True, value=[])

        return ValidationResult(is_valid=True, value=[])

    def _validate_boolean_entity(self, state_value: str) -> ValidationResult:
        """Validate boolean entity value."""
        bool_value = state_value.lower() in ("on", "true", "yes", "1")
        return ValidationResult(is_valid=True, value=bool_value)

    def _validate_numeric_entity(
        self, config_key: str, state_value: str, config: dict
    ) -> ValidationResult:
        """Validate numeric entity value with optional range checking."""
        try:
            num_value = float(state_value)
        except (ValueError, TypeError):
            return ValidationResult(
                is_valid=False,
                value=0.0,
                error_message=f"'{config_key}' value '{state_value}' is not a number",
            )

        min_val = config.get("min_value")
        max_val = config.get("max_value")

        if min_val is not None and num_value < min_val:
            return ValidationResult(
                is_valid=False,
                value=num_value,
                error_message=f"'{config_key}' value {num_value} is below minimum {min_val}",
                warning_message=f"'{config_key}' has unusual value: {num_value}",
            )

        if max_val is not None and num_value > max_val:
            return ValidationResult(
                is_valid=False,
                value=num_value,
                error_message=f"'{config_key}' value {num_value} is above maximum {max_val}",
                warning_message=f"'{config_key}' has unusual value: {num_value}",
            )

        return ValidationResult(is_valid=True, value=num_value)

    def _validate_string_entity(
        self, config_key: str, state_value: str, config: dict
    ) -> ValidationResult:
        """Validate string entity value with optional valid_values checking."""
        valid_values = config.get("valid_values")
        if valid_values and state_value not in valid_values:
            return ValidationResult(
                is_valid=False,
                value=state_value,
                error_message=(
                    f"'{config_key}' value '{state_value}' not in valid values: {valid_values}"
                ),
            )
        return ValidationResult(is_valid=True, value=state_value)

    def check_all_entities(self) -> dict[str, EntityHealth]:
        """Check health of all tracked entities.

        Returns:
            Dictionary mapping config keys to EntityHealth

        """
        for config_key in ENTITY_CONFIG:
            self.check_entity(config_key)

        self._last_full_check = dt_util.now()
        self._update_cached_status()

        return self._entity_health

    def _validate_type_value(
        self, value_str: str, expected_type: Any, entity_id: str
    ) -> tuple[bool, Any, str]:
        """Validate a state value against an expected type.

        Args:
            value_str: The state value as string
            expected_type: Type to validate against (bool, int, float, str, or tuple of types)
            entity_id: Entity ID for error messages

        Returns:
            Tuple of (is_valid, parsed_value, error_message)

        """
        if expected_type is None:
            return True, value_str, ""

        # Handle boolean
        if expected_type == bool:
            bool_value = value_str.lower() in ("on", "true", "yes", "1")
            return True, bool_value, ""

        # Handle numeric (int or float)
        if expected_type in ((int, float), int, float):
            try:
                num_value = float(value_str)
            except (ValueError, TypeError):
                return False, 0.0, f"value '{value_str}' is not a number"
            return True, num_value, ""

        # Handle string
        if expected_type == str:
            return True, value_str, ""

        # Default: accept as is
        return True, value_str, ""

    def check_all_localshift_entities(self) -> dict[str, dict[str, Any]]:
        """Check health of all LocalShift internal entities."""
        now = dt_util.now()
        results: dict[str, EntityHealth] = {}

        for entity_id, config in LOCALSHIFT_ENTITY_CONFIG.items():
            health = self._check_single_localshift_entity(entity_id, config, now)
            results[entity_id] = health

        summary = self._serialize_localshift_health(results)
        self._localshift_entity_health = results
        return summary

    def check_orphaned_owned_entities(
        self, config_entry_id: str
    ) -> dict[str, dict[str, Any]]:
        """Return registry entries owned by this config entry that are genuinely unprovided.

        A genuine orphan is a registry entry that the integration no longer provides:
        its live state is either missing (None) or HA reports it as 'unavailable'.
        This typically occurs for entities (numbers, switches, sensors) that have been
        removed or renamed in a newer code version while the registry entry persists
        (e.g. number.localshift_cycle_penalty after the slider was removed).

        Entries that are NOT considered orphans:
        - Entries in LOCALSHIFT_ENTITY_CONFIG (health-tracked; already handled elsewhere).
        - User-disabled entries (entry.disabled_by is not None) — a disabled entity
          is intentionally inactive, not a code-removed ghost.
        - Entries whose live state is any real value including 'unknown' — the entity
          is still being provided by the integration (insufficient data, startup, etc.).

        Args:
            config_entry_id: The config entry id for this localshift instance.

        Returns:
            Dict mapping entity_id -> {state, disabled, restored} for each orphan,
            where restored=True indicates the state was absent (None) rather than
            'unavailable'.
        """
        registry = er.async_get(self.hass)
        orphans: dict[str, dict[str, Any]] = {}
        for entry in registry.entities.get_entries_for_config_entry_id(config_entry_id):
            if entry.entity_id in LOCALSHIFT_ENTITY_CONFIG:
                continue
            # User-disabled entries are intentionally inactive — not ghosts
            if entry.disabled_by is not None:
                continue
            state = self.hass.states.get(entry.entity_id)
            # Only flag when the integration genuinely no longer provides the entity
            if state is not None and state.state != "unavailable":
                continue
            orphans[entry.entity_id] = {
                "state": "unavailable",
                "disabled": False,
                "restored": state is None,
            }
        return orphans

    def _check_single_localshift_entity(
        self, entity_id: str, config: dict, now: datetime
    ) -> EntityHealth:
        """Check health of a single LocalShift internal entity."""
        health = self._localshift_entity_health.get(entity_id)
        if health is None:
            health = EntityHealth(
                entity_id=entity_id,
                config_key=entity_id,
                category=config["category"],
                status=EntityStatus.OK,
                last_check=now,
            )
            self._localshift_entity_health[entity_id] = health
        else:
            health.entity_id = entity_id
            health.last_check = now

        state = self.hass.states.get(entity_id)
        if state is None:
            return self._record_entity_failure(
                health,
                entity_id,
                EntityStatus.MISSING,
                f"Entity '{entity_id}' does not exist",
            )

        failure_result = self._check_entity_state(entity_id, state)
        if failure_result:
            health.status = failure_result[0]
            health.error_message = failure_result[1]
            health.consecutive_failures += 1
            self._check_failure_thresholds(health, entity_id)
            return health

        parsed_value = self._validate_and_parse_entity(entity_id, config, state, health)
        if parsed_value is None:
            return health

        staleness_error = self._check_localshift_staleness(
            entity_id, config, state, now
        )
        if staleness_error:
            health.status = EntityStatus.STALE
            health.error_message = staleness_error
            return health

        if health.is_broken:
            _LOGGER.info(
                "Entity '%s' recovered from BROKEN status - now healthy",
                entity_id,
            )
            health.is_broken = False
        health.status = EntityStatus.OK
        health.error_message = ""
        health.last_valid_value = parsed_value
        health.last_valid_time = now
        health.consecutive_failures = 0
        return health

    def _validate_and_parse_entity(
        self, entity_id: str, config: dict, state: Any, health: EntityHealth
    ) -> Any | None:
        """Validate entity type and parse value. Returns None on failure."""
        expected_type = config.get("expected_type")
        if expected_type is None:
            return state.state

        is_valid, parsed_value, error_msg = self._validate_type_value(
            state.state, expected_type, entity_id
        )
        if not is_valid:
            health.status = EntityStatus.INVALID_VALUE
            health.error_message = error_msg
            health.consecutive_failures += 1
            self._check_failure_thresholds(health, entity_id)
            return None
        return parsed_value

    def _check_localshift_staleness(
        self, entity_id: str, config: dict, state: Any, now: datetime
    ) -> str | None:
        """Check if LocalShift entity is stale. Returns error message if stale."""
        staleness_minutes = config.get("staleness_minutes")
        if staleness_minutes is None:
            return None

        threshold = timedelta(minutes=staleness_minutes)
        freshness_ts = self._get_freshness_timestamp(state)
        if freshness_ts is None:
            return None

        try:
            age = now - freshness_ts
        except TypeError:
            _LOGGER.debug(
                "Skipping staleness for %s due to timestamp mismatch",
                entity_id,
            )
            return None

        if age > threshold:
            return (
                f"Entity '{entity_id}' data is stale ({age.total_seconds():.0f}s old)"
            )
        return None

    def _serialize_localshift_health(
        self, results: dict[str, EntityHealth]
    ) -> dict[str, dict[str, Any]]:
        """Serialize entity health results to plain dicts."""
        summary: dict[str, dict[str, Any]] = {}
        for entity_id, health in results.items():
            summary[entity_id] = {
                "entity_id": health.entity_id,
                "status": health.status.value,
                "category": health.category.value,
                "last_valid_time": (
                    health.last_valid_time.isoformat()
                    if health.last_valid_time
                    else None
                ),
                "consecutive_failures": health.consecutive_failures,
                "is_broken": health.is_broken,
                "error_message": health.error_message if health.error_message else None,
            }
        return summary

    def _update_cached_status(self) -> None:
        """Update cached integration status and error messages."""
        errors = []
        warnings = []
        has_required_error = False
        has_degraded = False

        for config_key, health in self._entity_health.items():
            if health.is_healthy:
                continue

            config = ENTITY_CONFIG.get(config_key, {})
            description = config.get("description", config_key)
            msg = self._format_health_error_message(health, description)

            is_error, is_warning = self._categorize_health_severity(health)
            if is_error:
                has_required_error = True
                errors.append(msg)
            elif is_warning:
                warnings.append(msg)
                has_degraded = True

        if has_required_error:
            self._cached_status = IntegrationStatus.ERROR
        elif has_degraded:
            self._cached_status = IntegrationStatus.DEGRADED
        else:
            self._cached_status = IntegrationStatus.OK

        self._cached_errors = errors
        self._cached_warnings = warnings

    def _format_health_error_message(
        self, health: EntityHealth, description: str
    ) -> str:
        """Format an error message for an entity health status."""
        status_messages = {
            EntityStatus.MISSING: f"{description}: entity not found",
            EntityStatus.UNAVAILABLE: f"{description}: unavailable",
            EntityStatus.UNKNOWN: f"{description}: unknown state",
            EntityStatus.INVALID_VALUE: f"{description}: {health.error_message}",
            EntityStatus.STALE: f"{description}: data stale",
        }
        return status_messages.get(
            health.status, f"{description}: {health.error_message}"
        )

    def _categorize_health_severity(self, health: EntityHealth) -> tuple[bool, bool]:
        """Categorize health issue severity.

        Returns:
            Tuple of (is_error, is_warning).
        """
        if health.category != EntityCategory.REQUIRED:
            return (False, health.category == EntityCategory.RECOMMENDED)

        if health.status in (
            EntityStatus.MISSING,
            EntityStatus.UNAVAILABLE,
            EntityStatus.INVALID_VALUE,
        ):
            return (True, False)

        if health.status == EntityStatus.STALE:
            return (False, True)

        return (False, False)

    @property
    def status(self) -> IntegrationStatus:
        """Get overall integration status."""
        return self._cached_status

    @property
    def errors(self) -> list[str]:
        """Get list of current error messages."""
        return self._cached_errors.copy()

    @property
    def warnings(self) -> list[str]:
        """Get list of current warning messages."""
        return self._cached_warnings.copy()

    def get_health_summary(self) -> dict[str, Any]:
        """Get a summary of entity health for sensors and diagnostics.

        Returns:
            Dictionary with health summary data

        """
        summary = {
            "status": self._cached_status.value,
            "last_check": self._last_full_check.isoformat()
            if self._last_full_check
            else None,
            "errors": self._cached_errors,
            "warnings": self._cached_warnings,
            "entities": {},
        }

        for config_key, health in self._entity_health.items():
            summary["entities"][config_key] = {
                "entity_id": health.entity_id,
                "status": health.status.value,
                "category": health.category.value,
                "last_valid_time": (
                    health.last_valid_time.isoformat()
                    if health.last_valid_time
                    else None
                ),
                "consecutive_failures": health.consecutive_failures,
                "is_broken": health.is_broken,
                "error_message": health.error_message if health.error_message else None,
            }

        return summary

    def get_required_entities_status(self) -> dict[str, bool]:
        """Get status of required entities only.

        Returns:
            Dictionary mapping entity config keys to health status (True=healthy)

        """
        return {
            key: health.is_healthy
            for key, health in self._entity_health.items()
            if health.category == EntityCategory.REQUIRED
        }

    def get_user_friendly_message(self) -> str:
        """Get a user-friendly status message.

        Returns:
            Human-readable status message

        """
        if self._cached_status == IntegrationStatus.OK:
            return "All systems operational"

        if self._cached_status == IntegrationStatus.ERROR:
            if len(self._cached_errors) == 1:
                return f"Error: {self._cached_errors[0]}"
            return f"Multiple errors detected: {', '.join(self._cached_errors[:3])}"

        # DEGRADED
        if len(self._cached_warnings) == 1:
            return f"Warning: {self._cached_warnings[0]}"
        return f"Degraded: {len(self._cached_warnings)} issues detected"

    def should_allow_automation(self) -> bool:
        """Check if automation should be allowed based on entity health.

        Returns:
            True if automation can proceed, False if critical errors exist

        """
        # Check all required entities
        for config_key, health in self._entity_health.items():
            if health.category == EntityCategory.REQUIRED:
                # Block if entity is missing, unavailable, or marked as broken
                if health.status in (EntityStatus.MISSING, EntityStatus.UNAVAILABLE):
                    _LOGGER.warning(
                        "Automation blocked: required entity '%s' is %s",
                        config_key,
                        health.status.value,
                    )
                    return False
                if health.is_broken:
                    _LOGGER.warning(
                        "Automation blocked: required entity '%s' is marked as BROKEN "
                        "(%d consecutive failures)",
                        config_key,
                        health.consecutive_failures,
                    )
                    return False

        return True

    def reset_broken_status(self, config_key: str | None = None) -> None:
        """Reset broken status for one or all entities.

        This allows recovery without restart when:
        - User reconfigures entity via options flow
        - Entity becomes available again after being broken

        Args:
            config_key: Specific entity to reset, or None to reset all

        """
        if config_key is not None:
            # Reset specific entity
            health = self._entity_health.get(config_key)
            if health is not None and health.is_broken:
                health.is_broken = False
                health.consecutive_failures = 0
                _LOGGER.info(
                    "Reset broken status for entity '%s' (%s)",
                    health.entity_id,
                    config_key,
                )
        else:
            # Reset all broken entities
            reset_count = 0
            for _key, health in self._entity_health.items():
                if health.is_broken:
                    health.is_broken = False
                    health.consecutive_failures = 0
                    reset_count += 1

            if reset_count > 0:
                _LOGGER.info(
                    "Reset broken status for %d entities",
                    reset_count,
                )

    def reset_entity_tracking(self, config_key: str) -> None:
        """Reset tracking for a specific entity when its ID changes.

        Called when user reconfigures an entity via options flow.
        Resets the entity health tracking to pick up the new entity ID.

        Args:
            config_key: Configuration key for the entity

        """
        health = self._entity_health.get(config_key)
        if health is not None:
            old_entity_id = health.entity_id
            new_entity_id = self._get_entity_id(config_key)

            # Reset tracking state
            health.entity_id = new_entity_id
            health.status = EntityStatus.OK
            health.error_message = ""
            health.consecutive_failures = 0
            health.is_broken = False
            health.last_valid_value = None
            health.last_valid_time = None

            _LOGGER.info(
                "Reset entity tracking for '%s': '%s' -> '%s'",
                config_key,
                old_entity_id,
                new_entity_id,
            )
