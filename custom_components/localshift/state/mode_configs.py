"""Mode configuration for battery mode transitions."""

from __future__ import annotations

from dataclasses import dataclass

from ..const import BatteryMode


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
