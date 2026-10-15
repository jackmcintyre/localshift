"""Solar projection helpers for the DP optimizer."""

from __future__ import annotations

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
    """Check if solar can reach target during demand window.

    This function checks if solar alone can reach target at any point during
    the demand window (when allow_dw_entry_under_target is True).
    """
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


def can_solar_reach_target_feasible(
    inputs: OptimizerInputs,
    slots: list[SlotContext],
    config: OptimizerConfig,
    terminal_penalty_idx: int | None,
) -> bool:
    """Check if solar alone can reach target on entering the terminal penalty slot.

    This function checks pure solar feasibility without the allow_dw_entry_under_target
    gate. It returns True if solar alone can reach the target SOC entering the
    terminal penalty slot (typically DW entry). Like ``check_global_solar_sufficiency``
    since #1141, the entry slot's own solar does not count (#1185).

    Args:
        inputs: Optimizer inputs
        slots: Slot contexts
        config: Optimizer config
        terminal_penalty_idx: Terminal penalty index (typically DW entry)

    Returns:
        True if solar alone can reach target entering the terminal penalty slot
    """
    from custom_components.localshift.engine.dp_math import (
        _simulate_solar_only_terminal_soc,
    )

    terminal_soc = _simulate_solar_only_terminal_soc(
        initial_soc_pct=inputs.initial_soc_pct,
        slots=slots if terminal_penalty_idx is None else slots[:terminal_penalty_idx],
        terminal_penalty_idx=None,
        config=config,
    )
    return terminal_soc >= config.demand_window_target_soc_pct


def projected_solar_soc_gain_pct(
    *,
    slot_idx: int,
    slots: list[SlotContext],
    terminal_penalty_idx: int,
    battery_capacity_kwh: float,
    initial_soc_pct: float,
    config: OptimizerConfig,
) -> float:
    """Estimate realistic SOC gain (%) from solar before the terminal deadline."""
    from custom_components.localshift.engine.dp_math import (
        _simulate_solar_only_terminal_soc,
    )

    if terminal_penalty_idx <= slot_idx:
        return 0.0

    terminal_soc = _simulate_solar_only_terminal_soc(
        initial_soc_pct=initial_soc_pct,
        slots=slots[slot_idx:terminal_penalty_idx],
        terminal_penalty_idx=terminal_penalty_idx - slot_idx,
        config=config,
    )
    return max(0.0, terminal_soc - initial_soc_pct)


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


def forecast_accuracy_measured_as_zero(
    solar_accuracy_tracker: Any | None,
) -> bool:
    """True when the tracker has samples and they measure accuracy at zero.

    Tracker accuracy is ``max(0, 100 - MAPE)``, so a MAPE at or over 100 reports
    0. ``get_forecast_accuracy`` answers 1.0 for any non-positive figure, which
    is right for "nothing measured" and wrong for "measured, and it was the
    worst it can be". This tells the two apart for callers that must not read
    the worst measurement as full trust (Issue #1103, the negative-FIT
    recoverability floor). A zero with no samples behind it is not a
    measurement.
    """
    if solar_accuracy_tracker is None:
        return False

    try:
        metrics = solar_accuracy_tracker.metrics
        accuracy_pct = metrics.accuracy
        sample_count = metrics.sample_count
    except AttributeError:
        return False

    try:
        return sample_count > 0 and accuracy_pct is not None and accuracy_pct <= 0
    except TypeError:
        return False
