"""dp_math.py — Mathematical utilities for DP optimizer.

SOC grid construction, bin mapping, cost interpolation, and solar simulation.
"""

from custom_components.localshift.engine.types import OptimizerConfig, SlotContext


def _build_soc_grid(config: OptimizerConfig) -> list[float]:
    """Build SOC discretization grid from min_soc_pct to max_soc_pct."""
    if config.soc_bins <= 1:
        return [config.min_soc_pct]

    step = (config.max_soc_pct - config.min_soc_pct) / (config.soc_bins - 1)
    return [config.min_soc_pct + i * step for i in range(config.soc_bins)]


def _map_soc_to_bin(soc_pct: float, grid: list[float]) -> int:
    """Map a SOC percentage to the nearest bin index in the grid."""
    if not grid:
        return 0

    best_idx = 0
    best_dist = abs(soc_pct - grid[0])
    for i, soc in enumerate(grid[1:], start=1):
        dist = abs(soc_pct - soc)
        if dist < best_dist:
            best_dist = dist
            best_idx = i
    return best_idx


def _interpolate_cost_to_soc(
    soc_pct: float,
    soc_grid: list[float],
    cost_table: dict[int, float],
) -> float:
    """Interpolate cost from cost_table at nearest grid points to target SOC."""
    if not soc_grid or not cost_table:
        return float("inf")

    lower_idx = 0
    for i in range(len(soc_grid) - 1, -1, -1):
        if soc_grid[i] <= soc_pct and i in cost_table:
            lower_idx = i
            break
    else:
        lower_idx = min(cost_table.keys())

    upper_idx = len(soc_grid) - 1
    for i in range(len(soc_grid)):
        if soc_grid[i] >= soc_pct and i in cost_table:
            upper_idx = i
            break
    else:
        upper_idx = max(cost_table.keys())

    if lower_idx == upper_idx:
        return cost_table.get(lower_idx, float("inf"))

    lower_soc = soc_grid[lower_idx]
    upper_soc = soc_grid[upper_idx]
    lower_cost = cost_table.get(lower_idx, float("inf"))
    upper_cost = cost_table.get(upper_idx, float("inf"))

    if upper_soc == lower_soc:
        return lower_cost

    ratio = (soc_pct - lower_soc) / (upper_soc - lower_soc)
    return lower_cost + ratio * (upper_cost - lower_cost)


def _simulate_solar_only_terminal_soc(
    initial_soc_pct: float,
    slots: list[SlotContext],
    terminal_penalty_idx: int | None,
    config: OptimizerConfig,
) -> float:
    """Fast solar-only SOC simulation."""
    soc = initial_soc_pct
    for i, slot in enumerate(slots):
        net_kwh = slot.solar_kwh - slot.consumption_kwh
        slot_hours = slot.slot_interval_minutes / 60.0
        max_slot_transfer_kwh = config.solar_charge_rate_kw * slot_hours
        if net_kwh >= 0:
            delta = min(net_kwh, max_slot_transfer_kwh) * config.charge_efficiency
        else:
            delta = max(net_kwh, -max_slot_transfer_kwh) / config.discharge_efficiency
        soc += delta / config.battery_capacity_kwh * 100
        soc = max(config.min_soc_pct, min(100.0, soc))
        if terminal_penalty_idx is not None and i == terminal_penalty_idx:
            return soc
    return soc


def _simulate_max_soc_in_demand_window(
    initial_soc_pct: float,
    slots: list[SlotContext],
    config: OptimizerConfig,
    demand_bounds: dict[str, int | None] | None = None,
) -> float:
    """Simulate solar-only SOC and return max SOC within demand window slots."""
    soc = initial_soc_pct
    max_soc_in_dw = soc

    entry_idx: int | None = None
    end_idx: int | None = None
    if demand_bounds is not None:
        entry_idx = demand_bounds.get("entry_idx")
        end_idx = demand_bounds.get("end_idx")

    for i, slot in enumerate(slots):
        net_kwh = slot.solar_kwh - slot.consumption_kwh
        slot_hours = slot.slot_interval_minutes / 60.0
        max_transfer_kwh = config.solar_charge_rate_kw * slot_hours

        if net_kwh >= 0:
            delta = min(net_kwh, max_transfer_kwh) * config.charge_efficiency
        else:
            delta = max(net_kwh, -max_transfer_kwh) / config.discharge_efficiency

        soc += delta / config.battery_capacity_kwh * 100
        soc = max(config.min_soc_pct, min(100.0, soc))

        if slot.is_demand_window_slot:
            in_bounds = True
            if entry_idx is not None and end_idx is not None:
                in_bounds = entry_idx <= i <= end_idx
            if in_bounds:
                max_soc_in_dw = max(max_soc_in_dw, soc)

    return max_soc_in_dw
