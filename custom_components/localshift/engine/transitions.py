"""Transitions — battery state transitions for each optimizer action."""

from __future__ import annotations

from custom_components.localshift.engine.types import (
    OptimizerConfig,
    PlannerAction,
    SlotContext,
)


def transition(
    soc_pct: float,
    action: PlannerAction,
    slot: SlotContext,
    config: OptimizerConfig,
) -> tuple[float, float, float]:
    """Compute next SOC, grid_import_kwh, grid_export_kwh for a given action.

    All actions account for solar generation and household consumption.

    Returns:
        (next_soc_pct, grid_import_kwh, grid_export_kwh)

    """
    if action == PlannerAction.HOLD:
        return _transition_hold(soc_pct, slot, config)
    if action == PlannerAction.CHARGE_GRID_NORMAL:
        charge_rate_kw = (
            config.charge_rate_curve.rate_at_soc(soc_pct)
            if config.charge_rate_curve is not None
            else config.charge_rate_kw
        )
        return _transition_charge_grid(soc_pct, slot, config, charge_rate_kw)
    if action == PlannerAction.CHARGE_GRID_BOOST:
        boost_rate_kw = (
            config.boost_charge_rate_curve.rate_at_soc(soc_pct)
            if config.boost_charge_rate_curve is not None
            else config.boost_charge_rate_kw
        )
        return _transition_charge_grid(soc_pct, slot, config, boost_rate_kw)
    if action == PlannerAction.EXPORT_PROACTIVE:
        return _transition_export(soc_pct, slot, config)
    return soc_pct, 0.0, 0.0


def _transition_hold(
    soc_pct: float, slot: SlotContext, config: OptimizerConfig
) -> tuple[float, float, float]:
    """Compute transition for HOLD action.

    Returns:
        (next_soc, grid_import, grid_export)

    """
    slot_hours = slot.slot_interval_minutes / 60.0
    net_kwh = slot.solar_kwh - slot.consumption_kwh
    capacity_kwh = config.battery_capacity_kwh

    if net_kwh >= 0:
        return _transition_hold_surplus(
            soc_pct, net_kwh, slot_hours, config, capacity_kwh
        )
    return _transition_hold_deficit(soc_pct, net_kwh, slot_hours, config, capacity_kwh)


def _transition_hold_surplus(
    soc_pct: float,
    net_kwh: float,
    slot_hours: float,
    config: OptimizerConfig,
    capacity_kwh: float,
) -> tuple[float, float, float]:
    """Handle HOLD with solar surplus."""
    limit_kwh = config.solar_charge_rate_kw * slot_hours
    solar_surplus_kwh = net_kwh
    solar_by_rate_kwh = min(solar_surplus_kwh, limit_kwh)
    headroom_kwh = max(0.0, (config.max_soc_pct - soc_pct) / 100.0 * capacity_kwh)

    if config.charge_efficiency <= 0:
        solar_to_battery_kwh = 0.0
    else:
        solar_by_soc_kwh = headroom_kwh / config.charge_efficiency
        solar_to_battery_kwh = min(solar_by_rate_kwh, solar_by_soc_kwh)

    stored_kwh = solar_to_battery_kwh * config.charge_efficiency
    delta_soc = (stored_kwh / capacity_kwh) * 100.0
    next_soc = soc_pct + delta_soc
    grid_export_kwh = max(0.0, solar_surplus_kwh - solar_to_battery_kwh)
    return next_soc, 0.0, grid_export_kwh


def _transition_hold_deficit(
    soc_pct: float,
    net_kwh: float,
    slot_hours: float,
    config: OptimizerConfig,
    capacity_kwh: float,
) -> tuple[float, float, float]:
    """Handle HOLD with load deficit.

    Issue #559 Root Cause 3: when config.hold_soc is True, strictly preserve
    SOC by importing the entire load deficit from the grid (zero discharge).
    """
    limit_kwh = config.discharge_rate_kw * slot_hours
    load_deficit_kwh = -net_kwh

    # Issue #559: if hold_soc is enabled, meet entire deficit with grid import.
    if config.hold_soc:
        return soc_pct, load_deficit_kwh, 0.0

    discharge_by_rate_kwh = min(load_deficit_kwh, limit_kwh)
    available_battery_kwh = max(
        0.0, (soc_pct - config.min_soc_pct) / 100.0 * capacity_kwh
    )
    max_load_from_battery_kwh = available_battery_kwh * config.discharge_efficiency
    battery_to_load_kwh = min(discharge_by_rate_kwh, max_load_from_battery_kwh)

    if config.discharge_efficiency <= 0:
        battery_delta_kwh = 0.0
    else:
        battery_delta_kwh = -(battery_to_load_kwh / config.discharge_efficiency)

    delta_soc = (battery_delta_kwh / capacity_kwh) * 100.0
    next_soc = soc_pct + delta_soc
    grid_import_kwh = max(0.0, load_deficit_kwh - battery_to_load_kwh)
    return next_soc, grid_import_kwh, 0.0


