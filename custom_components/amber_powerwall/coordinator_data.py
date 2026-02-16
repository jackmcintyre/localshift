"""Coordinator data structures for the Amber Powerwall integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .const import BatteryMode


@dataclass
class ChargingDecision:
    """Represents a charging decision for a specific time slot.

    This is the shared decision structure used by both the forecast
    simulation and the active mode logic to ensure consistency.
    """

    slot_start: datetime
    should_grid_charge: bool = False
    should_boost: bool = False
    charge_amount_kwh: float = 0.0
    price_per_kwh: float = 0.0
    reason: str = ""


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
    allow_export: str = "unknown"

    # Computed binary sensors
    forecast_spike_within_window: bool = False
    force_discharge_active: bool = False
    force_charge_active: bool = False
    boost_charge_active: bool = False
    hold_active: bool = False
    proactive_export_active: bool = False
    forecast_expensive_period_coming: bool = False
    solar_can_reach_target: bool = False
    boost_charge_needed: bool = False
    demand_window_active: bool = False

    # Extra attributes for binary sensors
    max_forecast_price: float = 0.0
    max_buy_forecast_price: float = 0.0  # Max buy price (general_forecast) for display
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
    forecast_history: list[dict[str, Any]] = field(default_factory=list)
    daily_forecast: list[dict[str, Any]] = field(default_factory=list)
    daily_forecast_hourly: list[dict[str, Any]] = field(default_factory=list)
    daily_forecast_soc_15min: list[list[Any]] = field(default_factory=list)
    consumption_source: str = "unknown"
    consumption_profile_hours: int = 0
    consumption_fallback_hours: int = 0
    consumption_statistic_id: str = ""
    consumption_hourly_sample_counts: dict[int, int] = field(default_factory=dict)
    consumption_hourly_profile_kw: dict[int, float] = field(default_factory=dict)
    forecast_consumption_source_counts: dict[str, int] = field(default_factory=dict)
    recent_load_1hr_kw: float = 0.0
    recent_load_1hr_statistic_id: str = ""
    recent_load_1hr_samples: int = 0
    recent_load_1hr_last_error: str = ""
    consumption_weighting: float = 0.67

    # Cost accumulators (Phase 4)
    grid_import_cost: float = 0.0
    grid_export_revenue: float = 0.0
    battery_savings: float = 0.0
    battery_charge_cost: float = 0.0

    # Internal state flags (managed by state machine / buttons)
    manual_override: bool = False
    target_reached_today: bool = False

    # Shared charging decisions (computed once, used by both forecast and active_mode)
    forecast_charging_decisions: list[ChargingDecision] = field(default_factory=list)
    charging_needed_before_dw: float = 0.0  # Total kWh needed before DW
    optimal_charge_start: datetime | None = None  # Earliest optimal charging slot
    optimal_charge_end: datetime | None = None  # Latest optimal charging slot
