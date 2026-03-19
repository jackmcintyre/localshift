"""optimizer_dp.py — Backward compatibility shim.

This module re-exports all symbols from the refactored engine modules
for backward compatibility with existing code.

New code should import directly from:
- custom_components.localshift.engine.types
- custom_components.localshift.engine.core
"""

# Re-export all types
# Re-export constraint functions
from custom_components.localshift.engine.constraints import feasible_actions

# Re-export DPPlanner from core
from custom_components.localshift.engine.core import DPPlanner

# Re-export cost functions
from custom_components.localshift.engine.cost import stage_cost, terminal_cost

# Re-export transition function
from custom_components.localshift.engine.transitions import transition

# Re-export math functions from dp_math
from custom_components.localshift.engine.dp_math import (
    _build_soc_grid,
    _interpolate_cost_to_soc,
    _map_soc_to_bin,
    _simulate_max_soc_in_demand_window,
    _simulate_solar_only_terminal_soc,
)
from custom_components.localshift.engine.types import (
    ObjectiveTerms,
    OptimizerConfig,
    OptimizerInputs,
    OptimizerResult,
    PlannedSlotDecision,
    PlannerAction,
    PlannerReasonCode,
    SlotContext,
)

__all__ = [
    "DPPlanner",
    "feasible_actions",
    "stage_cost",
    "terminal_cost",
    "transition",
    "ObjectiveTerms",
    "OptimizerConfig",
    "OptimizerInputs",
    "OptimizerResult",
    "PlannerAction",
    "PlannerReasonCode",
    "PlannedSlotDecision",
    "SlotContext",
    "_build_soc_grid",
    "_interpolate_cost_to_soc",
    "_map_soc_to_bin",
    "_simulate_max_soc_in_demand_window",
    "_simulate_solar_only_terminal_soc",
]
