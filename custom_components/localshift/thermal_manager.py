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
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CONF_COOLING_TRIGGER_TEMP,
    CONF_DEHUMIDIFY_TRIGGER_HUMIDITY,
    CONF_HEATING_TRIGGER_TEMP,
    CONF_PRECONDITION_HOURS_BEFORE_DW,
    CONF_PRECONDITION_TEMP_OFFSET,
    CONF_SOLAR_TAPER_ENABLED,
    CONF_TAPER_MAX_SETPOINT_OFFSET,
    CONF_THERMAL_MANAGEMENT_ENABLED,
    DEFAULT_COOLING_TRIGGER_TEMP,
    DEFAULT_DEHUMIDIFY_TRIGGER_HUMIDITY,
    DEFAULT_HEATING_TRIGGER_TEMP,
    DEFAULT_PRECONDITION_HOURS_BEFORE_DW,
    DEFAULT_PRECONDITION_TEMP_OFFSET,
    DEFAULT_SOLAR_TAPER_ENABLED,
    DEFAULT_TAPER_MAX_SETPOINT_OFFSET,
    DEFAULT_THERMAL_MANAGEMENT_ENABLED,
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
    """Learned power consumption for a climate entity."""

    entity_id: str
    cooling_power_kw: float = 0.0  # kW when cooling
    heating_power_kw: float = 0.0  # kW when heating
    drying_power_kw: float = 0.0  # kW when drying
    sample_count: int = 0
    confidence: str = "low"  # "low", "medium", "high"


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
                    cooling_power_kw=power_data.get("cooling_power_kw", 0.0),
                    heating_power_kw=power_data.get("heating_power_kw", 0.0),
                    drying_power_kw=power_data.get("drying_power_kw", 0.0),
                    sample_count=power_data.get("sample_count", 0),
                    confidence=power_data.get("confidence", "low"),
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
                    "cooling_power_kw": power.cooling_power_kw,
                    "heating_power_kw": power.heating_power_kw,
                    "drying_power_kw": power.drying_power_kw,
                    "sample_count": power.sample_count,
                    "confidence": power.confidence,
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
        """Get dehumidify trigger humidity threshold."""
        return self._get_option(
            CONF_DEHUMIDIFY_TRIGGER_HUMIDITY, DEFAULT_DEHUMIDIFY_TRIGGER_HUMIDITY
        )

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
        """Get max setpoint offset for solar tapering."""
        return self._get_option(
            CONF_TAPER_MAX_SETPOINT_OFFSET, DEFAULT_TAPER_MAX_SETPOINT_OFFSET
        )

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
    # Load Profile Separation
    # ------------------------------------------------------------------

    def separate_load_samples(
        self,
        samples: list[dict[str, Any]],
        climate_states: dict[str, dict[str, Any]],
    ) -> tuple[dict[int, list[float]], dict[int, list[float]]]:
        """Separate historical load samples into HVAC and non-HVAC buckets.

        Args:
            samples: List of historical load samples with 'hour' and 'power_kw'.
            climate_states: Climate state history (entity_id -> state dict).

        Returns:
            Tuple of (non_hvac_samples, hvac_samples), each as hour -> list of powers.
        """
        non_hvac: dict[int, list[float]] = {}
        hvac: dict[int, list[float]] = {}

        for sample in samples:
            hour = sample.get("hour", 0)
            power_kw = sample.get("power_kw", 0.0)

            # Check if any HVAC was active during this sample
            hvac_active = False
            for _entity_id, state in climate_states.items():
                action = state.get("hvac_action", "off")
                if action in ("cooling", "heating", "drying"):
                    hvac_active = True
                    break

            if hvac_active:
                if hour not in hvac:
                    hvac[hour] = []
                hvac[hour].append(power_kw)
            else:
                if hour not in non_hvac:
                    non_hvac[hour] = []
                non_hvac[hour].append(power_kw)

        return non_hvac, hvac

    def calculate_baseline_profile(
        self, non_hvac_samples: dict[int, list[float]]
    ) -> dict[int, float]:
        """Calculate baseline load profile using 25th percentile.

        The 25th percentile filters out discretionary load spikes
        (dishwasher, EV charging, etc.) while preserving the typical
        background consumption pattern.

        Args:
            non_hvac_samples: Hour -> list of power readings (non-HVAC only).

        Returns:
            Hour -> baseline power in kW.
        """

        baseline: dict[int, float] = {}

        for hour, powers in non_hvac_samples.items():
            if len(powers) >= 3:
                # Use 25th percentile
                sorted_powers = sorted(powers)
                idx = int(len(sorted_powers) * 0.25)
                baseline[hour] = sorted_powers[idx]
            elif powers:
                # Not enough samples for percentile - use min
                baseline[hour] = min(powers)
            else:
                baseline[hour] = 0.0

        return baseline

    def predict_hvac_load(
        self,
        hour: int,
        temperature: float,
        humidity: float | None = None,
        daily_mode: ThermalMode = ThermalMode.OFF,
    ) -> float:
        """Predict HVAC load for given hour based on weather and learned power.

        Args:
            hour: Hour of day (0-23).
            temperature: Forecasted temperature in °C.
            humidity: Current humidity percentage (optional).
            daily_mode: Current daily thermal mode.

        Returns:
            Predicted HVAC load in kW.
        """
        if daily_mode == ThermalMode.OFF:
            return 0.0

        # Get average learned power for the active mode
        total_power = 0.0
        entity_count = 0

        for _entity_id, power in self._learned_power.items():
            if power.confidence == "low":
                continue  # Skip low-confidence learning

            if daily_mode == ThermalMode.COOL and power.cooling_power_kw > 0:
                total_power += power.cooling_power_kw
                entity_count += 1
            elif daily_mode == ThermalMode.HEAT and power.heating_power_kw > 0:
                total_power += power.heating_power_kw
                entity_count += 1
            elif daily_mode == ThermalMode.DRY and power.drying_power_kw > 0:
                total_power += power.drying_power_kw
                entity_count += 1

        if entity_count == 0:
            # No learned data - use heuristic estimate
            if daily_mode == ThermalMode.COOL:
                # Estimate based on temperature differential
                cooling_trigger = self.get_cooling_trigger_temp()
                temp_diff = max(0, temperature - cooling_trigger)
                return min(3.0, temp_diff * 0.3)  # ~0.3 kW per degree over trigger
            elif daily_mode == ThermalMode.HEAT:
                heating_trigger = self.get_heating_trigger_temp()
                temp_diff = max(0, heating_trigger - temperature)
                return min(2.5, temp_diff * 0.25)  # ~0.25 kW per degree under trigger

        return total_power

    def get_learned_power_summary(self) -> dict[str, dict[str, Any]]:
        """Get summary of learned HVAC power for all entities.

        Returns:
            Dict of entity_id -> power summary dict.
        """
        return {
            entity_id: {
                "cooling_power_kw": power.cooling_power_kw,
                "heating_power_kw": power.heating_power_kw,
                "drying_power_kw": power.drying_power_kw,
                "sample_count": power.sample_count,
                "confidence": power.confidence,
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
            # Use conservative estimate of 2.5 kW
            total_hvac_power_kw = 2.5
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
        _demand_window_end: time,
    ) -> tuple[bool, float]:
        """Determine if pre-conditioning should be active.

        Args:
            data: CoordinatorData with current state.
            now: Current datetime.
            demand_window_start: Demand window start time.
            _demand_window_end: Demand window end time (unused, for API consistency).

        Returns:
            Tuple of (is_active, setpoint_offset).
        """
        if not self.is_enabled():
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

    async def async_apply_climate_control(
        self,
        data: CoordinatorData,
        setpoint_offset: float,
    ) -> None:
        """Apply setpoint adjustments to controlled climate entities.

        Args:
            data: CoordinatorData with current state.
            setpoint_offset: Setpoint adjustment in °C (positive = higher temp).
        """
        if not self.is_enabled():
            return

        if setpoint_offset == 0.0:
            return

        control_entities = data.climate_control_entities
        if not control_entities:
            return

        for entity_id in control_entities:
            state = data.climate_states.get(entity_id, {})
            current_setpoint = state.get("setpoint", 22.0)

            new_setpoint = current_setpoint + setpoint_offset

            # Clamp to reasonable range
            new_setpoint = max(16.0, min(30.0, new_setpoint))

            # Apply via climate.set_temperature service
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
                    "Adjusted %s setpoint: %.1f°C -> %.1f°C (offset %.1f°C)",
                    entity_id,
                    current_setpoint,
                    new_setpoint,
                    setpoint_offset,
                )
            except Exception as err:
                _LOGGER.warning("Failed to adjust %s setpoint: %s", entity_id, err)
