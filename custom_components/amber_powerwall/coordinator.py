"""Coordinator for the Amber Powerwall integration.

Subscribes to external entity state changes (Teslemetry, Amber, Solcast),
computes derived sensor values, and drives the battery state machine.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    CHARGE_RATE_BACKUP_KW,
    CONF_AMBER_FEED_IN_FORECAST,
    CONF_AMBER_FEED_IN_PRICE,
    CONF_AMBER_GENERAL_FORECAST,
    CONF_AMBER_GENERAL_PRICE,
    CONF_AMBER_PRICE_SPIKE,
    CONF_TESLEMETRY_ALLOW_EXPORT,
    CONF_BATTERY_TARGET,
    CONF_CHEAP_PRICE_DEADBAND,
    CONF_CHEAP_PRICE_PERCENTILE,
    CONF_DEMAND_WINDOW_END,
    CONF_DEMAND_WINDOW_START,
    CONF_FORECAST_LOOKAHEAD_HOURS,
    CONF_MAX_PRECHARGE_PRICE,
    CONF_NOTIFY_SERVICE,
    CONF_PRECHARGE_BATTERY_THRESHOLD,
    CONF_SOLCAST_FORECAST_TODAY,
    CONF_SOLCAST_FORECAST_TOMORROW,
    CONF_TESLEMETRY_BACKUP_RESERVE,
    CONF_TESLEMETRY_BATTERY_POWER,
    CONF_TESLEMETRY_GRID_POWER,
    CONF_TESLEMETRY_LOAD_POWER,
    CONF_TESLEMETRY_OPERATION_MODE,
    CONF_TESLEMETRY_SOC,
    CONF_TESLEMETRY_SOLAR_POWER,
    DEFAULT_BATTERY_TARGET,
    DEFAULT_CHEAP_PRICE_DEADBAND,
    DEFAULT_CHEAP_PRICE_PERCENTILE,
    DEFAULT_DEMAND_WINDOW_END,
    DEFAULT_DEMAND_WINDOW_START,
    DEFAULT_FORECAST_LOOKAHEAD_HOURS,
    DEFAULT_MAX_PRECHARGE_PRICE,
    DEFAULT_PRECHARGE_BATTERY_THRESHOLD,
    SOLAR_EXPORT_SURPLUS_ENTRY,
    SOLAR_EXPORT_SURPLUS_STAY,
    SWITCH_AUTOMATION_ENABLED,
    SWITCH_DEFAULTS,
    SWITCH_DEMAND_WINDOW_BLOCK,
    SWITCH_DRY_RUN,
    SWITCH_SPIKE_DISCHARGE_ENABLED,
    TESLEMETRY_EXPORT_BATTERY_OK,
    TESLEMETRY_EXPORT_PV_ONLY,
    BatteryMode,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# How often the coordinator re-evaluates (matches A16/A9 cadence)
PERIODIC_INTERVAL = timedelta(minutes=1)

# Powerwall capacity
BATTERY_CAPACITY_KWH = 13.5

# Spike discharge time window (dummy tariff limitation)
# Only discharge between 6am-midnight (dummy tariff has low sell price overnight)
DISCHARGE_EARLIEST_HOUR = 6
DISCHARGE_LATEST_HOUR = 0  # midnight (0 = 00:00, handled specially)


@dataclass
class CoordinatorData:
    """Snapshot of all computed data, consumed by sensor entities."""

    # External state (raw reads)
    grid_power_kw: float = 0.0
    battery_power_kw: float = 0.0
    solar_power_kw: float = 0.0
    load_power_kw: float = 0.0
    soc: float = 0.0
    operation_mode: str = ""
    backup_reserve: float = 0.0
    general_price: float = 0.0
    feed_in_price: float = 0.0
    price_spike: bool = False
    general_forecast: list[dict[str, Any]] = field(default_factory=list)
    feed_in_forecast: list[dict[str, Any]] = field(default_factory=list)
    solcast_today: list[dict[str, Any]] = field(default_factory=list)
    solcast_tomorrow: list[dict[str, Any]] = field(default_factory=list)
    allow_export: str = ""

    # Computed binary sensors
    forecast_spike_within_window: bool = False
    force_discharge_active: bool = False
    force_charge_active: bool = False
    boost_charge_active: bool = False
    hold_active: bool = False
    forecast_expensive_period_coming: bool = False
    solar_can_reach_target: bool = False
    boost_charge_needed: bool = False
    hold_justified: bool = False
    solar_export_hold_justified: bool = False
    demand_window_active: bool = False

    # Extra attributes for binary sensors
    max_forecast_price: float = 0.0
    surplus_ratio: float = 0.0

    # Computed sensors
    effective_cheap_price: float = 0.0
    cheap_charge_stop_price: float = 0.0
    solar_weighted_avg_fit: float = 0.0
    solar_remaining_kwh: float = 0.0
    active_mode: BatteryMode = BatteryMode.SELF_CONSUMPTION
    grid_import_power_kw: float = 0.0
    grid_export_power_kw: float = 0.0
    solar_battery_forecast: dict[str, Any] = field(default_factory=dict)
    decision_log: list[dict[str, Any]] = field(default_factory=list)

    # Cost accumulators (Phase 4)
    grid_import_cost: float = 0.0
    grid_export_revenue: float = 0.0
    battery_savings: float = 0.0
    battery_charge_cost: float = 0.0

    # Internal state flags (managed by state machine / buttons)
    manual_override: bool = False
    hold_mode: bool = False
    solar_export_hold: bool = False
    target_reached_today: bool = False


class AmberPowerwallCoordinator:
    """Central coordinator: reads external entities, computes state, drives battery.

    This is NOT a DataUpdateCoordinator (we don't poll an API). Instead we
    subscribe to HA entity state changes and run a periodic 1-minute tick.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator."""
        self.hass = hass
        self.entry = entry
        self.data = CoordinatorData()
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsub_state: CALLBACK_TYPE | None = None
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._unsub_midnight: CALLBACK_TYPE | None = None
        self._unsub_daily_summary: CALLBACK_TYPE | None = None
        self._update_callbacks: list[CALLBACK_TYPE] = []
        # Switch state bridge — switches read/write via these methods
        self._switch_states: dict[str, bool] = dict(SWITCH_DEFAULTS)
        # Track previous mode for decision log
        self._previous_active_mode: BatteryMode = BatteryMode.SELF_CONSUMPTION
        # State machine tracking (Phase 3)
        self._commanded_mode: BatteryMode = BatteryMode.SELF_CONSUMPTION
        self._mode_desired_since: dict[BatteryMode, datetime] = {}
        self._startup_grace_until: datetime | None = None
        self._evaluate_lock = asyncio.Lock()
        # Flag to skip re-evaluation during programmatic mode transitions
        self._in_mode_transition: bool = False

    # ------------------------------------------------------------------
    # Entity ID helpers (read from config entry data)
    # ------------------------------------------------------------------

    @property
    def entity_ids(self) -> dict[str, str]:
        """Return the configured external entity IDs."""
        return self.entry.data

    def _get_entity_id(self, key: str) -> str:
        """Get a configured external entity ID by config key."""
        return self.entry.data[key]

    # ------------------------------------------------------------------
    # Options helpers (read from config entry options)
    # ------------------------------------------------------------------

    def get_option(self, key: str, default: Any = None) -> Any:
        """Get a user-configurable option value."""
        return self.entry.options.get(key, default)

    # ------------------------------------------------------------------
    # Switch state bridge
    # ------------------------------------------------------------------

    def get_switch_state(self, key: str) -> bool:
        """Get a switch state by key."""
        return self._switch_states.get(key, SWITCH_DEFAULTS.get(key, False))

    def set_switch_state(self, key: str, value: bool) -> None:
        """Set a switch state and trigger re-evaluation."""
        self._switch_states[key] = value

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Start listening to entity changes and periodic timer."""
        # Collect all external entity IDs to watch
        # NOTE: We don't watch CONF_TESLEMETRY_ALLOW_EXPORT because we change it
        # programmatically and don't want to trigger re-evaluation loops
        monitored_entities = [
            self._get_entity_id(CONF_TESLEMETRY_OPERATION_MODE),
            self._get_entity_id(CONF_TESLEMETRY_BACKUP_RESERVE),
            self._get_entity_id(CONF_TESLEMETRY_SOC),
            self._get_entity_id(CONF_TESLEMETRY_GRID_POWER),
            self._get_entity_id(CONF_TESLEMETRY_BATTERY_POWER),
            self._get_entity_id(CONF_TESLEMETRY_SOLAR_POWER),
            self._get_entity_id(CONF_TESLEMETRY_LOAD_POWER),
            # NOT monitoring allow_export - changes programmatically
            self._get_entity_id(CONF_AMBER_GENERAL_PRICE),
            self._get_entity_id(CONF_AMBER_FEED_IN_PRICE),
            self._get_entity_id(CONF_AMBER_GENERAL_FORECAST),
            self._get_entity_id(CONF_AMBER_FEED_IN_FORECAST),
            self._get_entity_id(CONF_AMBER_PRICE_SPIKE),
            self._get_entity_id(CONF_SOLCAST_FORECAST_TODAY),
            self._get_entity_id(CONF_SOLCAST_FORECAST_TOMORROW),
        ]

        # Subscribe to state changes
        self._unsub_state = async_track_state_change_event(
            self.hass, monitored_entities, self._handle_state_change
        )

        # 1-minute periodic tick (cost accumulation, DW checks, re-evaluation)
        self._unsub_timer = async_track_time_interval(
            self.hass, self._handle_periodic_tick, PERIODIC_INTERVAL
        )

        # Midnight reset (replaces A12): reset cost accumulators + target flag
        self._unsub_midnight = async_track_time_change(
            self.hass, self._handle_midnight_reset, hour=0, minute=0, second=0
        )

        # Daily summary notification (replaces A15): fires at DW end time
        dw_end = self._parse_time_option(
            CONF_DEMAND_WINDOW_END, DEFAULT_DEMAND_WINDOW_END
        )
        self._unsub_daily_summary = async_track_time_change(
            self.hass,
            self._handle_daily_summary,
            hour=dw_end.hour,
            minute=dw_end.minute,
            second=0,
        )

        # Read initial state and compute
        self._read_all_external_state()
        self._compute_derived_values()

        # Startup grace: wait 30 s for entities to populate before acting
        self._startup_grace_until = dt_util.now() + timedelta(seconds=30)
        self._commanded_mode = self._infer_current_hardware_mode()

        _LOGGER.info(
            "Amber Powerwall coordinator started, monitoring %d entities, "
            "inferred mode: %s",
            len(monitored_entities),
            self._commanded_mode.value,
        )

    async def async_stop(self) -> None:
        """Stop listening and clean up."""
        for unsub in (
            self._unsub_state,
            self._unsub_timer,
            self._unsub_midnight,
            self._unsub_daily_summary,
        ):
            if unsub:
                unsub()
        self._unsub_state = None
        self._unsub_timer = None
        self._unsub_midnight = None
        self._unsub_daily_summary = None
        _LOGGER.info("Amber Powerwall coordinator stopped")

    # ------------------------------------------------------------------
    # Entity update subscription (for sensor/binary_sensor entities)
    # ------------------------------------------------------------------

    @callback
    def async_add_listener(self, update_callback: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Register a callback that fires when data changes.

        Returns a callable to unsubscribe.
        """
        self._update_callbacks.append(update_callback)

        @callback
        def remove_listener() -> None:
            self._update_callbacks.remove(update_callback)

        return remove_listener

    @callback
    def _notify_listeners(self) -> None:
        """Notify all registered entity listeners of new data."""
        for cb in self._update_callbacks:
            cb()

    # ------------------------------------------------------------------
    # State reading
    # ------------------------------------------------------------------

    def _read_float(self, entity_id: str, default: float = 0.0) -> float:
        """Read a float value from an entity's state."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default

    def _read_state(self, entity_id: str, default: str = "") -> str:
        """Read a string value from an entity's state."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        return state.state

    def _read_bool(self, entity_id: str) -> bool:
        """Read a boolean value from an entity's state (on/off)."""
        return self._read_state(entity_id) == "on"

    def _read_attribute(
        self, entity_id: str, attr: str, default: Any = None
    ) -> Any:
        """Read an attribute from an entity."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return default
        return state.attributes.get(attr, default)

    def _read_all_external_state(self) -> None:
        """Read current state of all monitored external entities."""
        d = self.data

        # Teslemetry
        d.grid_power_kw = self._read_float(
            self._get_entity_id(CONF_TESLEMETRY_GRID_POWER)
        )
        d.battery_power_kw = self._read_float(
            self._get_entity_id(CONF_TESLEMETRY_BATTERY_POWER)
        )
        d.solar_power_kw = self._read_float(
            self._get_entity_id(CONF_TESLEMETRY_SOLAR_POWER)
        )
        d.load_power_kw = self._read_float(
            self._get_entity_id(CONF_TESLEMETRY_LOAD_POWER)
        )
        d.soc = self._read_float(self._get_entity_id(CONF_TESLEMETRY_SOC))
        d.operation_mode = self._read_state(
            self._get_entity_id(CONF_TESLEMETRY_OPERATION_MODE)
        )
        d.backup_reserve = self._read_float(
            self._get_entity_id(CONF_TESLEMETRY_BACKUP_RESERVE)
        )
        d.allow_export = self._read_state(
            self._get_entity_id(CONF_TESLEMETRY_ALLOW_EXPORT)
        )

        # Amber
        d.general_price = self._read_float(
            self._get_entity_id(CONF_AMBER_GENERAL_PRICE)
        )
        d.feed_in_price = self._read_float(
            self._get_entity_id(CONF_AMBER_FEED_IN_PRICE)
        )
        d.price_spike = self._read_bool(
            self._get_entity_id(CONF_AMBER_PRICE_SPIKE)
        )
        d.general_forecast = (
            self._read_attribute(
                self._get_entity_id(CONF_AMBER_GENERAL_FORECAST), "forecasts", []
            )
            or []
        )
        d.feed_in_forecast = (
            self._read_attribute(
                self._get_entity_id(CONF_AMBER_FEED_IN_FORECAST), "forecasts", []
            )
            or []
        )

        # Solcast
        d.solcast_today = (
            self._read_attribute(
                self._get_entity_id(CONF_SOLCAST_FORECAST_TODAY),
                "detailedForecast",
                [],
            )
            or []
        )
        d.solcast_tomorrow = (
            self._read_attribute(
                self._get_entity_id(CONF_SOLCAST_FORECAST_TOMORROW),
                "detailedForecast",
                [],
            )
            or []
        )

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    @callback
    def _handle_state_change(self, event: Event) -> None:
        """Handle a state change from a monitored entity."""
        # Skip re-evaluation if we're in the middle of a mode transition
        # This prevents feedback loops when we programmatically change entities
        if self._in_mode_transition:
            _LOGGER.debug("Skipping re-evaluation during mode transition")
            return

        self._read_all_external_state()
        self._compute_derived_values()
        self.hass.async_create_task(
            self.async_evaluate_state_machine(),
            "amber_powerwall_evaluate_state_change",
        )
        self._notify_listeners()

    @callback
    def _handle_periodic_tick(self, now: datetime) -> None:
        """Handle the 1-minute periodic re-evaluation."""
        self._read_all_external_state()
        self._compute_derived_values()
        self.hass.async_create_task(
            self.async_evaluate_state_machine(),
            "amber_powerwall_evaluate_periodic",
        )
        self._accumulate_costs()
        self._notify_listeners()

    @callback
    def _handle_midnight_reset(self, now: datetime) -> None:
        """Reset daily cost accumulators and target flag at midnight.

        Replaces YAML A12 (amber_reset_target_reached).
        """
        d = self.data
        d.grid_import_cost = 0.0
        d.grid_export_revenue = 0.0
        d.battery_savings = 0.0
        d.battery_charge_cost = 0.0
        d.target_reached_today = False
        self._notify_listeners()
        _LOGGER.info("Midnight reset: cost accumulators and target flag cleared")

    @callback
    def _handle_daily_summary(self, now: datetime) -> None:
        """Send daily summary notification at demand window end.

        Replaces YAML A15 (amber_daily_summary).
        """
        if not self.get_switch_state(SWITCH_AUTOMATION_ENABLED):
            return

        self.hass.async_create_task(
            self._send_daily_summary(),
            "amber_powerwall_daily_summary",
        )

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _parse_time_option(self, key: str, default: str) -> time:
        """Parse a time string option (HH:MM:SS) into a time object."""
        time_str = str(self.get_option(key, default))
        parts = time_str.split(":")
        try:
            return time(
                int(parts[0]),
                int(parts[1]) if len(parts) > 1 else 0,
                int(parts[2]) if len(parts) > 2 else 0,
            )
        except (ValueError, IndexError):
            d_parts = default.split(":")
            return time(int(d_parts[0]), int(d_parts[1]), int(d_parts[2]))

    @staticmethod
    def _parse_forecast_dt(dt_str: str | None) -> datetime | None:
        """Parse an ISO format datetime string from forecast data."""
        if dt_str is None:
            return None
        try:
            return dt_util.parse_datetime(str(dt_str))
        except (ValueError, TypeError):
            return None

    def _sum_solar_before_target(
        self,
        solcast: list[dict[str, Any]],
        now_dt: datetime,
        target_hour: int,
    ) -> float:
        """Sum pessimistic solar kWh (pv_estimate10) from now until target_hour.

        Includes prorated energy for the in-progress period (the period
        whose start_time <= now < start_time + 30 min).
        """
        target_dt = now_dt.replace(
            hour=target_hour, minute=0, second=0, microsecond=0
        )
        period_duration = timedelta(minutes=30)
        total = 0.0
        for period in solcast:
            period_start = self._parse_forecast_dt(period.get("period_start"))
            if period_start is None:
                continue
            ps_local = dt_util.as_local(period_start)
            period_end = ps_local + period_duration
            kwh = float(period.get("pv_estimate10", 0))

            if ps_local >= target_dt:
                # Period starts at or after the target — skip
                continue

            if ps_local >= now_dt:
                # Fully future period before target — include all of it
                total += kwh
            elif period_end > now_dt:
                # In-progress period — prorate remaining fraction
                remaining = (period_end - now_dt).total_seconds()
                fraction = remaining / period_duration.total_seconds()
                total += kwh * fraction

        return total

    @staticmethod
    def _scan_forecast_for_spike(
        forecasts: list[dict[str, Any]],
        now_dt: datetime,
        cutoff: datetime,
    ) -> bool:
        """Return True if any forecast has spike_status == 'spike' in window."""
        for f in forecasts:
            start = AmberPowerwallCoordinator._parse_forecast_dt(
                f.get("start_time")
            )
            if start is None:
                continue
            start_local = dt_util.as_local(start)
            if start_local >= now_dt and start_local <= cutoff:
                if f.get("spike_status") == "spike":
                    return True
        return False

    @staticmethod
    def _max_forecast_price(
        forecasts: list[dict[str, Any]],
        now_dt: datetime,
        cutoff: datetime,
    ) -> float:
        """Return the maximum per_kwh price from forecasts within the window."""
        max_price = 0.0
        for f in forecasts:
            start = AmberPowerwallCoordinator._parse_forecast_dt(
                f.get("start_time")
            )
            if start is None:
                continue
            start_local = dt_util.as_local(start)
            if start_local >= now_dt and start_local <= cutoff:
                price = float(f.get("per_kwh", 0))
                if price > max_price:
                    max_price = price
        return round(max_price, 2)

    @staticmethod
    def _percentile(
        prices: list[float],
        percentile: float,
    ) -> float:
        """Calculate the Nth percentile of a list of prices.
        
        Uses linear interpolation between closest ranks.
        """
        if not prices:
            return 0.0
        sorted_prices = sorted(prices)
        n = len(sorted_prices)
        index = (percentile / 100) * (n - 1)
        lower = int(index)
        upper = lower + 1
        if upper >= n:
            return sorted_prices[-1]
        fraction = index - lower
        return sorted_prices[lower] * (1 - fraction) + sorted_prices[upper] * fraction

    # ------------------------------------------------------------------
    # Cost tracking (Phase 4)
    # ------------------------------------------------------------------

    def _accumulate_costs(self) -> None:
        """Accumulate per-minute energy costs from current power and price.

        Replaces YAML A16 (amber_cost_accumulator).
        Formula: power_kW × price_$/kWh / 60 = $/min
        """
        d = self.data

        # Grid import cost: positive grid power × buy price
        import_cost = max(d.grid_power_kw, 0.0) * d.general_price / 60
        d.grid_import_cost += import_cost

        # Grid export revenue: negative grid power (export) × sell price
        export_revenue = max(-d.grid_power_kw, 0.0) * d.feed_in_price / 60
        d.grid_export_revenue += export_revenue

        # Battery savings: battery discharge × buy price (avoided purchase)
        savings = max(-d.battery_power_kw, 0.0) * d.general_price / 60
        d.battery_savings += savings

        # Battery charge cost: battery charge × buy price
        charge_cost = max(d.battery_power_kw, 0.0) * d.general_price / 60
        d.battery_charge_cost += charge_cost

    async def _send_daily_summary(self) -> None:
        """Send end-of-day summary notification with energy and cost stats.

        Replaces YAML A15 (amber_daily_summary). Reads daily energy
        from utility meter entities (still in YAML) and cost accumulators.
        """
        d = self.data
        net = d.grid_import_cost - d.grid_export_revenue

        # Read daily energy from utility meter sensors (remain in YAML)
        import_kwh = self._read_float(
            "sensor.grid_import_energy_daily", 0.0
        )
        export_kwh = self._read_float(
            "sensor.grid_export_energy_daily", 0.0
        )
        solar_kwh = self._read_float(
            "sensor.solar_production_energy_daily", 0.0
        )

        soc = round(d.soc)

        message = (
            f"Today so far:\n\n"
            f"Solar: {solar_kwh:.1f} kWh\n"
            f"Grid import: {import_kwh:.1f} kWh "
            f"(${d.grid_import_cost:.2f})\n"
            f"Grid export: {export_kwh:.1f} kWh "
            f"(${d.grid_export_revenue:.2f} revenue)\n"
            f"Net cost: ${net:.2f}\n\n"
            f"Battery savings: ${d.battery_savings:.2f}\n"
            f"Battery charge cost: ${d.battery_charge_cost:.2f}\n"
            f"SOC: {soc}%"
        )

        await self.async_send_notification(
            "Powerwall: Daily Summary", message
        )
        _LOGGER.info("Daily summary notification sent")

    # ------------------------------------------------------------------
    # State machine helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_debounce_for_transition(
        from_mode: BatteryMode, to_mode: BatteryMode
    ) -> timedelta:
        """Return the required debounce duration for a mode transition.

        Matches the YAML ``for: minutes: N`` patterns:
        - Spike / demand window / manual → immediate
        - Solar export hold → 2 minutes (A17/A18)
        - All price-driven transitions → 5 minutes (A3/A4/A10/A11)
        """
        # Immediate: high-priority or safety transitions
        if to_mode in (
            BatteryMode.SPIKE_DISCHARGE,
            BatteryMode.DEMAND_BLOCK,
            BatteryMode.MANUAL,
        ):
            return timedelta(0)
        # Immediate: leaving high-priority modes
        if from_mode in (BatteryMode.SPIKE_DISCHARGE, BatteryMode.DEMAND_BLOCK):
            return timedelta(0)
        # Immediate: holding for spike (forecast-based, not price jitter)
        if to_mode == BatteryMode.HOLDING_FOR_SPIKE:
            return timedelta(0)
        # Solar export hold: 2 minutes
        if (
            to_mode == BatteryMode.SOLAR_EXPORT_HOLD
            or from_mode == BatteryMode.SOLAR_EXPORT_HOLD
        ):
            return timedelta(minutes=2)
        # All other (price-driven): 5 minutes
        return timedelta(minutes=5)

    def _infer_current_hardware_mode(self) -> BatteryMode:
        """Infer the current battery mode from Teslemetry hardware state.

        Used at startup to sync ``_commanded_mode`` so we don't issue
        a redundant command on the first evaluation.
        """
        d = self.data
        if d.force_discharge_active:
            return BatteryMode.SPIKE_DISCHARGE
        if d.boost_charge_active:
            return BatteryMode.BOOST_CHARGING
        if d.force_charge_active:
            return BatteryMode.GRID_CHARGING
        if d.hold_mode:
            if d.solar_export_hold:
                return BatteryMode.SOLAR_EXPORT_HOLD
            return BatteryMode.HOLD
        return BatteryMode.SELF_CONSUMPTION

    # ------------------------------------------------------------------
    # Computation — all derived values in dependency order
    # ------------------------------------------------------------------

    def _compute_derived_values(self) -> None:
        """Compute all derived sensor/binary_sensor values from raw state.

        Ported from Jinja templates in the YAML package. Steps are ordered
        by dependency — later steps can reference earlier results.
        """
        d = self.data
        now_dt = dt_util.now()

        # Common time values used by multiple steps
        dw_start_time = self._parse_time_option(
            CONF_DEMAND_WINDOW_START, DEFAULT_DEMAND_WINDOW_START
        )
        dw_end_time = self._parse_time_option(
            CONF_DEMAND_WINDOW_END, DEFAULT_DEMAND_WINDOW_END
        )
        target_hour = dw_start_time.hour
        now_t = now_dt.time()
        before_dw = now_t < dw_start_time
        after_dw = now_t >= dw_start_time

        # ---- Step 1: Directional power (always positive) ----
        d.grid_import_power_kw = max(d.grid_power_kw, 0.0)
        d.grid_export_power_kw = max(-d.grid_power_kw, 0.0)

        # ---- Step 2: Mode detection from Teslemetry state ----
        d.force_discharge_active = (
            d.operation_mode == "autonomous" and d.backup_reserve < 11
        )
        # force_charge_active = ANY charging state (backup OR boost)
        d.force_charge_active = d.operation_mode == "backup" or (
            d.operation_mode == "autonomous" and d.backup_reserve > 99
        )
        d.boost_charge_active = (
            d.operation_mode == "autonomous" and d.backup_reserve > 99
        )
        # hold_active uses internal flag (matches YAML: input_boolean.battery_hold_mode)
        d.hold_active = d.hold_mode

        # ---- Step 3: demand_window_active ----
        dw_block_enabled = self.get_switch_state(SWITCH_DEMAND_WINDOW_BLOCK)
        d.demand_window_active = (
            dw_block_enabled and now_t >= dw_start_time and now_t < dw_end_time
        )

        # ---- Step 4: solar_battery_forecast ----
        target_pct = float(
            self.get_option(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
        )

        if after_dw:
            # After DW start: report current SOC, safe defaults
            d.solar_battery_forecast = {
                "predicted_soc": round(d.soc, 1),
                "solar_before_dw_kwh": 0.0,
                "consumption_estimate_kwh": 0.0,
                "net_solar_kwh": 0.0,
                "deficit_kwh": 0.0,
                "can_reach_target": True,
                "boost_needed": False,
                "hours_to_target_time": 0.0,
            }
            # Mark target reached if SOC is there
            if d.soc >= target_pct:
                d.target_reached_today = True
        else:
            # Hours remaining until DW start
            target_dt = now_dt.replace(
                hour=target_hour, minute=0, second=0, microsecond=0
            )
            hours_to_target = max(
                (target_dt - now_dt).total_seconds() / 3600, 0
            )

            # Deficit: kWh needed to reach target
            deficit_kwh = max(
                (target_pct - d.soc) / 100 * BATTERY_CAPACITY_KWH, 0
            )

            # Solar forecast: pessimistic estimate between now and DW
            solar_kwh = self._sum_solar_before_target(
                d.solcast_today, now_dt, target_hour
            )

            # Consumption estimate: current load extrapolated
            load_kw = d.load_power_kw if d.load_power_kw > 0 else 0.5
            consumption_kwh = load_kw * hours_to_target

            # Net solar (after consumption)
            net_solar = solar_kwh - consumption_kwh

            # Predicted SOC at DW
            net_solar_pct = net_solar / BATTERY_CAPACITY_KWH * 100
            predicted_soc = d.soc + net_solar_pct

            # Can solar alone reach target?
            can_reach = d.soc >= target_pct or net_solar >= deficit_kwh

            # Boost needed? Only if gentle charging can't reach target before DW
            # Goal: reach target as close to DW start as possible
            if d.soc >= target_pct:
                boost_needed = False
            else:
                remaining_deficit = max(deficit_kwh - max(net_solar, 0), 0)
                # Time needed to charge remaining deficit at gentle rate (3.3kW)
                # Include 90% efficiency factor
                time_needed_hours = remaining_deficit / (CHARGE_RATE_BACKUP_KW * 0.9) if remaining_deficit > 0 else 0
                # Only boost if we can't make it in time with gentle charging
                # Allow 30 minute buffer to ensure we reach target before DW
                boost_needed = time_needed_hours > (hours_to_target - 0.5) and remaining_deficit > 0

            d.solar_battery_forecast = {
                "predicted_soc": round(predicted_soc, 1),
                "solar_before_dw_kwh": round(solar_kwh, 2),
                "consumption_estimate_kwh": round(consumption_kwh, 2),
                "net_solar_kwh": round(net_solar, 2),
                "deficit_kwh": round(deficit_kwh, 2),
                "can_reach_target": can_reach,
                "boost_needed": boost_needed,
                "hours_to_target_time": round(hours_to_target, 1),
            }

            # Mark target reached if SOC is there
            if d.soc >= target_pct:
                d.target_reached_today = True

        # ---- Step 5: solar_can_reach_target (from forecast) ----
        d.solar_can_reach_target = d.solar_battery_forecast.get(
            "can_reach_target", True
        )

        # ---- Step 6: boost_charge_needed (from forecast) ----
        d.boost_charge_needed = d.solar_battery_forecast.get(
            "boost_needed", False
        )

        # ---- Step 7: effective_cheap_price ----
        # Calculate base from percentile of forecast prices
        lookahead = float(
            self.get_option(
                CONF_FORECAST_LOOKAHEAD_HOURS, DEFAULT_FORECAST_LOOKAHEAD_HOURS
            )
        )
        cutoff = now_dt + timedelta(hours=lookahead)
        
        # Collect forecast prices within lookahead window
        forecast_prices = []
        for f in d.general_forecast:
            start = self._parse_forecast_dt(f.get("start_time"))
            if start is None:
                continue
            start_local = dt_util.as_local(start)
            if start_local >= now_dt and start_local <= cutoff:
                forecast_prices.append(float(f.get("per_kwh", 0)))
        
        # Calculate percentile-based cheap price
        percentile = float(
            self.get_option(
                CONF_CHEAP_PRICE_PERCENTILE, DEFAULT_CHEAP_PRICE_PERCENTILE
            )
        )
        if forecast_prices:
            base = round(self._percentile(forecast_prices, percentile), 2)
        else:
            # Fallback to max_precharge_price if no forecast data
            base = float(
                self.get_option(
                    CONF_MAX_PRECHARGE_PRICE, DEFAULT_MAX_PRECHARGE_PRICE
                )
            )
        
        max_price = float(
            self.get_option(
                CONF_MAX_PRECHARGE_PRICE, DEFAULT_MAX_PRECHARGE_PRICE
            )
        )
        solar_gap = not d.solar_can_reach_target

        if not solar_gap or not before_dw or d.target_reached_today:
            d.effective_cheap_price = base
        else:
            target_dt = now_dt.replace(
                hour=target_hour, minute=0, second=0, microsecond=0
            )
            hours_left = max(
                (target_dt - now_dt).total_seconds() / 3600, 0
            )
            total_window = 8.0
            urgency = max(min(1 - (hours_left / total_window), 1.0), 0.0)
            urgency_price = base + (max_price - base) * urgency

            # Find minimum forecast price before DW
            min_forecast = max_price
            for f in d.general_forecast:
                start = self._parse_forecast_dt(f.get("start_time"))
                if start is None:
                    continue
                start_local = dt_util.as_local(start)
                if (
                    start_local >= now_dt
                    and start_local.hour < target_hour
                ):
                    price = float(f.get("per_kwh", max_price))
                    if price < min_forecast:
                        min_forecast = price

            forecast_floor = max(min_forecast + 0.02, base)
            final = min(urgency_price, max_price)
            final = max(final, forecast_floor)
            d.effective_cheap_price = round(final, 2)

        # ---- Step 8: cheap_charge_stop_price ----
        deadband = float(
            self.get_option(
                CONF_CHEAP_PRICE_DEADBAND, DEFAULT_CHEAP_PRICE_DEADBAND
            )
        )
        d.cheap_charge_stop_price = round(
            d.effective_cheap_price + deadband, 2
        )

        # ---- Step 9: forecast_spike_within_window ----
        lookahead = float(
            self.get_option(
                CONF_FORECAST_LOOKAHEAD_HOURS, DEFAULT_FORECAST_LOOKAHEAD_HOURS
            )
        )
        cutoff = now_dt + timedelta(hours=lookahead)
        d.forecast_spike_within_window = self._scan_forecast_for_spike(
            d.feed_in_forecast, now_dt, cutoff
        )
        d.max_forecast_price = self._max_forecast_price(
            d.feed_in_forecast, now_dt, cutoff
        )

        # ---- Step 10: forecast_expensive_period_coming ----
        d.forecast_expensive_period_coming = self._scan_forecast_for_spike(
            d.general_forecast, now_dt, cutoff
        )

        # ---- Step 11: hold_justified ----
        # Check 1: meaningful solar (>= 0.5 kWh) within lookahead
        solar_kwh_lookahead = 0.0
        for forecast_list in [d.solcast_today, d.solcast_tomorrow]:
            for period in forecast_list:
                period_start = self._parse_forecast_dt(
                    period.get("period_start")
                )
                if period_start is None:
                    continue
                ps_local = dt_util.as_local(period_start)
                if ps_local >= now_dt and ps_local <= cutoff:
                    solar_kwh_lookahead += float(
                        period.get("pv_estimate10", 0)
                    )

        # Check 2: cheaper price coming within lookahead
        cheap_coming = False
        for f in d.general_forecast:
            start = self._parse_forecast_dt(f.get("start_time"))
            if start is None:
                continue
            start_local = dt_util.as_local(start)
            if start_local >= now_dt and start_local <= cutoff:
                if float(f.get("per_kwh", 99)) < d.effective_cheap_price:
                    cheap_coming = True
                    break

        d.hold_justified = solar_kwh_lookahead >= 0.5 or cheap_coming

        # ---- Step 12: solar_weighted_avg_fit ----
        if after_dw:
            d.solar_weighted_avg_fit = 0.0
            d.solar_remaining_kwh = 0.0
        else:
            weighted_sum = 0.0
            total_solar = 0.0

            for period in d.solcast_today:
                period_start = self._parse_forecast_dt(
                    period.get("period_start")
                )
                if period_start is None:
                    continue
                ps_local = dt_util.as_local(period_start)
                if ps_local >= now_dt and ps_local.hour < target_hour:
                    solar_kwh_val = float(period.get("pv_estimate10", 0))
                    if solar_kwh_val > 0:
                        # Find FIT price at midpoint of 30-min period
                        mid = period_start + timedelta(minutes=15)
                        fit_price = 0.0
                        for f in d.feed_in_forecast:
                            f_start = self._parse_forecast_dt(
                                f.get("start_time")
                            )
                            f_end = self._parse_forecast_dt(
                                f.get("end_time")
                            )
                            if (
                                f_start is not None
                                and f_end is not None
                                and f_start <= mid < f_end
                            ):
                                fit_price = float(f.get("per_kwh", 0))
                                break

                        weighted_sum += solar_kwh_val * fit_price
                        total_solar += solar_kwh_val

            if total_solar > 0:
                d.solar_weighted_avg_fit = round(
                    weighted_sum / total_solar, 4
                )
            else:
                d.solar_weighted_avg_fit = 0.0
            d.solar_remaining_kwh = round(total_solar, 2)

        # ---- Step 13: solar_export_hold_justified ----
        # Safe default: assume sun is down if entity unavailable (prevents overnight issues)
        sun_state = self.hass.states.get("sun.sun")
        sun_up = sun_state is not None and sun_state.state == "above_horizon"
        deficit_kwh = d.solar_battery_forecast.get("deficit_kwh", 0)
        net_solar_kwh = d.solar_battery_forecast.get("net_solar_kwh", 0)
        current_fit = d.feed_in_price
        avg_fit = d.solar_weighted_avg_fit
        in_solar_export_hold = d.solar_export_hold
        charging = d.force_charge_active

        if (
            not sun_up
            or not before_dw
            or d.demand_window_active
            or deficit_kwh <= 0
            or charging
        ):
            d.solar_export_hold_justified = False
            d.surplus_ratio = 0.0
        else:
            surplus_ratio = (
                net_solar_kwh / deficit_kwh if deficit_kwh > 0 else 0
            )
            d.surplus_ratio = round(surplus_ratio, 2)
            threshold = (
                SOLAR_EXPORT_SURPLUS_STAY
                if in_solar_export_hold
                else SOLAR_EXPORT_SURPLUS_ENTRY
            )
            d.solar_export_hold_justified = (
                surplus_ratio >= threshold
                and current_fit > avg_fit
                and avg_fit > 0
            )

        # ---- Step 14: active_mode ----
        automation_enabled = self.get_switch_state(SWITCH_AUTOMATION_ENABLED)
        spike_discharge_enabled = self.get_switch_state(
            SWITCH_SPIKE_DISCHARGE_ENABLED
        )

        # Check if we're in the valid discharge window (6am-midnight)
        # The dummy tariff has low sell price overnight, so discharging is pointless
        current_hour = now_dt.hour
        in_discharge_window = current_hour >= DISCHARGE_EARLIEST_HOUR

        if not automation_enabled:
            d.active_mode = BatteryMode.MANUAL
        elif d.demand_window_active:
            d.active_mode = BatteryMode.DEMAND_BLOCK
        elif d.price_spike and spike_discharge_enabled and in_discharge_window:
            # Spike overrides manual actions (YAML A1/A5 have no manual check)
            # Only discharge during valid window (6am-midnight)
            d.active_mode = BatteryMode.SPIKE_DISCHARGE
        elif d.manual_override:
            d.active_mode = BatteryMode.MANUAL
        elif d.solar_export_hold and d.hold_mode:
            d.active_mode = BatteryMode.SOLAR_EXPORT_HOLD
        elif d.general_price < d.effective_cheap_price:
            # Price below threshold — consider charging
            precharge_threshold = float(
                self.get_option(
                    CONF_PRECHARGE_BATTERY_THRESHOLD,
                    DEFAULT_PRECHARGE_BATTERY_THRESHOLD,
                )
            )
            battery_low = d.soc < precharge_threshold
            expensive_coming = d.forecast_expensive_period_coming
            solar_gap_flag = not d.solar_can_reach_target

            if d.target_reached_today:
                d.active_mode = BatteryMode.SELF_CONSUMPTION
            elif sun_up and (solar_gap_flag or expensive_coming):
                if d.boost_charge_needed:
                    d.active_mode = BatteryMode.BOOST_CHARGING
                else:
                    d.active_mode = BatteryMode.GRID_CHARGING
            elif not sun_up and battery_low and expensive_coming:
                d.active_mode = BatteryMode.GRID_CHARGING
            else:
                d.active_mode = BatteryMode.SELF_CONSUMPTION
        elif d.general_price < d.cheap_charge_stop_price:
            # Price in deadband — maintain charge or hold
            if d.force_charge_active:
                if d.boost_charge_active:
                    d.active_mode = BatteryMode.BOOST_CHARGING
                else:
                    d.active_mode = BatteryMode.GRID_CHARGING
            else:
                if d.hold_justified:
                    d.active_mode = BatteryMode.HOLD
                else:
                    d.active_mode = BatteryMode.SELF_CONSUMPTION
        elif d.forecast_spike_within_window:
            d.active_mode = BatteryMode.HOLDING_FOR_SPIKE
        else:
            d.active_mode = BatteryMode.SELF_CONSUMPTION

        # ---- Step 15: decision_log ----
        if d.active_mode != self._previous_active_mode:
            reason = self._generate_decision_reason(
                self._previous_active_mode, d.active_mode, d
            )
            entry = {
                "timestamp": dt_util.now().isoformat(),
                "old_mode": self._previous_active_mode.value,
                "new_mode": d.active_mode.value,
                "buy_price": round(d.general_price, 2),
                "sell_price": round(d.feed_in_price, 2),
                "soc": round(d.soc),
                "effective_threshold": d.effective_cheap_price,
                "reason": reason,
            }
            d.decision_log.append(entry)
            # Cap log at 50 entries
            if len(d.decision_log) > 50:
                d.decision_log = d.decision_log[-50:]

            self._previous_active_mode = d.active_mode

    # ------------------------------------------------------------------
    # Decision log reason generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_decision_reason(
        old_mode: BatteryMode,
        new_mode: BatteryMode,
        d: CoordinatorData,
    ) -> str:
        """Generate a human-readable reason for a mode transition."""
        if new_mode == BatteryMode.SPIKE_DISCHARGE:
            return (
                f"Price spike detected "
                f"(feed-in ${d.feed_in_price:.2f}/kWh)"
            )
        if new_mode == BatteryMode.DEMAND_BLOCK:
            return "Demand window active -- protecting from grid imports"
        if new_mode == BatteryMode.GRID_CHARGING:
            return (
                f"Price ${d.general_price:.2f}/kWh below threshold "
                f"${d.effective_cheap_price:.2f}/kWh, "
                f"SOC {d.soc:.0f}%"
            )
        if new_mode == BatteryMode.BOOST_CHARGING:
            return (
                f"Solar gap -- boost charging needed, "
                f"price ${d.general_price:.2f}/kWh, "
                f"SOC {d.soc:.0f}%"
            )
        if new_mode == BatteryMode.HOLD:
            return (
                f"Price in deadband (${d.general_price:.2f}/kWh), "
                "preserving battery for solar/cheap prices"
            )
        if new_mode == BatteryMode.HOLDING_FOR_SPIKE:
            return (
                "Spike forecast within lookahead -- "
                "holding battery for discharge"
            )
        if new_mode == BatteryMode.SOLAR_EXPORT_HOLD:
            return (
                f"Solar export hold -- FIT ${d.feed_in_price:.2f}/kWh "
                f"(avg ${d.solar_weighted_avg_fit:.2f}/kWh)"
            )
        if new_mode == BatteryMode.SELF_CONSUMPTION:
            if old_mode == BatteryMode.SOLAR_EXPORT_HOLD:
                return (
                    "Solar export hold released -- "
                    "FIT dropped or surplus insufficient"
                )
            if old_mode in (
                BatteryMode.GRID_CHARGING,
                BatteryMode.BOOST_CHARGING,
            ):
                return (
                    f"Charging ended -- price ${d.general_price:.2f}/kWh "
                    f"(above ${d.cheap_charge_stop_price:.2f}/kWh)"
                )
            if old_mode == BatteryMode.SPIKE_DISCHARGE:
                return "Price spike cleared"
            if old_mode in (BatteryMode.HOLD, BatteryMode.HOLDING_FOR_SPIKE):
                return (
                    f"Hold ended -- price ${d.general_price:.2f}/kWh, "
                    "using battery"
                )
            if old_mode == BatteryMode.DEMAND_BLOCK:
                return "Demand window ended"
            return "Normal operation -- no special conditions active"
        if new_mode == BatteryMode.MANUAL:
            return "Automation disabled or manual override"
        return f"Mode changed: {old_mode.value} -> {new_mode.value}"

    # ------------------------------------------------------------------
    # State machine — evaluate and execute transitions
    # ------------------------------------------------------------------

    async def async_evaluate_state_machine(self) -> None:
        """Compare desired mode with commanded mode and execute transitions.

        Called after ``_compute_derived_values()`` on every state change and
        periodic tick.  Handles debounce, command issuance, flag management,
        and notifications.
        """
        async with self._evaluate_lock:
            d = self.data
            now = dt_util.now()
            desired = d.active_mode

            # --- Startup grace period (30 s) ---
            if self._startup_grace_until is not None:
                if now < self._startup_grace_until:
                    _LOGGER.debug(
                        "State machine in startup grace period, skipping"
                    )
                    return
                self._startup_grace_until = None
                self._commanded_mode = self._infer_current_hardware_mode()
                _LOGGER.info(
                    "Startup grace ended, inferred mode: %s",
                    self._commanded_mode.value,
                )

            # --- Automation disabled ---
            if not self.get_switch_state(SWITCH_AUTOMATION_ENABLED):
                self._commanded_mode = BatteryMode.MANUAL
                self._mode_desired_since.clear()
                return

            # --- No change needed ---
            if desired == self._commanded_mode:
                self._mode_desired_since.clear()
                return

            # --- Debounce tracking ---
            debounce = self._get_debounce_for_transition(
                self._commanded_mode, desired
            )

            if desired not in self._mode_desired_since:
                # First time this mode is desired — start the timer
                self._mode_desired_since.clear()
                self._mode_desired_since[desired] = now
                if debounce > timedelta(0):
                    _LOGGER.debug(
                        "Mode %s desired, debounce %s starts now",
                        desired.value,
                        debounce,
                    )
                    return

            desired_since = self._mode_desired_since[desired]
            elapsed = now - desired_since

            if elapsed < debounce:
                _LOGGER.debug(
                    "Mode %s desired for %s, need %s — waiting",
                    desired.value,
                    elapsed,
                    debounce,
                )
                return

            # --- Debounce satisfied — execute transition ---
            old_mode = self._commanded_mode
            _LOGGER.info(
                "State machine transition: %s → %s (desired for %s)",
                old_mode.value,
                desired.value,
                elapsed,
            )

            await self._execute_mode_transition(desired)
            self._commanded_mode = desired
            self._mode_desired_since.clear()

            # Send notification
            await self._send_transition_notification(old_mode, desired)

    async def _execute_mode_transition(self, target: BatteryMode) -> None:
        """Issue battery commands and set state flags for *target* mode."""
        d = self.data

        # Set flag to prevent re-evaluation during mode transition
        # This prevents feedback loops when we programmatically change entities
        self._in_mode_transition = True

        try:
            if target == BatteryMode.SELF_CONSUMPTION:
                await self.async_set_self_consumption()

            elif target == BatteryMode.DEMAND_BLOCK:
                # Demand block is self_consumption with extra protection
                await self.async_set_self_consumption()

            elif target == BatteryMode.HOLD:
                d.solar_export_hold = False
                await self.async_set_hold()

            elif target == BatteryMode.SOLAR_EXPORT_HOLD:
                d.solar_export_hold = True
                await self.async_set_hold()

            elif target == BatteryMode.HOLDING_FOR_SPIKE:
                d.solar_export_hold = False
                await self.async_set_hold()

            elif target == BatteryMode.GRID_CHARGING:
                await self.async_set_force_charge()

            elif target == BatteryMode.BOOST_CHARGING:
                await self.async_set_boost_charge()

            elif target == BatteryMode.SPIKE_DISCHARGE:
                await self.async_set_force_discharge()

            elif target == BatteryMode.MANUAL:
                pass  # No command — user is controlling manually
        finally:
            # Always clear the flag, even if an exception occurs
            self._in_mode_transition = False

    async def _send_transition_notification(
        self, old_mode: BatteryMode, new_mode: BatteryMode
    ) -> None:
        """Send a notification for a mode transition."""
        d = self.data
        prefix = "Powerwall: "

        if new_mode == BatteryMode.SPIKE_DISCHARGE:
            title = f"{prefix}Price Spike!"
            message = (
                f"Price spike detected. Feed-in: ${d.feed_in_price:.2f}/kWh. "
                f"Battery at {d.soc:.0f}%. "
                f"Switching to force discharge (export)."
            )
        elif new_mode == BatteryMode.DEMAND_BLOCK:
            dw_start = self.get_option(
                CONF_DEMAND_WINDOW_START, DEFAULT_DEMAND_WINDOW_START
            )
            dw_end = self.get_option(
                CONF_DEMAND_WINDOW_END, DEFAULT_DEMAND_WINDOW_END
            )
            title = f"{prefix}Demand Window Active"
            message = (
                f"Demand window started ({dw_start}–{dw_end}). "
                f"Grid imports blocked. Battery at {d.soc:.0f}%."
            )
        elif new_mode == BatteryMode.GRID_CHARGING:
            title = f"{prefix}Cheap Grid Charging"
            message = (
                f"Grid price is ${d.general_price:.2f}/kWh "
                f"(below threshold ${d.effective_cheap_price:.2f}/kWh). "
                f"Battery at {d.soc:.0f}%. Charging from grid at ~3.3kW."
            )
        elif new_mode == BatteryMode.BOOST_CHARGING:
            net_solar = d.solar_battery_forecast.get("net_solar_kwh", 0)
            target_pct = self.get_option(
                CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET
            )
            title = f"{prefix}Boost Charging (5kW)"
            message = (
                f"Grid price is ${d.general_price:.2f}/kWh "
                f"(below threshold ${d.effective_cheap_price:.2f}/kWh). "
                f"Battery at {d.soc:.0f}%, target {target_pct}%. "
                f"Solar forecast insufficient — boost charging at ~5kW. "
                f"Net solar: {net_solar}kWh before demand window."
            )
        elif new_mode == BatteryMode.HOLD:
            title = f"{prefix}Entering Hold"
            message = (
                f"Grid price at ${d.general_price:.2f}/kWh "
                f"(in deadband zone). Holding battery — house draws from "
                f"grid. Battery at {d.soc:.0f}%."
            )
        elif new_mode == BatteryMode.HOLDING_FOR_SPIKE:
            title = f"{prefix}Holding for Spike"
            message = (
                f"Spike forecast within lookahead window. "
                f"Holding battery for potential discharge. "
                f"Battery at {d.soc:.0f}%."
            )
        elif new_mode == BatteryMode.SOLAR_EXPORT_HOLD:
            title = f"{prefix}Solar Export Hold"
            message = (
                f"Holding battery to export solar at "
                f"${d.feed_in_price:.2f}/kWh "
                f"(above avg ${d.solar_weighted_avg_fit:.2f}/kWh). "
                f"Battery at {d.soc:.0f}%."
            )
        elif new_mode == BatteryMode.SELF_CONSUMPTION:
            title, message = self._self_consumption_notification(old_mode)
        elif new_mode == BatteryMode.MANUAL:
            title = f"{prefix}Manual Override"
            message = "Automation disabled or manual override active."
        else:
            title = f"{prefix}Mode Change"
            message = (
                f"Mode changed: {old_mode.value} → {new_mode.value}"
            )

        await self.async_send_notification(title, message)

    def _self_consumption_notification(
        self, old_mode: BatteryMode
    ) -> tuple[str, str]:
        """Generate notification text for returning to self consumption."""
        d = self.data
        prefix = "Powerwall: "

        if old_mode == BatteryMode.SPIKE_DISCHARGE:
            return (
                f"{prefix}Spike Ended",
                f"Price spike has cleared. Feed-in now: "
                f"${d.feed_in_price:.2f}/kWh. "
                f"Battery at {d.soc:.0f}%. "
                f"Returning to self consumption.",
            )
        if old_mode in (
            BatteryMode.GRID_CHARGING,
            BatteryMode.BOOST_CHARGING,
        ):
            return (
                f"{prefix}Charging Stopped",
                f"Grid price rose to ${d.general_price:.2f}/kWh "
                f"(above stop threshold "
                f"${d.cheap_charge_stop_price:.2f}/kWh). "
                f"Battery at {d.soc:.0f}%. "
                f"Returning to self consumption.",
            )
        if old_mode in (
            BatteryMode.HOLD,
            BatteryMode.HOLDING_FOR_SPIKE,
        ):
            return (
                f"{prefix}Leaving Hold",
                f"Grid price rose to ${d.general_price:.2f}/kWh "
                f"(above stop threshold "
                f"${d.cheap_charge_stop_price:.2f}/kWh). "
                f"Battery at {d.soc:.0f}%. "
                f"Returning to self consumption.",
            )
        if old_mode == BatteryMode.SOLAR_EXPORT_HOLD:
            return (
                f"{prefix}Solar Export Hold Released",
                f"FIT now ${d.feed_in_price:.2f}/kWh "
                f"(avg ${d.solar_weighted_avg_fit:.2f}/kWh). "
                f"Resuming self consumption to charge battery. "
                f"Battery at {d.soc:.0f}%.",
            )
        if old_mode == BatteryMode.DEMAND_BLOCK:
            return (
                f"{prefix}Demand Window Ended",
                f"Demand window ended. Battery at {d.soc:.0f}%. "
                f"Returning to normal automation.",
            )
        return (
            f"{prefix}Self Consumption",
            f"Returning to self consumption. "
            f"Battery at {d.soc:.0f}%.",
        )

    # ------------------------------------------------------------------
    # Battery commands
    # ------------------------------------------------------------------

    async def _set_export_mode(self, mode: str) -> None:
        """Set the Teslemetry allow_export mode (pv_only or battery_ok)."""
        await self.hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": self._get_entity_id(
                    CONF_TESLEMETRY_ALLOW_EXPORT
                ),
                "option": mode,
            },
        )

    async def _set_operation_mode(self, mode: str) -> None:
        """Set the Teslemetry operation mode."""
        await self.hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": self._get_entity_id(
                    CONF_TESLEMETRY_OPERATION_MODE
                ),
                "option": mode,
            },
        )

    async def _set_backup_reserve(self, value: int | float) -> None:
        """Set the Teslemetry backup reserve percentage."""
        await self.hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": self._get_entity_id(
                    CONF_TESLEMETRY_BACKUP_RESERVE
                ),
                "value": value,
            },
        )

    async def async_set_self_consumption(self) -> None:
        """Set battery to self consumption mode (reserve=10, self_consumption)."""
        d = self.data
        d.manual_override = False
        d.hold_mode = False
        d.solar_export_hold = False

        if self.get_switch_state(SWITCH_DRY_RUN):
            _LOGGER.info("[DRY RUN] Would set self_consumption, reserve=10, allow_export=pv_only")
            return

        # Set allow_export to pv_only first (don't allow battery to export)
        await self._set_export_mode(TESLEMETRY_EXPORT_PV_ONLY)
        await asyncio.sleep(5)
        await self._set_operation_mode("self_consumption")
        await asyncio.sleep(5)
        await self._set_backup_reserve(10)

    async def async_set_hold(self) -> None:
        """Set battery to hold mode (reserve=floor(SOC), self_consumption)."""
        d = self.data
        d.hold_mode = True
        reserve = max(10, min(100, math.floor(d.soc)))

        if self.get_switch_state(SWITCH_DRY_RUN):
            _LOGGER.info("[DRY RUN] Would set hold, reserve=%d, allow_export=pv_only", reserve)
            return

        # Set allow_export to pv_only first (don't allow battery to export)
        await self._set_export_mode(TESLEMETRY_EXPORT_PV_ONLY)
        await asyncio.sleep(5)
        await self._set_backup_reserve(reserve)
        await asyncio.sleep(5)
        await self._set_operation_mode("self_consumption")

    async def async_set_force_charge(self) -> None:
        """Set battery to force charge mode (backup)."""
        d = self.data
        d.hold_mode = False
        d.solar_export_hold = False

        if self.get_switch_state(SWITCH_DRY_RUN):
            _LOGGER.info("[DRY RUN] Would set force charge (backup), allow_export=pv_only")
            return

        # Set allow_export to pv_only first (don't allow battery to export)
        await self._set_export_mode(TESLEMETRY_EXPORT_PV_ONLY)
        await asyncio.sleep(5)
        await self._set_operation_mode("backup")

    async def async_set_boost_charge(self) -> None:
        """Set battery to boost charge mode (autonomous, reserve=100)."""
        d = self.data
        d.hold_mode = False
        d.solar_export_hold = False

        if self.get_switch_state(SWITCH_DRY_RUN):
            _LOGGER.info(
                "[DRY RUN] Would set boost charge (autonomous, reserve=100), allow_export=pv_only"
            )
            return

        # Set allow_export to pv_only first (don't allow battery to export)
        await self._set_export_mode(TESLEMETRY_EXPORT_PV_ONLY)
        await asyncio.sleep(5)
        await self._set_backup_reserve(100)
        await asyncio.sleep(5)
        await self._set_operation_mode("autonomous")

    async def async_set_force_discharge(self) -> None:
        """Set battery to force discharge mode (autonomous, reserve=10).

        Relies on the Tesla Energy Plan dummy tariff (high sell price
        6am-midnight) to incentivise the Powerwall to export to grid.
        """
        d = self.data
        d.hold_mode = False
        d.solar_export_hold = False

        if self.get_switch_state(SWITCH_DRY_RUN):
            _LOGGER.info(
                "[DRY RUN] Would set force discharge (autonomous, reserve=10), allow_export=battery_ok"
            )
            return

        # Set allow_export to battery_ok first (allow battery to export to grid)
        await self._set_export_mode(TESLEMETRY_EXPORT_BATTERY_OK)
        await asyncio.sleep(5)
        await self._set_backup_reserve(10)
        await asyncio.sleep(5)
        await self._set_operation_mode("autonomous")

    async def async_send_notification(self, title: str, message: str) -> None:
        """Send a notification via the configured notify service."""
        service_target = self._get_entity_id(CONF_NOTIFY_SERVICE)
        # Parse "notify.mobile_app_xxx" into domain="notify", service="mobile_app_xxx"
        parts = service_target.split(".", 1)
        if len(parts) == 2:
            await self.hass.services.async_call(
                parts[0],
                parts[1],
                {"title": title, "message": message},
            )
