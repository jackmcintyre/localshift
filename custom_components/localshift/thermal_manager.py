"""Thermal Manager for HVAC-aware load forecasting and control.

This module solves the chicken-and-egg feedback loop (Issue #137) by:
1. Learning HVAC power consumption from state changes
2. Separating HVAC load from baseline consumption
3. Using baseline-only for grid charging decisions
4. Predicting HVAC load from weather forecast

Additionally, it provides thermal management features (Issue #63):
- Daily thermal mode determination (HEAT/COOL/DRY/OFF)
- Pre-conditioning before demand window
- Solar tapering to consume excess solar
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CONF_COOLING_TRIGGER_TEMP,
    CONF_HEATING_TRIGGER_TEMP,
    CONF_HVAC_SAMPLE_INTERVAL,
    CONF_MIN_SETPOINT_CHANGE_INTERVAL,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_PRECONDITION_HOURS_BEFORE_DW,
    CONF_PRECONDITION_TEMP_OFFSET,
    CONF_SOLAR_TAPER_ENABLED,
    CONF_TEMP_MODEL_MIN_SAMPLES,
    CONF_THERMAL_HYSTERESIS,
    CONF_THERMAL_MANAGEMENT_ENABLED,
    CONF_THERMAL_OFF_FORECAST_CLEAR,
    CONF_THERMAL_OFF_TEMP_MARGIN,
    CONF_THERMAL_OFF_TIME,
    CONF_USER_OVERRIDE_COOLDOWN,
    DEFAULT_COOLING_TRIGGER_TEMP,
    DEFAULT_DEHUMIDIFY_TRIGGER_HUMIDITY,
    DEFAULT_HEATING_TRIGGER_TEMP,
    DEFAULT_HVAC_SAMPLE_INTERVAL,
    DEFAULT_MIN_SETPOINT_CHANGE_INTERVAL,
    DEFAULT_PRECONDITION_HOURS_BEFORE_DW,
    DEFAULT_PRECONDITION_TEMP_OFFSET,
    DEFAULT_SOLAR_TAPER_ENABLED,
    DEFAULT_TAPER_MAX_SETPOINT_OFFSET,
    DEFAULT_TEMP_MODEL_MIN_SAMPLES,
    DEFAULT_THERMAL_HYSTERESIS,
    DEFAULT_THERMAL_MANAGEMENT_ENABLED,
    DEFAULT_THERMAL_OFF_FORECAST_CLEAR,
    DEFAULT_THERMAL_OFF_TEMP_MARGIN,
    DEFAULT_THERMAL_OFF_TIME,
    DEFAULT_USER_OVERRIDE_COOLDOWN,
    ThermalMode,
)

if TYPE_CHECKING:
    from .coordinator_data import CoordinatorData

_LOGGER = logging.getLogger(__name__)

# Storage version and key for learned HVAC power data
STORAGE_VERSION = 1
STORAGE_KEY = "localshift_thermal_manager"


@dataclass
class LearnedHVACPower:
    """Learned power consumption for a climate entity.

    Supports both fixed power values (legacy) and temperature-correlated
    models (Issue #171). The temperature model provides more accurate
    predictions by accounting for outdoor temperature variation.

    Attributes:
        entity_id: Climate entity identifier.
        cooling_power_kw: Fixed cooling power (legacy, kW).
        heating_power_kw: Fixed heating power (legacy, kW).
        drying_power_kw: Fixed drying power (legacy, kW).
        sample_count: Number of state transition samples (legacy).
        confidence: Confidence level for fixed values.
        cooling_power_at_25c: Reference cooling power at 25°C outdoor.
        cooling_temp_coefficient: Additional kW per °C above 25°C.
        heating_power_at_15c: Reference heating power at 15°C outdoor.
        heating_temp_coefficient: Additional kW per °C below 15°C.
        cooling_samples: List of (outdoor_temp, power_kw) samples for cooling.
        heating_samples: List of (outdoor_temp, power_kw) samples for heating.
        temp_model_r2: R² value of temperature correlation model.
        temp_model_samples: Number of samples used for temperature model.
        temp_model_confidence: Confidence level for temperature model.
    """

    entity_id: str
    # Legacy: fixed power values
    cooling_power_kw: float = 0.0  # kW when cooling
    heating_power_kw: float = 0.0  # kW when heating
    drying_power_kw: float = 0.0  # kW when drying
    sample_count: int = 0
    confidence: str = "low"  # "low", "medium", "high"

    # Temperature-correlated model (Issue #171)
    cooling_power_at_25c: float = 0.0  # Reference power at 25°C outdoor
    cooling_temp_coefficient: float = 0.0  # Additional kW per °C above 25°C
    heating_power_at_15c: float = 0.0  # Reference power at 15°C outdoor
    heating_temp_coefficient: float = 0.0  # Additional kW per °C below 15°C

    # Sample storage for model fitting (not persisted)
    cooling_samples: list[tuple[float, float]] = field(default_factory=list)
    heating_samples: list[tuple[float, float]] = field(default_factory=list)

    # Model quality metrics
    temp_model_r2: float = 0.0  # R² of temperature correlation
    temp_model_samples: int = 0  # Samples used for temperature model
    temp_model_confidence: str = "low"  # Confidence for temp model


@dataclass
class ClimateEntityState:
    """Snapshot of a climate entity's current state."""

    entity_id: str
    state: str  # "off", "cool", "heat", "dry", "auto"
    hvac_action: str  # "off", "cooling", "heating", "drying", "idle"
    setpoint: float  # Target temperature in °C
    current_temperature: float | None  # Current room temperature
    is_controlled: bool  # Whether this entity is in the control subset
    timestamp: datetime | None = None  # When this state was recorded


class ThermalManager:
    """Manages HVAC-aware load forecasting and thermal control.

    This class provides:
    1. HVAC power learning from state changes
    2. Baseline/HVAC load separation
    3. Daily thermal mode determination
    4. Pre-conditioning logic
    5. Solar tapering logic
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        get_entity_id_func: Callable[[str], str],
        get_switch_state_func: Callable[[str], bool],
        get_option_func: Callable[[str, Any], Any],
    ) -> None:
        """Initialize thermal manager.

        Args:
            hass: Home Assistant instance.
            entry: Config entry.
            get_entity_id_func: Function to get configured entity IDs.
            get_switch_state_func: Function to get switch states.
            get_option_func: Function to get config options.
        """
        self.hass = hass
        self.entry = entry
        self._get_entity_id = get_entity_id_func
        self._get_switch_state = get_switch_state_func
        self._get_option = get_option_func

        # Storage for learned HVAC power
        self._store = Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)

        # Learned power data: entity_id -> LearnedHVACPower
        self._learned_power: dict[str, LearnedHVACPower] = {}

        # Previous climate states for change detection
        self._prev_climate_states: dict[str, ClimateEntityState] = {}

        # Previous load reading for power learning
        self._prev_load_kw: float = 0.0
        self._prev_load_timestamp: datetime | None = None

        # Original setpoints for controlled entities (to prevent ratchet bug)
        # These are the user-configured setpoints before any thermal adjustments
        self._original_setpoints: dict[str, float] = {}

        # Current active offset (to avoid re-applying the same offset)
        self._current_active_offset: float = 0.0

        # Real-time thermal control state
        self._thermal_activated_today: bool = False
        self._thermal_activated_at: datetime | None = None
        self._last_setpoint_change: datetime | None = None
        self._recent_room_temps: list[float] = []  # For trend detection
        self._last_avg_room_temp: float | None = None

        # User override detection
        self._last_applied_mode: dict[str, str] = {}  # entity_id -> "cool", "heat", "off"
        self._last_applied_setpoint: dict[str, float] = {}  # entity_id -> temp
        self._user_override_until: datetime | None = None
        self._user_override_reason: str = ""

        # Adaptive ramp: track if we're in ramp mode to adapt to user changes
        self._in_dw_ramp: bool = False

    # ------------------------------------------------------------------
    # Initialization and Storage
    # ------------------------------------------------------------------

    async def async_initialize(self) -> None:
        """Load persisted learned HVAC power data from storage."""
        data = await self._store.async_load()
        if data is not None and "learned_power" in data:
            for entity_id, power_data in data["learned_power"].items():
                self._learned_power[entity_id] = LearnedHVACPower(
                    entity_id=entity_id,
                    # Legacy fields
                    cooling_power_kw=power_data.get("cooling_power_kw", 0.0),
                    heating_power_kw=power_data.get("heating_power_kw", 0.0),
                    drying_power_kw=power_data.get("drying_power_kw", 0.0),
                    sample_count=power_data.get("sample_count", 0),
                    confidence=power_data.get("confidence", "low"),
                    # Temperature-correlated model fields (Issue #171)
                    cooling_power_at_25c=power_data.get("cooling_power_at_25c", 0.0),
                    cooling_temp_coefficient=power_data.get(
                        "cooling_temp_coefficient", 0.0
                    ),
                    heating_power_at_15c=power_data.get("heating_power_at_15c", 0.0),
                    heating_temp_coefficient=power_data.get(
                        "heating_temp_coefficient", 0.0
                    ),
                    temp_model_r2=power_data.get("temp_model_r2", 0.0),
                    temp_model_samples=power_data.get("temp_model_samples", 0),
                    temp_model_confidence=power_data.get("temp_model_confidence", "low"),
                )
            _LOGGER.info(
                "Loaded learned HVAC power for %d entities",
                len(self._learned_power),
            )

    async def _async_save_learned_power(self) -> None:
        """Persist learned HVAC power data to storage."""
        data = {
            "learned_power": {
                entity_id: {
                    # Legacy fields
                    "cooling_power_kw": power.cooling_power_kw,
                    "heating_power_kw": power.heating_power_kw,
                    "drying_power_kw": power.drying_power_kw,
                    "sample_count": power.sample_count,
                    "confidence": power.confidence,
                    # Temperature-correlated model fields (Issue #171)
                    "cooling_power_at_25c": power.cooling_power_at_25c,
                    "cooling_temp_coefficient": power.cooling_temp_coefficient,
                    "heating_power_at_15c": power.heating_power_at_15c,
                    "heating_temp_coefficient": power.heating_temp_coefficient,
                    "temp_model_r2": power.temp_model_r2,
                    "temp_model_samples": power.temp_model_samples,
                    "temp_model_confidence": power.temp_model_confidence,
                }
                for entity_id, power in self._learned_power.items()
            }
        }
        await self._store.async_save(data)

    # ------------------------------------------------------------------
    # Configuration Accessors
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """Check if thermal management is enabled."""
        return self._get_switch_state(
            CONF_THERMAL_MANAGEMENT_ENABLED
        ) or self._get_option(
            CONF_THERMAL_MANAGEMENT_ENABLED, DEFAULT_THERMAL_MANAGEMENT_ENABLED
        )

    def is_solar_taper_enabled(self) -> bool:
        """Check if solar tapering is enabled."""
        return self._get_switch_state(CONF_SOLAR_TAPER_ENABLED) or self._get_option(
            CONF_SOLAR_TAPER_ENABLED, DEFAULT_SOLAR_TAPER_ENABLED
        )

    def get_cooling_trigger_temp(self) -> float:
        """Get cooling trigger temperature threshold."""
        return self._get_option(CONF_COOLING_TRIGGER_TEMP, DEFAULT_COOLING_TRIGGER_TEMP)

    def get_heating_trigger_temp(self) -> float:
        """Get heating trigger temperature threshold."""
        return self._get_option(CONF_HEATING_TRIGGER_TEMP, DEFAULT_HEATING_TRIGGER_TEMP)

    def get_dehumidify_trigger_humidity(self) -> float:
        """Get dehumidify trigger humidity threshold (hardcoded - Issue #214)."""
        return DEFAULT_DEHUMIDIFY_TRIGGER_HUMIDITY

    def get_precondition_hours(self) -> float:
        """Get hours before DW to start pre-conditioning."""
        return self._get_option(
            CONF_PRECONDITION_HOURS_BEFORE_DW, DEFAULT_PRECONDITION_HOURS_BEFORE_DW
        )

    def get_precondition_temp_offset(self) -> float:
        """Get temperature offset for pre-conditioning."""
        return self._get_option(
            CONF_PRECONDITION_TEMP_OFFSET, DEFAULT_PRECONDITION_TEMP_OFFSET
        )

    def get_taper_max_offset(self) -> float:
        """Get max setpoint offset for solar tapering (hardcoded - Issue #214)."""
        return DEFAULT_TAPER_MAX_SETPOINT_OFFSET

    def get_thermal_hysteresis(self) -> float:
        """Get thermal hysteresis (deadband between on/off)."""
        return self._get_option(CONF_THERMAL_HYSTERESIS, DEFAULT_THERMAL_HYSTERESIS)

    def get_thermal_off_time(self) -> time:
        """Get earliest time to consider turning off AC."""
        time_str = self._get_option(CONF_THERMAL_OFF_TIME, DEFAULT_THERMAL_OFF_TIME)
        parts = time_str.split(":")
        return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)

    def get_thermal_off_temp_margin(self) -> float:
        """Get temperature margin for turning off."""
        return self._get_option(
            CONF_THERMAL_OFF_TEMP_MARGIN, DEFAULT_THERMAL_OFF_TEMP_MARGIN
        )

    def get_thermal_off_forecast_clear(self) -> bool:
        """Check if clear forecast is required for turn-off."""
        return self._get_option(
            CONF_THERMAL_OFF_FORECAST_CLEAR, DEFAULT_THERMAL_OFF_FORECAST_CLEAR
        )

    def get_min_setpoint_change_interval(self) -> int:
        """Get minimum minutes between setpoint changes."""
        return self._get_option(
            CONF_MIN_SETPOINT_CHANGE_INTERVAL, DEFAULT_MIN_SETPOINT_CHANGE_INTERVAL
        )

    def get_user_override_cooldown(self) -> int:
        """Get user override cooldown period in minutes."""
        return self._get_option(
            CONF_USER_OVERRIDE_COOLDOWN, DEFAULT_USER_OVERRIDE_COOLDOWN
        )

    # ------------------------------------------------------------------
    # User Override Detection
    # ------------------------------------------------------------------

    def _detect_user_override(
        self,
        data: CoordinatorData,
        now: datetime,
    ) -> bool:
        """Detect if user has manually changed HVAC settings.

        Compares current state to what we last applied. If different,
        sets override cooldown and returns True.

        Args:
            data: CoordinatorData with current climate states.
            now: Current datetime.

        Returns:
            True if user override detected and control should be suspended.
        """
        # Check if we're already in override cooldown
        if self._user_override_until is not None:
            if now < self._user_override_until:
                _LOGGER.debug(
                    "User override active, suspending thermal control (reason: %s)",
                    self._user_override_reason,
                )
                return True
            else:
                # Cooldown expired, clear override state
                _LOGGER.info("User override cooldown expired, resuming thermal control")
                self._user_override_until = None
                self._user_override_reason = ""
                # Reset original setpoints to current values when resuming
                self._original_setpoints.clear()
                self._last_applied_mode.clear()
                self._last_applied_setpoint.clear()
                return False

        # No active override - check if user changed settings
        control_entities = data.climate_control_entities
        if not control_entities:
            return False

        for entity_id in control_entities:
            state_dict = data.climate_states.get(entity_id, {})
            current_mode = state_dict.get("state", "off")  # "off", "cool", "heat"
            current_setpoint = state_dict.get("setpoint")

            # Get what we last applied
            last_mode = self._last_applied_mode.get(entity_id)
            last_setpoint = self._last_applied_setpoint.get(entity_id)

            # Skip if we haven't applied anything yet
            if last_mode is None:
                continue

            # Check for mode change
            if current_mode != last_mode:
                cooldown_minutes = self.get_user_override_cooldown()
                self._user_override_until = now + timedelta(minutes=cooldown_minutes)
                self._user_override_reason = (
                    f"Mode changed from {last_mode} to {current_mode} on {entity_id}"
                )
                _LOGGER.info(
                    "User override detected: %s. Suspending thermal control for %d minutes",
                    self._user_override_reason,
                    cooldown_minutes,
                )
                return True

            # Check for setpoint change (with 0.5°C tolerance for rounding)
            if last_setpoint is not None and current_setpoint is not None:
                setpoint_diff = abs(current_setpoint - last_setpoint)
                if setpoint_diff > 0.5:
                    # During DW ramp: adapt to user change instead of suspending
                    if self._in_dw_ramp:
                        _LOGGER.info(
                            "User adjusted setpoint during DW ramp: %.1f°C -> %.1f°C on %s. Adapting to new setpoint.",
                            last_setpoint,
                            current_setpoint,
                            entity_id,
                        )
                        # Update original setpoint to the new user value
                        self._original_setpoints[entity_id] = current_setpoint
                        self._last_applied_setpoint[entity_id] = current_setpoint
                        # Don't suspend control - continue with adapted baseline
                        continue

                    # Outside DW ramp: suspend control as before
                    cooldown_minutes = self.get_user_override_cooldown()
                    self._user_override_until = now + timedelta(minutes=cooldown_minutes)
                    self._user_override_reason = (
                        f"Setpoint changed from {last_setpoint:.1f}°C to {current_setpoint:.1f}°C on {entity_id}"
                    )
                    _LOGGER.info(
                        "User override detected: %s. Suspending thermal control for %d minutes",
                        self._user_override_reason,
                        cooldown_minutes,
                    )
                    return True

        return False

    def is_user_override_active(self) -> bool:
        """Check if user override is currently active.

        Returns:
            True if thermal control is suspended due to user override.
        """
        return self._user_override_until is not None

    # ------------------------------------------------------------------
    # Daily Mode Determination
    # ------------------------------------------------------------------

    def determine_daily_mode(
        self, temperature_forecast: dict[int, float], humidity: float | None = None
    ) -> ThermalMode:
        """Determine today's thermal mode from weather forecast.

        Priority: HEAT > COOL > DRY (per user requirement).

        Args:
            temperature_forecast: Hour -> temperature mapping from weather forecast.
            humidity: Current humidity percentage (optional).

        Returns:
            Determined ThermalMode for the day.
        """
        if not temperature_forecast:
            return ThermalMode.OFF

        # Calculate min/max temps from forecast
        temps = list(temperature_forecast.values())
        max_temp = max(temps)
        min_temp = min(temps)

        cooling_trigger = self.get_cooling_trigger_temp()
        heating_trigger = self.get_heating_trigger_temp()
        humidity_trigger = self.get_dehumidify_trigger_humidity()

        _LOGGER.debug(
            "Determining daily mode: min=%.1f°C, max=%.1f°C, humidity=%.1f%%, "
            "triggers: cool>%.1f, heat<%.1f, dry>%.1f%%",
            min_temp,
            max_temp,
            humidity or 0,
            cooling_trigger,
            heating_trigger,
            humidity_trigger,
        )

        # Priority: HEAT > COOL > DRY
        if min_temp < heating_trigger:
            mode = ThermalMode.HEAT
        elif max_temp > cooling_trigger:
            mode = ThermalMode.COOL
        elif humidity is not None and humidity > humidity_trigger:
            mode = ThermalMode.DRY
        else:
            mode = ThermalMode.OFF

        _LOGGER.info(
            "Daily thermal mode determined: %s (min=%.1f°C, max=%.1f°C)",
            mode.value,
            min_temp,
            max_temp,
        )
        return mode

    # ------------------------------------------------------------------
    # HVAC Power Learning
    # ------------------------------------------------------------------

    def learn_hvac_power(
        self,
        data: CoordinatorData,
        current_load_kw: float,
        timestamp: datetime,
    ) -> None:
        """Learn HVAC power consumption from state changes.

        When a climate entity's state changes, we observe the delta in load
        to estimate its power consumption.

        Args:
            data: CoordinatorData with current climate states.
            current_load_kw: Current total load in kW.
            timestamp: Current timestamp.
        """
        if not self.is_enabled():
            return

        if not data.climate_states:
            self._prev_load_kw = current_load_kw
            self._prev_load_timestamp = timestamp
            return

        # Check for state changes
        for entity_id, state_dict in data.climate_states.items():
            current_action = state_dict.get("hvac_action", "off")
            prev_state = self._prev_climate_states.get(entity_id)

            if prev_state is None:
                # First run - just record the state
                continue

            prev_action = prev_state.hvac_action

            # Detect state transitions
            if current_action != prev_action:
                self._process_state_change(
                    entity_id=entity_id,
                    prev_action=prev_action,
                    current_action=current_action,
                    prev_load_kw=self._prev_load_kw,
                    current_load_kw=current_load_kw,
                    timestamp=timestamp,
                )

        # Update previous state
        self._prev_load_kw = current_load_kw
        self._prev_load_timestamp = timestamp
        for entity_id, state_dict in data.climate_states.items():
            self._prev_climate_states[entity_id] = ClimateEntityState(
                entity_id=entity_id,
                state=state_dict.get("state", "off"),
                hvac_action=state_dict.get("hvac_action", "off"),
                setpoint=state_dict.get("setpoint", 0.0),
                current_temperature=state_dict.get("current_temperature"),
                is_controlled=state_dict.get("is_controlled", False),
                timestamp=timestamp,
            )

    def _process_state_change(
        self,
        entity_id: str,
        prev_action: str,
        current_action: str,
        prev_load_kw: float,
        current_load_kw: float,
        timestamp: datetime,
    ) -> None:
        """Process a state change and learn power consumption.

        Args:
            entity_id: Climate entity ID.
            prev_action: Previous HVAC action.
            current_action: Current HVAC action.
            prev_load_kw: Load before state change.
            current_load_kw: Load after state change.
            timestamp: Timestamp of the change.
        """
        # Calculate load delta
        load_delta = current_load_kw - prev_load_kw

        # Only learn if:
        # 1. Load increased (turned on)
        # 2. Load delta is reasonable (0.1 - 10 kW)
        if load_delta < 0.1 or load_delta > 10.0:
            _LOGGER.debug(
                "Ignoring load delta %.2f kW for %s (out of range)",
                load_delta,
                entity_id,
            )
            return

        # Get or create learned power entry
        if entity_id not in self._learned_power:
            self._learned_power[entity_id] = LearnedHVACPower(entity_id=entity_id)

        power = self._learned_power[entity_id]

        # Update based on action type
        if current_action == "cooling":
            power.cooling_power_kw = self._update_moving_average(
                power.cooling_power_kw, load_delta, power.sample_count
            )
            _LOGGER.info(
                "Learned cooling power for %s: %.2f kW (sample %d)",
                entity_id,
                power.cooling_power_kw,
                power.sample_count + 1,
            )
        elif current_action == "heating":
            power.heating_power_kw = self._update_moving_average(
                power.heating_power_kw, load_delta, power.sample_count
            )
            _LOGGER.info(
                "Learned heating power for %s: %.2f kW (sample %d)",
                entity_id,
                power.heating_power_kw,
                power.sample_count + 1,
            )
        elif current_action == "drying":
            power.drying_power_kw = self._update_moving_average(
                power.drying_power_kw, load_delta, power.sample_count
            )
            _LOGGER.info(
                "Learned drying power for %s: %.2f kW (sample %d)",
                entity_id,
                power.drying_power_kw,
                power.sample_count + 1,
            )

        power.sample_count += 1
        power.confidence = self._calculate_confidence(power.sample_count)

        # Save periodically (every 10 samples)
        if power.sample_count % 10 == 0:
            self.hass.async_create_task(
                self._async_save_learned_power(),
                "localshift_save_hvac_power",
            )

    def _update_moving_average(
        self, current_avg: float, new_value: float, sample_count: int
    ) -> float:
        """Update a moving average with a new value.

        Args:
            current_avg: Current average value.
            new_value: New value to incorporate.
            sample_count: Number of samples used for current average.

        Returns:
            Updated average.
        """
        if sample_count == 0:
            return new_value
        # Simple exponential moving average
        alpha = 0.3  # Weight for new value
        return current_avg * (1 - alpha) + new_value * alpha

    def _calculate_confidence(self, sample_count: int) -> str:
        """Calculate confidence level based on sample count.

        Args:
            sample_count: Number of learning samples.

        Returns:
            Confidence level string: "low", "medium", or "high".
        """
        if sample_count >= 20:
            return "high"
        elif sample_count >= 5:
            return "medium"
        return "low"

    # ------------------------------------------------------------------
    # Temperature-Correlated Power Learning (Issue #171)
    # ------------------------------------------------------------------

    def get_outdoor_temp_entity(self) -> str | None:
        """Get configured outdoor temperature entity.

        Returns:
            Entity ID for outdoor temperature sensor, or None.
        """
        return self._get_option(CONF_OUTDOOR_TEMP_ENTITY, "")

    def get_sample_interval(self) -> int:
        """Get HVAC sample interval in minutes."""
        return self._get_option(CONF_HVAC_SAMPLE_INTERVAL, DEFAULT_HVAC_SAMPLE_INTERVAL)

    def get_temp_model_min_samples(self) -> int:
        """Get minimum samples required for temperature model."""
        return self._get_option(
            CONF_TEMP_MODEL_MIN_SAMPLES, DEFAULT_TEMP_MODEL_MIN_SAMPLES
        )

    def get_outdoor_temperature(self) -> float | None:
        """Get current outdoor temperature from configured entity.

        Returns:
            Outdoor temperature in °C, or None if unavailable.
        """
        entity_id = self.get_outdoor_temp_entity()
        if not entity_id:
            return None

        state = self.hass.states.get(entity_id)
        if state is None:
            _LOGGER.debug("Outdoor temp entity %s not found", entity_id)
            return None

        try:
            return float(state.state)
        except (ValueError, TypeError):
            _LOGGER.debug("Invalid outdoor temp value from %s", entity_id)
            return None

    def sample_hvac_power_during_operation(
        self,
        data: CoordinatorData,
        current_load_kw: float,
        outdoor_temp: float | None,
        timestamp: datetime,
    ) -> None:
        """Sample HVAC power during continuous operation.

        Called periodically (every 5-10 min) while HVAC is running to collect
        temperature-correlated power data. This enables learning how power
        consumption varies with outdoor temperature.

        Args:
            data: CoordinatorData with current climate states.
            current_load_kw: Current total household load in kW.
            outdoor_temp: Current outdoor temperature in °C.
            timestamp: Current timestamp.
        """
        if not self.is_enabled():
            return

        if outdoor_temp is None:
            return

        if not data.climate_states:
            return

        # Estimate baseline load (non-HVAC consumption)
        baseline_kw = self._estimate_baseline_load(data, current_load_kw)

        # Sample each active HVAC entity
        for entity_id, state_dict in data.climate_states.items():
            hvac_action = state_dict.get("hvac_action", "off")

            if hvac_action not in ("cooling", "heating"):
                continue

            # Get or create learned power entry
            if entity_id not in self._learned_power:
                self._learned_power[entity_id] = LearnedHVACPower(entity_id=entity_id)

            power = self._learned_power[entity_id]

            # Estimate this unit's power contribution
            # For single AC: hvac_power = current_load - baseline
            hvac_power_kw = current_load_kw - baseline_kw

            # Validate reasonable range
            if not (0.1 < hvac_power_kw < 10.0):
                _LOGGER.debug(
                    "HVAC power estimate %.2f kW out of range for %s, skipping sample",
                    hvac_power_kw,
                    entity_id,
                )
                continue

            # Store sample with temperature
            if hvac_action == "cooling":
                power.cooling_samples.append((outdoor_temp, hvac_power_kw))
                # Keep last 200 samples (rolling window)
                if len(power.cooling_samples) > 200:
                    power.cooling_samples.pop(0)
                _LOGGER.debug(
                    "Sampled cooling power for %s: %.2f kW at %.1f°C (n=%d)",
                    entity_id,
                    hvac_power_kw,
                    outdoor_temp,
                    len(power.cooling_samples),
                )
            elif hvac_action == "heating":
                power.heating_samples.append((outdoor_temp, hvac_power_kw))
                if len(power.heating_samples) > 200:
                    power.heating_samples.pop(0)
                _LOGGER.debug(
                    "Sampled heating power for %s: %.2f kW at %.1f°C (n=%d)",
                    entity_id,
                    hvac_power_kw,
                    outdoor_temp,
                    len(power.heating_samples),
                )

        # Periodically fit temperature models
        self._maybe_fit_models()

    def _estimate_baseline_load(
        self, data: CoordinatorData, current_load_kw: float
    ) -> float:
        """Estimate baseline (non-HVAC) load from current state.

        Uses historical baseline if available, otherwise estimates from
        current load minus expected HVAC power.

        Args:
            data: CoordinatorData with current state.
            current_load_kw: Current total load in kW.

        Returns:
            Estimated baseline load in kW.
        """
        # Count active HVAC entities
        active_hvac_count = 0
        expected_hvac_power = 0.0

        for entity_id, state_dict in data.climate_states.items():
            hvac_action = state_dict.get("hvac_action", "off")
            if hvac_action in ("cooling", "heating"):
                active_hvac_count += 1
                # Use learned power if available
                if entity_id in self._learned_power:
                    power = self._learned_power[entity_id]
                    if hvac_action == "cooling" and power.cooling_power_kw > 0:
                        expected_hvac_power += power.cooling_power_kw
                    elif hvac_action == "heating" and power.heating_power_kw > 0:
                        expected_hvac_power += power.heating_power_kw

        # If no HVAC active, current load is baseline
        if active_hvac_count == 0:
            return current_load_kw

        # If we have expected HVAC power, subtract it
        if expected_hvac_power > 0:
            baseline = current_load_kw - expected_hvac_power
            return max(0.1, baseline)

        # Fallback: assume 0.5 kW baseline per non-HVAC
        # This is a rough estimate when no learned data available
        return max(0.1, current_load_kw * 0.2)

    def _maybe_fit_models(self) -> None:
        """Periodically fit temperature models if enough new samples."""
        min_samples = self.get_temp_model_min_samples()

        for power in self._learned_power.values():
            # Fit cooling model if enough new samples
            cooling_count = len(power.cooling_samples)
            if cooling_count >= min_samples and (
                power.temp_model_samples == 0
                or cooling_count >= power.temp_model_samples + 20
            ):
                self._fit_temperature_model(power, mode="cooling")

            # Fit heating model if enough new samples
            heating_count = len(power.heating_samples)
            if heating_count >= min_samples and (
                power.temp_model_samples == 0
                or heating_count >= power.temp_model_samples + 20
            ):
                self._fit_temperature_model(power, mode="heating")

    def _fit_temperature_model(
        self, power: LearnedHVACPower, mode: str
    ) -> None:
        """Fit temperature correlation model from samples.

        Uses simple linear regression to fit:
            power = base + coefficient × (temp - reference)

        For cooling: power = cooling_power_at_25c + cooling_temp_coefficient × (temp - 25)
        For heating: power = heating_power_at_15c + heating_temp_coefficient × (15 - temp)

        Args:
            power: LearnedHVACPower to update with fitted model.
            mode: "cooling" or "heating".
        """
        if mode == "cooling":
            samples = power.cooling_samples
            reference_temp = 25.0  # Reference temperature for cooling
        else:
            samples = power.heating_samples
            reference_temp = 15.0  # Reference temperature for heating

        if len(samples) < self.get_temp_model_min_samples():
            return

        # Extract temperatures and powers
        temps = [s[0] for s in samples]
        powers = [s[1] for s in samples]

        # Simple linear regression: y = a + bx
        n = len(temps)
        sum_x = sum(temps)
        sum_y = sum(powers)
        sum_xy = sum(t * p for t, p in samples)
        sum_x2 = sum(t * t for t in temps)

        # Calculate slope and intercept
        denominator = n * sum_x2 - sum_x * sum_x
        if denominator == 0:
            _LOGGER.warning(
                "Cannot fit temperature model for %s: all samples at same temperature",
                power.entity_id,
            )
            return

        slope = (n * sum_xy - sum_x * sum_y) / denominator
        intercept = (sum_y - slope * sum_x) / n

        # Calculate R² for model quality
        y_mean = sum_y / n
        ss_tot = sum((p - y_mean) ** 2 for p in powers)
        ss_res = sum((p - (intercept + slope * t)) ** 2 for t, p in samples)
        r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        # Store model parameters
        if mode == "cooling":
            # power_at_25c = intercept + slope × 25
            power.cooling_power_at_25c = intercept + slope * reference_temp
            power.cooling_temp_coefficient = slope
            _LOGGER.info(
                "Fitted cooling model for %s: power=%.2f + %.3f×(T-25), R²=%.2f, n=%d",
                power.entity_id,
                power.cooling_power_at_25c,
                power.cooling_temp_coefficient,
                r2,
                n,
            )
        else:
            # For heating, coefficient is positive when power increases as temp drops
            power.heating_power_at_15c = intercept + slope * reference_temp
            power.heating_temp_coefficient = -slope  # Negative because temp delta is inverted
            _LOGGER.info(
                "Fitted heating model for %s: power=%.2f + %.3f×(15-T), R²=%.2f, n=%d",
                power.entity_id,
                power.heating_power_at_15c,
                power.heating_temp_coefficient,
                r2,
                n,
            )

        # Update model quality metrics
        power.temp_model_r2 = r2
        power.temp_model_samples = n
        power.temp_model_confidence = self._calculate_temp_model_confidence(n, r2)

        # Save the updated model
        self.hass.async_create_task(
            self._async_save_learned_power(),
            "localshift_save_hvac_temp_model",
        )

    def _calculate_temp_model_confidence(self, sample_count: int, r2: float) -> str:
        """Calculate confidence level for temperature model.

        Args:
            sample_count: Number of samples used for model.
            r2: R² value of the fitted model.

        Returns:
            Confidence level string: "low", "medium", or "high".
        """
        # Need both sufficient samples AND good correlation
        if sample_count >= 50 and r2 >= 0.7:
            return "high"
        elif sample_count >= 20 and r2 >= 0.5:
            return "medium"
        return "low"

    def predict_hvac_load(
        self,
        hour: int,
        temperature: float,
        humidity: float | None = None,
        daily_mode: ThermalMode = ThermalMode.OFF,
    ) -> float:
        """Predict HVAC load for given hour based on weather and learned power.

        Uses temperature-correlated model when available (Issue #171), falling
        back to fixed power values when the model is not ready.

        Args:
            hour: Hour of day (0-23).
            temperature: Forecasted outdoor temperature in °C.
            humidity: Current humidity percentage (optional).
            daily_mode: Current daily thermal mode.

        Returns:
            Predicted HVAC load in kW.
        """
        if daily_mode == ThermalMode.OFF:
            return 0.0

        total_power = 0.0
        entity_count = 0

        for _entity_id, power in self._learned_power.items():
            if daily_mode == ThermalMode.COOL:
                # Prefer temperature-correlated model if available and confident
                if (
                    power.cooling_power_at_25c > 0
                    and power.temp_model_r2 >= 0.5
                    and power.temp_model_confidence != "low"
                ):
                    # Use temperature-correlated model
                    temp_delta = temperature - 25.0  # Reference is 25°C
                    predicted = power.cooling_power_at_25c + (
                        power.cooling_temp_coefficient * temp_delta
                    )
                    # Clamp to reasonable range
                    predicted = max(0.5, min(6.0, predicted))
                    total_power += predicted
                    entity_count += 1
                    _LOGGER.debug(
                        "Temp-correlated prediction for %s: %.2f kW at %.1f°C (model: %.2f + %.3f×ΔT)",
                        power.entity_id,
                        predicted,
                        temperature,
                        power.cooling_power_at_25c,
                        power.cooling_temp_coefficient,
                    )
                elif power.cooling_power_kw > 0 and power.confidence != "low":
                    # Fallback to fixed value
                    total_power += power.cooling_power_kw
                    entity_count += 1

            elif daily_mode == ThermalMode.HEAT:
                # Prefer temperature-correlated model if available and confident
                if (
                    power.heating_power_at_15c > 0
                    and power.temp_model_r2 >= 0.5
                    and power.temp_model_confidence != "low"
                ):
                    # Use temperature-correlated model
                    temp_delta = 15.0 - temperature  # Reference is 15°C
                    predicted = power.heating_power_at_15c + (
                        power.heating_temp_coefficient * temp_delta
                    )
                    predicted = max(0.5, min(5.0, predicted))
                    total_power += predicted
                    entity_count += 1
                    _LOGGER.debug(
                        "Temp-correlated prediction for %s: %.2f kW at %.1f°C (model: %.2f + %.3f×ΔT)",
                        power.entity_id,
                        predicted,
                        temperature,
                        power.heating_power_at_15c,
                        power.heating_temp_coefficient,
                    )
                elif power.heating_power_kw > 0 and power.confidence != "low":
                    # Fallback to fixed value
                    total_power += power.heating_power_kw
                    entity_count += 1

            elif daily_mode == ThermalMode.DRY and power.drying_power_kw > 0:
                if power.confidence != "low":
                    total_power += power.drying_power_kw
                    entity_count += 1

        if entity_count == 0:
            # No learned data - use heuristic estimate
            return self._heuristic_hvac_estimate(temperature, daily_mode)

        return total_power

    def _heuristic_hvac_estimate(
        self, temperature: float, daily_mode: ThermalMode
    ) -> float:
        """Estimate HVAC load using heuristics when no learned data available.

        Args:
            temperature: Forecasted outdoor temperature in °C.
            daily_mode: Current daily thermal mode.

        Returns:
            Estimated HVAC load in kW.
        """
        if daily_mode == ThermalMode.COOL:
            # Estimate based on temperature differential
            cooling_trigger = self.get_cooling_trigger_temp()
            temp_diff = max(0, temperature - cooling_trigger)
            return min(4.0, temp_diff * 0.5)  # ~0.5 kW per degree over trigger
        elif daily_mode == ThermalMode.HEAT:
            heating_trigger = self.get_heating_trigger_temp()
            temp_diff = max(0, heating_trigger - temperature)
            return min(3.5, temp_diff * 0.35)  # ~0.35 kW per degree under trigger
        return 0.0

    def get_learned_power_summary(self) -> dict[str, dict[str, Any]]:
        """Get summary of learned HVAC power for all entities.

        Returns:
            Dict of entity_id -> power summary dict.
        """
        return {
            entity_id: {
                # Legacy fields
                "cooling_power_kw": power.cooling_power_kw,
                "heating_power_kw": power.heating_power_kw,
                "drying_power_kw": power.drying_power_kw,
                "sample_count": power.sample_count,
                "confidence": power.confidence,
                # Temperature-correlated model fields (Issue #171)
                "cooling_power_at_25c": power.cooling_power_at_25c,
                "cooling_temp_coefficient": power.cooling_temp_coefficient,
                "heating_power_at_15c": power.heating_power_at_15c,
                "heating_temp_coefficient": power.heating_temp_coefficient,
                "temp_model_r2": power.temp_model_r2,
                "temp_model_samples": power.temp_model_samples,
                "temp_model_confidence": power.temp_model_confidence,
                "cooling_samples_count": len(power.cooling_samples),
                "heating_samples_count": len(power.heating_samples),
            }
            for entity_id, power in self._learned_power.items()
        }

    def estimate_baseline_from_historical(
        self,
        historical_avg_kw: dict[int, float],
        daily_mode: ThermalMode | None = None,
    ) -> dict[int, float]:
        """Estimate baseline load by subtracting learned HVAC power from historical averages.

        This is the key method for solving Issue #137. It estimates the non-HVAC
        (baseline) consumption by subtracting the learned HVAC power from the
        historical average load.

        The historical average includes HVAC spikes. By subtracting the learned
        HVAC power, we get an estimate of what load would be WITHOUT HVAC.

        This baseline is then used for grid charging decisions, preventing the
        feedback loop where:
        1. HVAC turns on → load increases
        2. System forecasts higher consumption using historical (with HVAC spikes)
        3. System triggers grid charging unnecessarily
        4. Energy wasted instead of using solar surplus

        Args:
            historical_avg_kw: Historical hourly average load (includes HVAC).
            daily_mode: Current daily thermal mode. If None, uses COOL as default
                       since cooling is the most common HVAC load in summer.

        Returns:
            Dict of hour -> estimated baseline load in kW (non-HVAC).
        """
        if not historical_avg_kw:
            return {}

        # Determine which power value to subtract based on mode
        if daily_mode is None:
            daily_mode = ThermalMode.COOL  # Default assumption

        # Sum learned HVAC power for the active mode
        total_hvac_power_kw = 0.0
        valid_entities = 0

        for _entity_id, power in self._learned_power.items():
            # Only use medium/high confidence learning
            if power.confidence == "low":
                continue

            if daily_mode == ThermalMode.COOL and power.cooling_power_kw > 0:
                total_hvac_power_kw += power.cooling_power_kw
                valid_entities += 1
            elif daily_mode == ThermalMode.HEAT and power.heating_power_kw > 0:
                total_hvac_power_kw += power.heating_power_kw
                valid_entities += 1
            elif daily_mode == ThermalMode.DRY and power.drying_power_kw > 0:
                total_hvac_power_kw += power.drying_power_kw
                valid_entities += 1

        # If no learned power, use a default estimate based on typical AC
        if valid_entities == 0:
            # Typical split system AC: 2-4 kW
            # Use estimate of 3.5 kW (observed ~4 kW in user's system)
            total_hvac_power_kw = 3.5
            _LOGGER.debug(
                "No learned HVAC power, using default estimate: %.1f kW",
                total_hvac_power_kw,
            )

        # Estimate duty cycle based on historical patterns
        # HVAC typically runs 30-50% of the time during active cooling/heating
        # Use 40% as a reasonable default
        duty_cycle = 0.4

        # Calculate effective HVAC contribution to historical average
        effective_hvac_kw = total_hvac_power_kw * duty_cycle

        _LOGGER.info(
            "Baseline estimation: mode=%s, learned_hvac=%.2f kW, duty_cycle=%.0f%%, effective=%.2f kW",
            daily_mode.value,
            total_hvac_power_kw,
            duty_cycle * 100,
            effective_hvac_kw,
        )

        # Subtract HVAC contribution from historical averages
        baseline: dict[int, float] = {}
        for hour, avg_kw in historical_avg_kw.items():
            # Estimate baseline by subtracting HVAC
            baseline[hour] = max(0.0, avg_kw - effective_hvac_kw)

        return baseline

    # ------------------------------------------------------------------
    # Pre-conditioning Evaluation
    # ------------------------------------------------------------------

    def evaluate_preconditioning(
        self,
        data: CoordinatorData,
        now: datetime,
        demand_window_start: time,
        demand_window_end: time,  # pylint: disable=unused-argument
    ) -> tuple[bool, float]:
        """Determine if pre-conditioning should be active.

        Args:
            data: CoordinatorData with current state.
            now: Current datetime.
            demand_window_start: Demand window start time.
            demand_window_end: Demand window end time (unused, for API consistency).

        Returns:
            Tuple of (is_active, setpoint_offset).
        """
        if not self.is_enabled():
            return False, 0.0

        # Check for user override - suspend control if user manually changed settings
        if self._user_override_until is not None and now < self._user_override_until:
            _LOGGER.debug(
                "Pre-conditioning suspended due to user override: %s",
                self._user_override_reason,
            )
            return False, 0.0

        daily_mode = data.daily_thermal_mode
        if daily_mode not in (ThermalMode.HEAT, ThermalMode.COOL):
            return False, 0.0

        # Check if we're in the pre-conditioning window
        precondition_hours = self.get_precondition_hours()
        precondition_start = datetime.combine(
            now.date(), demand_window_start
        ) - timedelta(hours=precondition_hours)
        dw_start_dt = datetime.combine(now.date(), demand_window_start)

        if not (precondition_start <= now < dw_start_dt):
            return False, 0.0

        # Calculate setpoint offset based on mode
        temp_offset = self.get_precondition_temp_offset()

        if daily_mode == ThermalMode.COOL:
            # Pre-cool: lower setpoint
            setpoint_offset = -temp_offset
        elif daily_mode == ThermalMode.HEAT:
            # Pre-heat: raise setpoint
            setpoint_offset = temp_offset
        else:
            setpoint_offset = 0.0

        _LOGGER.info(
            "Pre-conditioning active: mode=%s, offset=%.1f°C",
            daily_mode.value,
            setpoint_offset,
        )
        return True, setpoint_offset

    # ------------------------------------------------------------------
    # Solar Tapering Evaluation
    # ------------------------------------------------------------------

    def evaluate_solar_taper(
        self,
        data: CoordinatorData,
        excess_solar_kw: float,
        load_shift_signal: str,
    ) -> tuple[bool, float]:
        """Determine solar tapering status and setpoint offset.

        Args:
            data: CoordinatorData with current state.
            excess_solar_kw: Current excess solar generation in kW.
            load_shift_signal: Current load shift signal (INCREASE_LOAD, etc.).

        Returns:
            Tuple of (is_active, setpoint_offset).
        """
        if not self.is_enabled() or not self.is_solar_taper_enabled():
            return False, 0.0

        # Check for user override - suspend control if user manually changed settings
        now = datetime.now()
        if self._user_override_until is not None and now < self._user_override_until:
            _LOGGER.debug(
                "Solar taper suspended due to user override: %s",
                self._user_override_reason,
            )
            return False, 0.0

        daily_mode = data.daily_thermal_mode
        if daily_mode not in (ThermalMode.HEAT, ThermalMode.COOL):
            return False, 0.0

        # Only taper when there's excess solar and INCREASE_LOAD signal
        if excess_solar_kw < 0.5 or load_shift_signal != "INCREASE_LOAD":
            return False, 0.0

        # Calculate setpoint offset based on excess
        max_offset = self.get_taper_max_offset()

        # Scale offset with excess (2kW excess -> full offset)
        scale = min(1.0, excess_solar_kw / 2.0)
        raw_offset = max_offset * scale

        # Apply direction based on mode
        if daily_mode == ThermalMode.COOL:
            # More cooling to consume excess
            setpoint_offset = -raw_offset
        elif daily_mode == ThermalMode.HEAT:
            # More heating to consume excess
            setpoint_offset = raw_offset
        else:
            setpoint_offset = 0.0

        _LOGGER.debug(
            "Solar taper: excess=%.2f kW, offset=%.1f°C",
            excess_solar_kw,
            setpoint_offset,
        )
        return True, setpoint_offset

    # ------------------------------------------------------------------
    # Climate Control
    # ------------------------------------------------------------------

    def _capture_original_setpoints(self, data: CoordinatorData) -> None:
        """Capture original setpoints for controlled entities if not already tracked.

        This is called when thermal control starts to establish a baseline
        for offset calculations, preventing the "ratchet bug" where offsets
        accumulate on each tick.

        Args:
            data: CoordinatorData with current climate states.
        """
        control_entities = data.climate_control_entities
        if not control_entities:
            return

        for entity_id in control_entities:
            # Only capture if not already tracking
            if entity_id not in self._original_setpoints:
                state = data.climate_states.get(entity_id, {})
                current_setpoint = state.get("setpoint")
                if current_setpoint is not None:
                    self._original_setpoints[entity_id] = current_setpoint
                    _LOGGER.debug(
                        "Captured original setpoint for %s: %.1f°C",
                        entity_id,
                        current_setpoint,
                    )

    def _clear_original_setpoints(self) -> None:
        """Clear tracked original setpoints when thermal control stops.

        Called when offset returns to 0 or thermal management is disabled.
        """
        if self._original_setpoints:
            _LOGGER.debug(
                "Clearing original setpoints for %d entities",
                len(self._original_setpoints),
            )
        self._original_setpoints.clear()
        self._current_active_offset = 0.0

    async def async_apply_climate_control(
        self,
        data: CoordinatorData,
        setpoint_offset: float,
    ) -> None:
        """Apply setpoint adjustments to controlled climate entities.

        This method tracks original setpoints to prevent the "ratchet bug"
        where offsets would accumulate on each tick. Instead, it calculates
        the new setpoint from the original baseline.

        Also controls HVAC mode:
        - When activating (offset != 0): Turns AC on in cool/heat mode
        - When deactivating (offset == 0): Turns AC off

        Args:
            data: CoordinatorData with current state.
            setpoint_offset: Setpoint adjustment in °C (positive = higher temp).
        """
        if not self.is_enabled():
            self._clear_original_setpoints()
            return

        control_entities = data.climate_control_entities
        if not control_entities:
            self._clear_original_setpoints()
            return

        # Determine HVAC mode from daily thermal mode
        daily_mode = data.daily_thermal_mode
        if daily_mode == ThermalMode.COOL:
            hvac_mode = "cool"
        elif daily_mode == ThermalMode.HEAT:
            hvac_mode = "heat"
        else:
            # For DRY/OFF modes, don't control HVAC
            _LOGGER.debug(
                "Skipping climate control for mode: %s",
                daily_mode.value,
            )
            return

        # If offset is 0, turn off AC and restore original setpoints
        if setpoint_offset == 0.0:
            if self._current_active_offset != 0.0:
                # We had an active offset, now need to turn off and restore
                for entity_id in control_entities:
                    # Turn off AC
                    try:
                        await self.hass.services.async_call(
                            "climate",
                            "set_hvac_mode",
                            {
                                "entity_id": entity_id,
                                "hvac_mode": "off",
                            },
                            blocking=False,
                        )
                        _LOGGER.info(
                            "Turned off %s (thermal control deactivated)",
                            entity_id,
                        )
                    except Exception as err:
                        _LOGGER.warning(
                            "Failed to turn off %s: %s", entity_id, err
                        )

                    # Restore original setpoint
                    if entity_id in self._original_setpoints:
                        original = self._original_setpoints[entity_id]
                        try:
                            await self.hass.services.async_call(
                                "climate",
                                "set_temperature",
                                {
                                    "entity_id": entity_id,
                                    "temperature": original,
                                },
                                blocking=False,
                            )
                            _LOGGER.info(
                                "Restored %s setpoint to original: %.1f°C",
                                entity_id,
                                original,
                            )
                        except Exception as err:
                            _LOGGER.warning(
                                "Failed to restore %s setpoint: %s", entity_id, err
                            )
            self._clear_original_setpoints()
            return

        # Note: We intentionally do NOT skip when offset is the same as before.
        # The user may have manually changed the setpoint, or the AC may have been
        # on with a different setpoint. We always re-apply to ensure correctness.
        # The set_temperature call is idempotent, so repeated calls are harmless.

        # Capture original setpoints if this is a new control session
        if not self._original_setpoints:
            self._capture_original_setpoints(data)

        for entity_id in control_entities:
            # Get the original setpoint (user's configured value)
            original_setpoint = self._original_setpoints.get(entity_id)

            if original_setpoint is None:
                # Fallback to current setpoint if original not tracked
                state = data.climate_states.get(entity_id, {})
                original_setpoint = state.get("setpoint", 22.0)
                self._original_setpoints[entity_id] = original_setpoint

            # Calculate new setpoint from ORIGINAL, not current
            new_setpoint = original_setpoint + setpoint_offset

            # Clamp to reasonable range
            new_setpoint = max(16.0, min(30.0, new_setpoint))

            # Turn on AC with correct HVAC mode (blocking to ensure mode is set before temperature)
            try:
                await self.hass.services.async_call(
                    "climate",
                    "set_hvac_mode",
                    {
                        "entity_id": entity_id,
                        "hvac_mode": hvac_mode,
                    },
                    blocking=True,
                )
                _LOGGER.info(
                    "Turned on %s in %s mode (thermal control activated)",
                    entity_id,
                    hvac_mode,
                )
            except Exception as err:
                _LOGGER.warning("Failed to turn on %s: %s", entity_id, err)

            # Apply setpoint via climate.set_temperature service
            try:
                await self.hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {
                        "entity_id": entity_id,
                        "temperature": new_setpoint,
                    },
                    blocking=False,
                )
                _LOGGER.info(
                    "Adjusted %s setpoint: %.1f°C (original) -> %.1f°C (offset %.1f°C)",
                    entity_id,
                    original_setpoint,
                    new_setpoint,
                    setpoint_offset,
                )
            except Exception as err:
                _LOGGER.warning("Failed to adjust %s setpoint: %s", entity_id, err)

        # Track what we applied for user override detection
        for entity_id in control_entities:
            self._last_applied_mode[entity_id] = hvac_mode
            self._last_applied_setpoint[entity_id] = new_setpoint

        # Track the current active offset
        self._current_active_offset = setpoint_offset

    # ------------------------------------------------------------------
    # Real-time Thermal Control
    # ------------------------------------------------------------------

    def calculate_average_room_temp(self, data: CoordinatorData) -> float | None:
        """Calculate average room temperature from climate entities.

        Uses current_temperature from all available climate entities.
        Updates trend tracking for turn-off decisions.

        Args:
            data: CoordinatorData with current climate states.

        Returns:
            Average temperature in °C, or None if no readings available.
        """
        temps: list[float] = []
        entities_with_temp = []
        entities_without_temp = []

        for entity_id, state_dict in data.climate_states.items():
            current_temp = state_dict.get("current_temperature")
            if current_temp is not None and current_temp > 0:
                temps.append(current_temp)
                entities_with_temp.append(entity_id)
            else:
                entities_without_temp.append(entity_id)

        if not temps:
            _LOGGER.info(
                "No room temperature readings available from %d climate entities. "
                "Entities without temp: %s",
                len(data.climate_states),
                entities_without_temp if entities_without_temp else "none",
            )
            return None

        avg_temp = sum(temps) / len(temps)
        self._last_avg_room_temp = avg_temp

        # Track recent temps for trend detection (keep last 6 readings ~30 min)
        self._recent_room_temps.append(avg_temp)
        if len(self._recent_room_temps) > 6:
            self._recent_room_temps.pop(0)

        _LOGGER.info(
            "Average room temperature: %.1f°C from %d entities (%s)",
            avg_temp,
            len(temps),
            ", ".join(entities_with_temp),
        )

        return avg_temp

    def _is_rate_limited(self, now: datetime) -> bool:
        """Check if setpoint change is rate-limited.

        Args:
            now: Current datetime.

        Returns:
            True if change should be skipped due to rate limiting.
        """
        min_interval = self.get_min_setpoint_change_interval()
        if min_interval <= 0:
            return False

        if self._last_setpoint_change is None:
            return False

        elapsed = (now - self._last_setpoint_change).total_seconds() / 60
        return elapsed < min_interval

    def _should_turn_on(
        self,
        avg_room_temp: float,
        daily_mode: ThermalMode,
    ) -> bool:
        """Determine if thermal control should activate.

        For initial activation: use trigger temp directly (no hysteresis).
        Hysteresis is only used for turn-off decisions to prevent cycling.

        This allows the AC to turn on when room meets the trigger temp,
        rather than waiting for trigger + hysteresis.

        Args:
            avg_room_temp: Current average room temperature.
            daily_mode: Current daily thermal mode.

        Returns:
            True if thermal control should activate.
        """
        if daily_mode == ThermalMode.OFF:
            return False

        if daily_mode == ThermalMode.COOL:
            trigger = self.get_cooling_trigger_temp()
            # Turn on when room exceeds trigger (no hysteresis for activation)
            should_on = avg_room_temp > trigger
            if should_on:
                _LOGGER.debug(
                    "Turn-on check (COOL): room=%.1f°C > trigger %.1f°C = %s",
                    avg_room_temp,
                    trigger,
                    should_on,
                )
            return should_on

        elif daily_mode == ThermalMode.HEAT:
            trigger = self.get_heating_trigger_temp()
            # Turn on when room is below trigger (no hysteresis for activation)
            should_on = avg_room_temp < trigger
            if should_on:
                _LOGGER.debug(
                    "Turn-on check (HEAT): room=%.1f°C < trigger %.1f°C = %s",
                    avg_room_temp,
                    trigger,
                    should_on,
                )
            return should_on

        return False

    def _should_turn_off(
        self,
        avg_room_temp: float,
        daily_mode: ThermalMode,
        now: datetime,
        temperature_forecast: dict[int, float] | None,
    ) -> tuple[bool, str]:
        """Determine if thermal control should deactivate.

        Conservative multi-condition turn-off:
        1. Time check: Must be after configured off_time
        2. Temperature check: Room must be beyond trigger with margin
        3. Trend check (optional): Temperature trend should be favorable
        4. Forecast check (optional): No upcoming temperature spikes

        Args:
            avg_room_temp: Current average room temperature.
            daily_mode: Current daily thermal mode.
            now: Current datetime.
            temperature_forecast: Hourly temperature forecast.

        Returns:
            Tuple of (should_turn_off, reason).
        """
        if daily_mode == ThermalMode.OFF:
            return False, ""

        # Condition 1: Time check
        off_time = self.get_thermal_off_time()
        current_time = now.time()

        if current_time < off_time:
            return False, f"Before off_time ({off_time})"

        margin = self.get_thermal_off_temp_margin()

        if daily_mode == ThermalMode.COOL:
            trigger = self.get_cooling_trigger_temp()
            # Room must be below trigger - margin
            target_temp = trigger - margin
            temp_ok = avg_room_temp < target_temp

            if not temp_ok:
                return (
                    False,
                    f"Room still warm ({avg_room_temp:.1f}°C >= {target_temp:.1f}°C)",
                )

            # Condition 4: Forecast check (optional)
            if self.get_thermal_off_forecast_clear() and temperature_forecast:
                # Check next 3 hours for temperature spikes
                current_hour = now.hour
                for h in range(current_hour, min(current_hour + 3, 24)):
                    if h in temperature_forecast:
                        forecast_temp = temperature_forecast[h]
                        if forecast_temp > trigger:
                            return (
                                False,
                                f"Forecast spike at {h}:00 ({forecast_temp:.1f}°C)",
                            )

            return (
                True,
                f"Room cool enough ({avg_room_temp:.1f}°C < {target_temp:.1f}°C)",
            )

        elif daily_mode == ThermalMode.HEAT:
            trigger = self.get_heating_trigger_temp()
            # Room must be above trigger + margin
            target_temp = trigger + margin
            temp_ok = avg_room_temp > target_temp

            if not temp_ok:
                return (
                    False,
                    f"Room still cool ({avg_room_temp:.1f}°C <= {target_temp:.1f}°C)",
                )

            # Condition 4: Forecast check (optional)
            if self.get_thermal_off_forecast_clear() and temperature_forecast:
                current_hour = now.hour
                for h in range(current_hour, min(current_hour + 3, 24)):
                    if h in temperature_forecast:
                        forecast_temp = temperature_forecast[h]
                        if forecast_temp < trigger:
                            return (
                                False,
                                f"Forecast cold at {h}:00 ({forecast_temp:.1f}°C)",
                            )

            return (
                True,
                f"Room warm enough ({avg_room_temp:.1f}°C > {target_temp:.1f}°C)",
            )

        return False, "Unknown mode"

    def evaluate_realtime_thermal(
        self,
        data: CoordinatorData,
        now: datetime,
        demand_window_start: time,
        demand_window_end: time,
        excess_solar_kw: float,
        load_shift_signal: str,
        temperature_forecast: dict[int, float] | None = None,
    ) -> tuple[bool, float, str]:
        """Main real-time thermal evaluation method.

        Determines if and how thermal control should be active, considering:
        1. User override detection (highest priority - suspends control)
        2. Pre-conditioning before demand window (second priority)
        3. Solar tapering for excess solar (third priority)
        4. Real-time on/off control (base layer)

        The layers stack: if pre-conditioning is active, its offset is used.
        Otherwise, solar taper offset. Otherwise, real-time control.

        Args:
            data: CoordinatorData with current state.
            now: Current datetime.
            demand_window_start: Demand window start time.
            demand_window_end: Demand window end time.
            excess_solar_kw: Current excess solar generation.
            load_shift_signal: Current load shift signal.
            temperature_forecast: Hourly temperature forecast.

        Returns:
            Tuple of (is_active, setpoint_offset, reason).
        """
        if not self.is_enabled():
            return False, 0.0, "Thermal management disabled"

        daily_mode = data.daily_thermal_mode
        if daily_mode not in (ThermalMode.HEAT, ThermalMode.COOL):
            return False, 0.0, f"Mode not applicable: {daily_mode.value}"

        # Check for user override (suspends all thermal control)
        if self._detect_user_override(data, now):
            return False, 0.0, f"User override: {self._user_override_reason}"

        # Always calculate average room temperature (needed for all layers and diagnostics)
        avg_room_temp = self.calculate_average_room_temp(data)

        # Layer 1: Check pre-conditioning (highest priority)
        precon_active, precon_offset = self.evaluate_preconditioning(
            data, now, demand_window_start, demand_window_end
        )
        if precon_active:
            self._thermal_activated_today = True
            if self._thermal_activated_at is None:
                self._thermal_activated_at = now
            return True, precon_offset, "Pre-conditioning active"

        # Layer 2: Check solar tapering (second priority)
        taper_active, taper_offset = self.evaluate_solar_taper(
            data, excess_solar_kw, load_shift_signal
        )
        if taper_active:
            self._thermal_activated_today = True
            if self._thermal_activated_at is None:
                self._thermal_activated_at = now
            return True, taper_offset, f"Solar taper ({excess_solar_kw:.1f}kW excess)"

        # Layer 3: Real-time control (base layer)
        if avg_room_temp is None:
            return False, 0.0, "No room temperature reading"

        # Check rate limiting
        if self._is_rate_limited(now):
            # Return current state without change
            if self._thermal_activated_today:
                return True, 0.0, "Rate limited (keeping current)"
            return False, 0.0, "Rate limited"

        # Check if we're in the demand window (for ramp calculation)
        dw_start_dt = datetime.combine(now.date(), demand_window_start)
        dw_end_dt = datetime.combine(now.date(), demand_window_end)
        in_demand_window = dw_start_dt <= now < dw_end_dt

        # Determine if we should turn on or off
        if not self._thermal_activated_today:
            # Not yet activated today - check if we should turn on
            if self._should_turn_on(avg_room_temp, daily_mode):
                self._thermal_activated_today = True
                self._thermal_activated_at = now
                self._last_setpoint_change = now

                # Real-time activation: use 0 offset (just turn on at user's setpoint)
                # Offset only applies during pre-conditioning or solar taper
                return True, 0.0, f"Activated: room {avg_room_temp:.1f}°C"
            else:
                return False, 0.0, f"Waiting to activate (room: {avg_room_temp:.1f}°C)"
        else:
            # Already activated - check if we should turn off
            should_off, reason = self._should_turn_off(
                avg_room_temp, daily_mode, now, temperature_forecast
            )
            if should_off:
                self._last_setpoint_change = now
                return False, 0.0, f"Deactivated: {reason}"
            else:
                # Check if in demand window - apply ramp
                if in_demand_window:
                    # Set ramp mode flag for adaptive user override handling
                    self._in_dw_ramp = True

                    # Calculate DW ramp offset (adaptive to conditions)
                    ramp_offset = self._calculate_dw_ramp_offset(
                        now=now,
                        dw_start=dw_start_dt,
                        dw_end=dw_end_dt,
                        avg_room_temp=avg_room_temp,
                        daily_mode=daily_mode,
                        battery_soc=data.battery_soc,
                    )
                    if daily_mode == ThermalMode.COOL:
                        # Positive offset = warmer setpoint = less cooling
                        return True, ramp_offset, f"DW ramp: {reason}"
                    else:
                        # Negative offset = cooler setpoint = less heating
                        return True, -ramp_offset, f"DW ramp: {reason}"
                else:
                    # Clear ramp mode flag when outside DW
                    self._in_dw_ramp = False

                    # Outside DW - maintain current state with 0 offset
                    return True, 0.0, f"Active: {reason}"

    def _calculate_dw_ramp_offset(
        self,
        now: datetime,
        dw_start: datetime,
        dw_end: datetime,
        avg_room_temp: float,
        daily_mode: ThermalMode,
        battery_soc: float,
    ) -> float:
        """Calculate setpoint offset for demand window ramp.

        Gradually increases setpoint during the demand window to conserve
        battery while maintaining comfort. The ramp is adaptive to:
        - Battery SOC: Low SOC = faster ramp (conserve more)
        - Room temp: Already warm = slower ramp (comfort)
        - Progress through DW: Linear increase

        Args:
            now: Current datetime.
            dw_start: Demand window start datetime.
            dw_end: Demand window end datetime.
            avg_room_temp: Current average room temperature.
            daily_mode: Current daily thermal mode.
            battery_soc: Current battery state of charge (%).

        Returns:
            Setpoint offset in °C (positive = warmer = less cooling).
        """
        # Default max ramp offset (can be made configurable later)
        max_offset = 3.0  # °C

        # Calculate progress through DW (0.0 to 1.0)
        dw_duration_seconds = (dw_end - dw_start).total_seconds()
        if dw_duration_seconds <= 0:
            return 0.0

        elapsed_seconds = (now - dw_start).total_seconds()
        progress = max(0.0, min(1.0, elapsed_seconds / dw_duration_seconds))

        # Base ramp: linear increase through DW
        base_offset = max_offset * progress

        # Adjustments based on conditions
        adjustments = 0.0

        # 1. Battery SOC adjustment: low SOC = ramp faster (conserve more)
        if battery_soc < 30:
            adjustments += 0.5  # Add 0.5°C when battery low
            _LOGGER.debug(
                "DW ramp: low SOC (%.0f%%), adding +0.5°C offset",
                battery_soc,
            )
        elif battery_soc < 50:
            adjustments += 0.25
            _LOGGER.debug(
                "DW ramp: moderate SOC (%.0f%%), adding +0.25°C offset",
                battery_soc,
            )

        # 2. Room temp adjustment: if already warm, don't ramp as fast
        cooling_trigger = self.get_cooling_trigger_temp()
        if daily_mode == ThermalMode.COOL:
            if avg_room_temp > cooling_trigger + 2:
                adjustments -= 0.5  # Reduce ramp if room already warm
                _LOGGER.debug(
                    "DW ramp: room warm (%.1f°C), reducing offset by -0.5°C",
                    avg_room_temp,
                )
            elif avg_room_temp > cooling_trigger:
                adjustments -= 0.25
                _LOGGER.debug(
                    "DW ramp: room slightly warm (%.1f°C), reducing offset by -0.25°C",
                    avg_room_temp,
                )

        # Calculate final offset
        final_offset = base_offset + adjustments

        # Clamp to reasonable range (0 to max + 1)
        final_offset = max(0.0, min(max_offset + 1.0, final_offset))

        _LOGGER.info(
            "DW ramp: progress=%.0f%%, base=%.1f°C, adjustments=%.1f°C, final=%.1f°C",
            progress * 100,
            base_offset,
            adjustments,
            final_offset,
        )

        return final_offset

    def reset_daily_thermal_state(self) -> None:
        """Reset daily thermal control state at midnight.

        Should be called once per day at midnight to reset the activation
        tracking for the new day.
        """
        _LOGGER.info(
            "Resetting daily thermal state (was activated: %s)",
            self._thermal_activated_today,
        )
        self._thermal_activated_today = False
        self._thermal_activated_at = None
        self._last_setpoint_change = None
        self._recent_room_temps.clear()

    def get_realtime_thermal_status(self) -> dict[str, Any]:
        """Get current real-time thermal control status for sensors.

        Returns:
            Dict with status information for sensor entities.
        """
        return {
            "activated_today": self._thermal_activated_today,
            "activated_at": self._thermal_activated_at.isoformat()
            if self._thermal_activated_at
            else None,
            "last_setpoint_change": self._last_setpoint_change.isoformat()
            if self._last_setpoint_change
            else None,
            "avg_room_temp": self._last_avg_room_temp,
            "recent_temps": self._recent_room_temps.copy(),
            "current_offset": self._current_active_offset,
            "user_override_active": self._user_override_until is not None,
            "user_override_until": self._user_override_until.isoformat()
            if self._user_override_until
            else None,
            "user_override_reason": self._user_override_reason,
        }
