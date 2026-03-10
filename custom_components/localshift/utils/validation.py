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
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_TESLEMETRY_ALLOW_EXPORT,
    CONF_TESLEMETRY_BACKUP_RESERVE,
    CONF_TESLEMETRY_BATTERY_POWER,
    CONF_TESLEMETRY_GRID_POWER,
    CONF_TESLEMETRY_LOAD_POWER,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    CONF_TESLEMETRY_SOLAR_POWER,
    CONF_WEATHER_ENTITY,
)

_LOGGER = logging.getLogger(__name__)


class EntityCategory(Enum):
    """Category of entity for error handling priority."""

    REQUIRED = "required"  # Integration cannot function without
    RECOMMENDED = "recommended"  # Degrades functionality if missing
    OPTIONAL = "optional"  # Nice to have, not critical


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


# Entity configuration: maps config keys to their category and validation rules
ENTITY_CONFIG: dict[str, dict[str, Any]] = {
    # Teslemetry - REQUIRED (core battery control)
    CONF_TESLEMETRY_OPERATION_MODE: {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "valid_values": ["self_consumption", "backup", "autonomous"],
        "description": "Battery operation mode",
    },
    CONF_TESLEMETRY_BACKUP_RESERVE: {
        "category": EntityCategory.REQUIRED,
        "expected_type": (int, float),
        "min_value": 0,
        "max_value": 100,
        "description": "Backup reserve percentage",
    },
    CONF_TESLEMETRY_SOC: {
        "category": EntityCategory.REQUIRED,
        "expected_type": (int, float),
        "min_value": 0,
        "max_value": 100,
        "description": "Battery state of charge",
    },
    CONF_TESLEMETRY_GRID_POWER: {
        "category": EntityCategory.RECOMMENDED,
        "expected_type": (int, float),
        "description": "Grid power flow (kW)",
    },
    CONF_TESLEMETRY_BATTERY_POWER: {
        "category": EntityCategory.RECOMMENDED,
        "expected_type": (int, float),
        "description": "Battery power flow (kW)",
    },
    CONF_TESLEMETRY_SOLAR_POWER: {
        "category": EntityCategory.RECOMMENDED,
        "expected_type": (int, float),
        "min_value": 0,
        "description": "Solar power generation (kW)",
    },
    CONF_TESLEMETRY_LOAD_POWER: {
        "category": EntityCategory.RECOMMENDED,
        "expected_type": (int, float),
        "min_value": 0,
        "description": "Home load power (kW)",
    },
    CONF_TESLEMETRY_ALLOW_EXPORT: {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "valid_values": ["pv_only", "battery_ok"],
        "description": "Export mode",
    },
    # Pricing - REQUIRED for price-based decisions
    CONF_PRICING_GENERAL_PRICE: {
        "category": EntityCategory.REQUIRED,
        "expected_type": (int, float),
        "description": "Current buy price ($/kWh)",
    },
    CONF_PRICING_FEED_IN_PRICE: {
        "category": EntityCategory.REQUIRED,
        "expected_type": (int, float),
        "description": "Current sell price ($/kWh)",
    },
    CONF_PRICING_GENERAL_FORECAST: {
        "category": EntityCategory.RECOMMENDED,
        "expected_type": list,
        "description": "Buy price forecast",
    },
    CONF_PRICING_FEED_IN_FORECAST: {
        "category": EntityCategory.RECOMMENDED,
        "expected_type": list,
        "description": "Sell price forecast",
    },
    CONF_PRICING_PRICE_SPIKE: {
        "category": EntityCategory.OPTIONAL,
        "expected_type": bool,
        "description": "Price spike indicator",
    },
    # Solcast - RECOMMENDED for solar forecasting
    CONF_SOLCAST_FORECAST_TODAY: {
        "category": EntityCategory.RECOMMENDED,
        "expected_type": list,
        "description": "Today's solar forecast",
    },
    CONF_SOLCAST_FORECAST_TOMORROW: {
        "category": EntityCategory.OPTIONAL,
        "expected_type": list,
        "description": "Tomorrow's solar forecast",
    },
    # Weather - OPTIONAL
    CONF_WEATHER_ENTITY: {
        "category": EntityCategory.OPTIONAL,
        "description": "Weather entity for load prediction",
    },
}

