"""Coordinator data structures for the Amber Powerwall integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .const import BatteryMode


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
