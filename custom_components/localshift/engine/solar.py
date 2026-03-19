"""Solar projection helpers for the DP optimizer."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    SlotContext,
)


def can_solar_reach_target(
    inputs: OptimizerInputs,
    slots: list[SlotContext],
    config: OptimizerConfig,
    demand_bounds: dict[str, int | None],
) -> bool:
    """Check if solar can reach target during demand window."""
    from custom_components.localshift.engine.dp_math import (
        _simulate_max_soc_in_demand_window,
    )

    demand_window_entry_idx = demand_bounds["entry_idx"]

    if not config.allow_dw_entry_under_target or demand_window_entry_idx is None:
        return False

    max_soc_in_dw = _simulate_max_soc_in_demand_window(
        inputs.initial_soc_pct, slots, config, demand_bounds
    )
    return max_soc_in_dw >= config.demand_window_target_soc_pct


def projected_solar_soc_gain_pct(
    slot_idx: int,
    slots: list[SlotContext],
    terminal_penalty_idx: int,
    battery_capacity_kwh: float,
) -> float:
    """
    Estimate the net SOC gain (%) achievable from solar between slot_idx
    (inclusive) and terminal_penalty_idx (exclusive), after subtracting
    household consumption.

    A positive return value means solar surplus exceeds consumption over the
    window; negative means consumption exceeds solar (net grid draw expected).
    """
    projected_net_kwh = sum(
        s.solar_kwh - s.consumption_kwh for s in slots[slot_idx:terminal_penalty_idx]
    )
    return (projected_net_kwh / battery_capacity_kwh) * 100.0


def projected_solcast_gain_pct(
    all_solcast: list[dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
    battery_capacity_kwh: float,
    avg_load_kw: float = 0.5,
) -> float:
    """Estimate net SOC gain (%) from solar in Solcast beyond the DP horizon.

    Calculates sum(solar - consumption) for the window [start_time, end_time).
    Solar comes from pv_estimate in all_solcast; consumption is estimated
    using avg_load_kw.
    """
    if end_time <= start_time:
        return 0.0

    solar_kwh = 0.0
    for period in all_solcast:
        p_start_str = period.get("period_start")
        if not p_start_str:
            continue
        try:
            p_start = datetime.fromisoformat(str(p_start_str))
            # Solcast periods are typically 30 mins
            if start_time <= p_start < end_time:
                solar_kwh += float(period.get("pv_estimate", 0)) * 0.5
        except (ValueError, TypeError):
            continue

    hours = (end_time - start_time).total_seconds() / 3600.0
    consumption_kwh = avg_load_kw * hours

    net_kwh = max(0.0, solar_kwh - consumption_kwh)
    return (net_kwh / battery_capacity_kwh) * 100.0


def get_forecast_accuracy(
    solar_accuracy_tracker: Any | None,
) -> float:
    """Get overall forecast accuracy from tracker.

    Returns:
        float: Accuracy as decimal (0.0 to 1.0), or 1.0 if unavailable/invalid
    """
    if solar_accuracy_tracker is None:
        return 1.0

    try:
        accuracy_pct = solar_accuracy_tracker.metrics.accuracy
    except AttributeError:
        return 1.0

    if accuracy_pct is None or accuracy_pct <= 0:
        return 1.0

    return accuracy_pct / 100.0
