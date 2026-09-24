"""Mode configuration for battery mode transitions."""

from __future__ import annotations

from dataclasses import dataclass

from ..const import (
    AWAY_RESERVE_MIN,
    BACKUP_RESERVE_MAX_VALID,
    DEFAULT_AWAY_RESERVE,
    PROACTIVE_EXPORT_SOC_BUFFER_PERCENT,
    BatteryMode,
)


@dataclass
class ModeConfig:
    """Complete configuration for a mode transition.

    Contains all parameters needed for a mode, ensuring atomic updates
    to both Tesla hardware state and internal tracking state.
    """

    operation_mode: str
    backup_reserve: int | float
    export_mode: str
    grid_charging_allowed: bool
    self_consumption_reserve: float | None = None
    grid_charging_reserve: int | None = None
    proactive_export_reserve: float | None = None
    # Issue #972: reserve the SPIKE_DISCHARGE builder actually wrote, so the
    # health-check expectation matches the written value (conservative
    # spike_reserve_soc, or minimum_target_soc otherwise) instead of a
    # hardcoded 10.
    spike_discharge_reserve: float | None = None


def calculate_proactive_export_reserve(soc: float, minimum_target_soc: float) -> float:
    """Return the PROACTIVE_EXPORT backup reserve for *soc* (Issue #974).

    ``max(minimum_target_soc, soc - PROACTIVE_EXPORT_SOC_BUFFER_PERCENT)``.

    This is the ONE shared formula, called by both the state-machine builder
    (``_build_proactive_export_config``) and the actuator
    (``BatteryController.set_proactive_export``) so the planner's expectation
    and the hardware write can never drift apart again. The old absolute
    ``PROACTIVE_EXPORT_MIN_RESERVE_PERCENT`` (4) floor is redundant here: for
    any SOC in (minimum_target_soc, minimum_target_soc + 5) it produced a
    reserve below the configured floor the planner never models.
    """
    return max(minimum_target_soc, soc - PROACTIVE_EXPORT_SOC_BUFFER_PERCENT)


def resolve_away_reserve_pct(raw: object) -> float:
    """Clamp a raw away-reserve option value to a valid backup reserve.

    Clamps to ``[AWAY_RESERVE_MIN, BACKUP_RESERVE_MAX_VALID]`` (10-80) — the
    Tesla firmware silently resets 81-99 to 80 — and falls back to
    ``DEFAULT_AWAY_RESERVE`` on anything non-numeric, so a corrupted or
    missing option can never leave the away reserve unbounded or crash a mode
    builder that calls this every cycle (docs/holiday-away/plan.md item 4).
    """
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = float(DEFAULT_AWAY_RESERVE)
    return max(AWAY_RESERVE_MIN, min(BACKUP_RESERVE_MAX_VALID, value))


def calculate_self_consumption_reserve(
    preserve_soc: float | None, away_reserve_pct: float | None
) -> float:
    """Return the SELF_CONSUMPTION / DEMAND_BLOCK / HOLD backup reserve.

    ``max(away_reserve_pct, preserve_soc if not None else 10)`` while away
    (``away_reserve_pct`` is not None); otherwise today's value —
    ``preserve_soc`` when set, else 10 (docs/holiday-away/plan.md item 4).

    The one shared formula, called by both the state-machine builder
    (``_build_self_consumption_config``) and the actuator
    (``BatteryController.set_self_consumption``), mirroring how
    ``calculate_proactive_export_reserve`` (#974) keeps the planner's
    expectation and the hardware write from drifting apart. Clamps
    ``away_reserve_pct`` itself via ``resolve_away_reserve_pct``, so a caller
    may pass the raw option value straight through.
    """
    base = preserve_soc if preserve_soc is not None else 10.0
    if away_reserve_pct is None:
        return base
    return max(resolve_away_reserve_pct(away_reserve_pct), base)


MODE_CONFIG_BUILDERS: dict[BatteryMode, str] = {
    BatteryMode.SELF_CONSUMPTION: "_build_self_consumption_config",
    BatteryMode.DEMAND_BLOCK: "_build_self_consumption_config",
    BatteryMode.GRID_CHARGING: "_build_grid_charging_config",
    BatteryMode.BOOST_CHARGING: "_build_boost_charging_config",
    BatteryMode.SPIKE_DISCHARGE: "_build_spike_discharge_config",
    BatteryMode.PROACTIVE_EXPORT: "_build_proactive_export_config",
    BatteryMode.HOLD: "_build_hold_config",
}

MODE_EXECUTORS: dict[BatteryMode, str] = {
    BatteryMode.SELF_CONSUMPTION: "_execute_self_consumption_transition",
    BatteryMode.DEMAND_BLOCK: "_execute_self_consumption_transition",
    BatteryMode.GRID_CHARGING: "_execute_grid_charging_transition",
    BatteryMode.BOOST_CHARGING: "_execute_boost_charging_transition",
    BatteryMode.SPIKE_DISCHARGE: "_execute_spike_discharge_transition",
    BatteryMode.PROACTIVE_EXPORT: "_execute_proactive_export_transition",
    BatteryMode.HOLD: "_execute_hold_transition",
}