# Staleness thresholds (how long before data is considered stale)
STALENESS_THRESHOLDS: dict[str, timedelta] = {
    # SOC can remain unchanged for extended periods during equilibrium.
    # Use a longer threshold to avoid false stale warnings while still detecting
    # genuinely dead telemetry feeds.
    CONF_TESLEMETRY_SOC: timedelta(minutes=30),
    CONF_TESLEMETRY_OPERATION_MODE: timedelta(minutes=5),
    CONF_PRICING_GENERAL_PRICE: timedelta(minutes=10),
    CONF_PRICING_FEED_IN_PRICE: timedelta(minutes=10),
    CONF_SOLCAST_FORECAST_TODAY: timedelta(hours=1),
    CONF_SOLCAST_FORECAST_TOMORROW: timedelta(hours=6),
}

# Consecutive failure thresholds before escalating status
FAILURE_THRESHOLD_WARNING = 3  # After 3 failures, log warning
FAILURE_THRESHOLD_ERROR = 10  # After 10 failures, consider entity broken

# LocalShift internal entity health configuration
# Maps entity_id to validation and health check parameters
LOCALSHIFT_ENTITY_CONFIG: dict[str, dict[str, Any]] = {
    # Sensors - REQUIRED core
    "sensor.localshift_price_cheap_effective": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_price_cheap_charge_stop": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_solar_weighted_avg_fit": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_forecast_battery": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_cost_electricity_net": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_optimizer_plan": {
        "category": EntityCategory.REQUIRED,
        "expected_type": int,
        "staleness_minutes": 15,
    },
    "sensor.localshift_forecast_prices": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_optimizer_plan_grid": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_forecast_diagnostics": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": 15,
    },
    "sensor.localshift_target_soc_minimum": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": None,
    },
    "sensor.localshift_excess_solar_kwh": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_load_shift_signal": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": 15,
    },
    "sensor.localshift_forecast_status": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": 15,
    },
    "sensor.localshift_automation_ready": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": 15,
    },
    "sensor.localshift_optimizer_plan_detailed": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": 15,
    },
    "sensor.localshift_optimizer_summary": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": 15,
    },
    # Binary sensors - all REQUIRED
    "binary_sensor.localshift_price_spike_coming": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_discharge_forced": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_charge_forced": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_charge_boost": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_price_expensive_coming": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_solar_can_reach_target": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_charge_boost_needed": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_demand_window": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_excess_solar_available": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    "binary_sensor.localshift_tesla_override_active": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": 15,
    },
    # Switches - all REQUIRED (static)
    "switch.localshift_automation_enabled": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "switch.localshift_spike_discharge_enabled": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "switch.localshift_spike_discharge_conservative": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "switch.localshift_dry_run": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "switch.localshift_demand_window_block": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "switch.localshift_allow_dw_entry_under_target": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "switch.localshift_notifications_enabled": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    "switch.localshift_enable_learning": {
        "category": EntityCategory.REQUIRED,
        "expected_type": bool,
        "staleness_minutes": None,
    },
    # Selects - REQUIRED (static)
    "select.localshift_battery_mode": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": None,
    },
    "select.localshift_optimization_mode": {
        "category": EntityCategory.REQUIRED,
        "expected_type": str,
        "staleness_minutes": None,
    },
    # Numbers - REQUIRED (static)
    "number.localshift_cheap_price_percentile": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": None,
    },
    "number.localshift_max_pre_charge_price": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": None,
    },
    "number.localshift_battery_target": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": None,
    },
    "number.localshift_minimum_target_soc": {
        "category": EntityCategory.REQUIRED,
        "expected_type": float,
        "staleness_minutes": None,
    },
    # Optional diagnostic sensors
    "sensor.localshift_forecast_accuracy": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": float,
        "staleness_minutes": 60,
    },
    "sensor.localshift_extended_forecast_accuracy": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": float,
        "staleness_minutes": 1440,
    },
    "sensor.localshift_solar_forecast_accuracy": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": float,
        "staleness_minutes": 1440,
    },
    "sensor.localshift_decision_lag": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": float,
        "staleness_minutes": 15,
    },
    "sensor.localshift_cost_reconciliation": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": str,
        "staleness_minutes": 1440,
    },
    "sensor.localshift_learning_status": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": str,
        "staleness_minutes": 60,
    },
    "sensor.localshift_decision_quality": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": float,
        "staleness_minutes": 60,
    },
    "sensor.localshift_learning_decision_history": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": int,
        "staleness_minutes": 60,
    },
    "sensor.localshift_decision_log": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": str,
        "staleness_minutes": 60,
    },
    "sensor.localshift_forecast_history": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": int,
        "staleness_minutes": 1440,
    },
    "sensor.localshift_integration_status": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": str,
        "staleness_minutes": 15,
    },
    "sensor.localshift_entity_health": {
        "category": EntityCategory.OPTIONAL,
        "expected_type": str,
        "staleness_minutes": 15,
    },
}


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
        """Check health of a single entity.

        Args:
            config_key: Configuration key for the entity

        Returns:
            EntityHealth with current status

        """
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

        # Skip checking optional entities that are not configured (empty entity_id)
        # This prevents false "MISSING" errors for optional features
        if not entity_id and health.category == EntityCategory.OPTIONAL:
            health.status = EntityStatus.OK
            health.error_message = ""
            health.consecutive_failures = 0
            health.is_broken = False
            return health

        # Check if entity exists
        state = self.hass.states.get(entity_id)
        if state is None:
            health.status = EntityStatus.MISSING
            health.error_message = f"Entity '{entity_id}' does not exist"
            health.consecutive_failures += 1
            self._check_failure_thresholds(health, config_key)
            return health

        # Check if entity is unavailable or unknown
        if state.state == "unavailable":
            health.status = EntityStatus.UNAVAILABLE
            health.error_message = f"Entity '{entity_id}' is unavailable"
            health.consecutive_failures += 1
            self._check_failure_thresholds(health, config_key)
            return health

        if state.state == "unknown":
            health.status = EntityStatus.UNKNOWN
            health.error_message = f"Entity '{entity_id}' has unknown state"
            health.consecutive_failures += 1
            self._check_failure_thresholds(health, config_key)
            return health

        # Validate the value if we have validation rules
        validation = self._validate_entity_value(
            config_key, state.state, state.attributes
        )
        if not validation.is_valid:
            health.status = EntityStatus.INVALID_VALUE
            health.error_message = validation.error_message
            health.consecutive_failures += 1
            self._check_failure_thresholds(health, config_key)
            return health

        # Check for staleness using entity's actual last_updated time
        # (not our internal last_valid_time which tracks when we last validated it)
        #
        # IMPORTANT: Skip staleness check for select entities (Issue #342)
        # Select entities only update last_updated when the VALUE changes, not periodically.
        # If a battery stays in "autonomous" mode for hours, the entity is still valid -
        # the data is NOT stale, it's just unchanged.
        staleness_threshold = STALENESS_THRESHOLDS.get(config_key)
        if staleness_threshold and not entity_id.startswith("select."):
            freshness_timestamp = self._get_freshness_timestamp(state)
            if freshness_timestamp is not None:
                try:
                    time_since_update = dt_util.now() - freshness_timestamp
                except TypeError:
                    # Defensive guard for malformed timestamps (e.g., naive vs aware)
                    _LOGGER.debug(
                        "Skipping staleness check for '%s' due to incompatible timestamp",
                        entity_id,
                    )
                else:
                    if time_since_update > staleness_threshold:
                        health.status = EntityStatus.STALE
                        health.error_message = (
                            f"Entity '{entity_id}' data is stale "
                            f"({time_since_update.total_seconds():.0f}s old)"
                        )
                        # Don't increment failures for stale - it still has a value
                        return health

        # Entity is healthy - clear broken status if it was previously set
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
        """Validate an entity's value against expected constraints.

        Args:
            config_key: Configuration key for the entity
            state_value: The entity's state value as string
            attributes: Entity attributes

        Returns:
            ValidationResult with validation outcome

        """
        config = ENTITY_CONFIG.get(config_key, {})

        # Handle entities that store data in attributes (forecasts)
        if config.get("expected_type") == list:
            # For forecast entities, check attributes
            if config_key in (
                CONF_PRICING_GENERAL_FORECAST,
                CONF_PRICING_FEED_IN_FORECAST,
            ):
                forecast = attributes.get("forecasts", [])
                if isinstance(forecast, list):
                    return ValidationResult(is_valid=True, value=forecast)
                return ValidationResult(
                    is_valid=False,
                    value=[],
                    error_message=f"'{config_key}' forecast attribute is not a list",
                )

            # Solcast forecasts use different attribute names
            if config_key in (
                CONF_SOLCAST_FORECAST_TODAY,
                CONF_SOLCAST_FORECAST_TOMORROW,
            ):
                for attr_name in ("detailedForecast", "detailedHourly", "forecast"):
                    forecast = attributes.get(attr_name)
                    if isinstance(forecast, list):
                        return ValidationResult(is_valid=True, value=forecast)
                # Empty list is valid (no forecast data yet)
                return ValidationResult(is_valid=True, value=[])

        # Try to parse the state value
        expected_type = config.get("expected_type")
        if expected_type is None:
            # No type validation required
            return ValidationResult(is_valid=True, value=state_value)

        # Handle boolean entities
        if expected_type == bool:
            bool_value = state_value.lower() in ("on", "true", "yes", "1")
            return ValidationResult(is_valid=True, value=bool_value)

        # Handle numeric entities
        if expected_type in ((int, float), int, float):
            try:
                num_value = float(state_value)
            except (ValueError, TypeError):
                return ValidationResult(
                    is_valid=False,
                    value=0.0,
                    error_message=f"'{config_key}' value '{state_value}' is not a number",
                )

            # Range validation
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

        # Handle string entities with valid values
        if expected_type == str:
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

        # Default: accept the value
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
        """Check health of all LocalShift internal entities.

        Returns:
            Dictionary mapping entity_id to serialized health info dict

        """
        now = dt_util.now()
        results: dict[str, EntityHealth] = {}

        for entity_id, config in LOCALSHIFT_ENTITY_CONFIG.items():
            # Get or create health object
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

            # Check existence
            state = self.hass.states.get(entity_id)
            if state is None:
                health.status = EntityStatus.MISSING
                health.error_message = f"Entity '{entity_id}' does not exist"
                health.consecutive_failures += 1
                self._check_failure_thresholds(health, entity_id)
                results[entity_id] = health
                continue

            # Check unavailable/unknown
            if state.state == "unavailable":
                health.status = EntityStatus.UNAVAILABLE
                health.error_message = f"Entity '{entity_id}' is unavailable"
                health.consecutive_failures += 1
                self._check_failure_thresholds(health, entity_id)
                results[entity_id] = health
                continue
            if state.state == "unknown":
                health.status = EntityStatus.UNKNOWN
                health.error_message = f"Entity '{entity_id}' has unknown state"
                health.consecutive_failures += 1
                self._check_failure_thresholds(health, entity_id)
                results[entity_id] = health
                continue

            # Type validation
            expected_type = config.get("expected_type")
            if expected_type is not None:
                is_valid, parsed_value, error_msg = self._validate_type_value(
                    state.state, expected_type, entity_id
                )
                if not is_valid:
                    health.status = EntityStatus.INVALID_VALUE
                    health.error_message = error_msg
                    health.consecutive_failures += 1
                    self._check_failure_thresholds(health, entity_id)
                    results[entity_id] = health
                    continue
            else:
                parsed_value = state.state

            # Staleness check
            staleness_minutes = config.get("staleness_minutes")
            if staleness_minutes is not None:
                threshold = timedelta(minutes=staleness_minutes)
                freshness_ts = self._get_freshness_timestamp(state)
                if freshness_ts is not None:
                    try:
                        age = now - freshness_ts
                    except TypeError:
                        _LOGGER.debug(
                            "Skipping staleness for %s due to timestamp mismatch",
                            entity_id,
                        )
                    else:
                        if age > threshold:
                            health.status = EntityStatus.STALE
                            health.error_message = (
                                f"Entity '{entity_id}' data is stale "
                                f"({age.total_seconds():.0f}s old)"
                            )
                            results[entity_id] = health
                            continue

            # Entity is healthy
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
            results[entity_id] = health

        # Serialize to plain dicts for coordinator data
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

        self._localshift_entity_health = results
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
            category = health.category

            # Format error message
            if health.status == EntityStatus.MISSING:
                msg = f"{description}: entity not found"
            elif health.status == EntityStatus.UNAVAILABLE:
                msg = f"{description}: unavailable"
            elif health.status == EntityStatus.UNKNOWN:
                msg = f"{description}: unknown state"
            elif health.status == EntityStatus.INVALID_VALUE:
                msg = f"{description}: {health.error_message}"
            elif health.status == EntityStatus.STALE:
                msg = f"{description}: data stale"
            else:
                msg = f"{description}: {health.error_message}"

            # Categorize by severity
            if category == EntityCategory.REQUIRED:
                if health.status in (
                    EntityStatus.MISSING,
                    EntityStatus.UNAVAILABLE,
                    EntityStatus.INVALID_VALUE,
                ):
                    has_required_error = True
                    errors.append(msg)
                elif health.status == EntityStatus.STALE:
                    warnings.append(msg)
                    has_degraded = True
            elif category == EntityCategory.RECOMMENDED:
                warnings.append(msg)
                has_degraded = True
            # Optional entities only generate info-level messages

        # Determine overall status
        if has_required_error:
            self._cached_status = IntegrationStatus.ERROR
        elif has_degraded:
            self._cached_status = IntegrationStatus.DEGRADED
        else:
            self._cached_status = IntegrationStatus.OK

        self._cached_errors = errors
        self._cached_warnings = warnings

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
