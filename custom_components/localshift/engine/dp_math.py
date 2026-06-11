"""dp_math.py — Mathematical utilities for DP optimizer.

SOC grid construction, bin mapping, cost interpolation, and solar simulation.
"""

from custom_components.localshift.engine.types import OptimizerConfig, SlotContext

# --- Deficit-aware urgency window (Issue #800 follow-up / 2026-06-11 incident) ---
URGENCY_WINDOW_MIN_HOURS = 4.0
"""Floor for the urgency window (the pre-#559-creep fixed value).

The #559 stability anchor: never widen *below* this, so near-target plans keep the
narrow 4h urgency ramp that stopped urgency creep from charging at marginal prices."""

URGENCY_WINDOW_MAX_HOURS = 8.0
"""Cap for the urgency window (the pre-#559 value).

Bounds urgency creep and #800 overnight-sawtooth exposure: a pathological deficit can
never widen the window past 8h, so slots more than 8h before the demand window always
gate on the un-inflated base price."""

URGENCY_CHARGE_MARGIN_HOURS = 0.5
"""Safety margin added to the bare charge-time estimate.

Mirrors the ``boost_needed`` margin in ``forecast/pipeline.py`` so the window opens
slightly before the charge would have to start flat-out to reach target in time."""


def urgency_window_hours(
    soc_pct: float,
    target_pct: float,
    battery_capacity_kwh: float,
    charge_rate_kw: float,
    charge_efficiency: float,
    *,
    min_hours: float = URGENCY_WINDOW_MIN_HOURS,
    max_hours: float = URGENCY_WINDOW_MAX_HOURS,
    margin_hours: float = URGENCY_CHARGE_MARGIN_HOURS,
) -> float:
    """Hours before the demand window over which urgency pre-charge is legitimate.

    The window is sized to the SOC deficit: how long flat-out normal-rate grid charging
    would take to close the gap to target, plus a small margin, clamped to ``[min_hours,
    max_hours]``. A fixed 4h window (the previous behaviour) blocked morning charging when
    a deep deficit needed more runway than 4h (2026-06-11 incident: 11.6% -> 95% needs
    ~4.2h). The floor preserves the #559 anti-creep behaviour for near-target plans; the
    cap bounds #800 overnight-sawtooth exposure.

    Mirrors the rate-aware charge-time math at ``forecast/pipeline.py`` (``deficit /
    (rate * efficiency)``). Degenerate rate/efficiency/deficit collapses to ``min_hours``.
    """
    deficit_kwh = max(0.0, (target_pct - soc_pct) / 100.0 * battery_capacity_kwh)
    rate = charge_rate_kw * charge_efficiency
    if rate <= 0.0 or deficit_kwh <= 0.0:
        return min_hours
    hours = deficit_kwh / rate + margin_hours
    return max(min_hours, min(max_hours, hours))


def urgency_ramp_price(
    base_price: float,
    max_price: float,
    hours_to_dw: float,
    window_hours: float,
) -> float:
    """Charge-price threshold at a moment ``hours_to_dw`` before the demand window.

    The single source of truth for the urgency ramp: the threshold rises linearly from
    ``base_price`` at the window's outer edge to ``max_price`` at the demand-window entry.
    ``price_calculator._calculate_urgency_adjusted_price`` uses it for the live "now"
    threshold and ``constraints.compute_pre_dw_charge_thresholds`` evaluates it at each
    future slot's own time, so the plan the DP produces in the morning agrees with what
    the live controller will be willing to pay when those slots arrive (the
    time-inconsistency behind the 2026-06-12 undercharge: a now-scalar threshold gated
    afternoon slots a morning plan could never use).

    Outside the window (``hours_to_dw >= window_hours``) the ramp contributes nothing
    (returns ``base_price``); a degenerate window or an inverted ``max_price < base_price``
    also collapses to ``base_price`` so a misconfigured ceiling can never *tighten* the gate.
    """
    if window_hours <= 0.0 or max_price <= base_price:
        return base_price
    urgency = max(min(1.0 - (hours_to_dw / window_hours), 1.0), 0.0)
    return base_price + (max_price - base_price) * urgency


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