def _transition_charge_grid(
    soc_pct: float,
    slot: SlotContext,
    config: OptimizerConfig,
    charge_rate_kw: float,
) -> tuple[float, float, float]:
    """Compute transition for CHARGE_GRID actions.

    Returns:
        (next_soc, grid_import, grid_export)

    """
    slot_hours = slot.slot_interval_minutes / 60.0
    net_kwh = slot.solar_kwh - slot.consumption_kwh
    capacity_kwh = config.battery_capacity_kwh
    max_charge_kwh = charge_rate_kw * slot_hours
    effective_charge_kwh = max_charge_kwh * config.charge_efficiency

    if net_kwh > 0:
        next_soc, grid_import = _charge_grid_with_solar(
            soc_pct, net_kwh, effective_charge_kwh, capacity_kwh, config
        )
    else:
        next_soc, grid_import = _charge_grid_with_deficit(
            soc_pct, net_kwh, max_charge_kwh, effective_charge_kwh, capacity_kwh
        )

    if next_soc > config.max_soc_pct:
        return _clip_charge_to_max_soc(soc_pct, net_kwh, next_soc, capacity_kwh, config)

    return next_soc, grid_import, 0.0


def _charge_grid_with_solar(
    soc_pct: float,
    net_kwh: float,
    effective_charge_kwh: float,
    capacity_kwh: float,
    config: OptimizerConfig,
) -> tuple[float, float]:
    """Calculate grid charge with solar surplus."""
    solar_to_battery = net_kwh * config.charge_efficiency
    soc_from_solar = (solar_to_battery / capacity_kwh) * 100.0
    remaining_headroom = config.max_soc_pct - soc_pct - soc_from_solar

    if remaining_headroom > 0:
        grid_charge_stored_kwh = min(
            effective_charge_kwh, (remaining_headroom / 100.0) * capacity_kwh
        )
    else:
        grid_charge_stored_kwh = 0.0

    grid_import_kwh = grid_charge_stored_kwh / config.charge_efficiency
    delta_soc_from_grid = grid_charge_stored_kwh / capacity_kwh * 100.0
    delta_soc_from_solar = solar_to_battery / capacity_kwh * 100.0
    next_soc = soc_pct + delta_soc_from_grid + delta_soc_from_solar
    return next_soc, grid_import_kwh


def _charge_grid_with_deficit(
    soc_pct: float,
    net_kwh: float,
    max_charge_kwh: float,
    effective_charge_kwh: float,
    capacity_kwh: float,
) -> tuple[float, float]:
    """Calculate grid charge with consumption deficit."""
    grid_charge_stored_kwh = effective_charge_kwh
    grid_import_kwh = max_charge_kwh + (-net_kwh)
    delta_soc = (grid_charge_stored_kwh / capacity_kwh) * 100.0
    next_soc = soc_pct + delta_soc
    return next_soc, grid_import_kwh


def _clip_charge_to_max_soc(
    soc_pct: float,
    net_kwh: float,
    next_soc: float,
    capacity_kwh: float,
    config: OptimizerConfig,
) -> tuple[float, float, float]:
    """Clip grid charging to hit max SOC exactly."""
    total_soc_needed = config.max_soc_pct - soc_pct
    solar_soc_contrib = 0.0
    if net_kwh > 0:
        solar_soc_contrib = (net_kwh * config.charge_efficiency / capacity_kwh) * 100.0
    grid_soc_needed = max(0.0, total_soc_needed - solar_soc_contrib)
    grid_import_for_charging = (
        grid_soc_needed / 100.0 * capacity_kwh
    ) / config.charge_efficiency
    grid_import_total = grid_import_for_charging
    if net_kwh < 0:
        grid_import_total += -net_kwh
    return config.max_soc_pct, grid_import_total, 0.0


def _transition_export(
    soc_pct: float, slot: SlotContext, config: OptimizerConfig
) -> tuple[float, float, float]:
    """Compute transition for EXPORT action.

    Returns:
        (next_soc, grid_import, grid_export)

    """
    slot_hours = slot.slot_interval_minutes / 60.0
    net_kwh = slot.solar_kwh - slot.consumption_kwh
    capacity_kwh = config.battery_capacity_kwh

    max_discharge_kwh = config.discharge_rate_kw * slot_hours
    available_kwh = max(0.0, (soc_pct - config.min_soc_pct) / 100.0 * capacity_kwh)
    battery_discharge_kwh = min(
        max_discharge_kwh, available_kwh * config.discharge_efficiency
    )

    if config.discharge_efficiency > 0:
        delta_soc = (
            -(battery_discharge_kwh / config.discharge_efficiency / capacity_kwh)
            * 100.0
        )
    else:
        delta_soc = 0.0

    next_soc = soc_pct + delta_soc

    if net_kwh > 0:
        grid_export_kwh = net_kwh + battery_discharge_kwh
        return next_soc, 0.0, grid_export_kwh

    grid_export_kwh = max(0.0, battery_discharge_kwh + net_kwh)
    return next_soc, 0.0, grid_export_kwh
