# LocalShift DP Optimizer - Agent Guidelines

## Overview

Dynamic Programming (DP) optimizer for battery charge/discharge scheduling. Solves a multi-horizon optimization problem to minimize electricity costs while respecting physical constraints.

## Core Pattern: Soft-Constrained DP

```
┌─────────────────────────────────────────────────────────────────┐
│  Hard Constraints (feasible_actions)  →  What CAN I do?         │
│  Soft Penalties (stage_cost)          →  What SHOULD I do?      │
│  Terminal Cost (terminal_cost)        →  What MUST I achieve?   │
└─────────────────────────────────────────────────────────────────┘
```

## Key Files

| File | Purpose | Lines |
|------|---------|-------|
| `optimizer_dp.py` | Core DP solver | ~865 |
| `optimizer_runner.py` | Slot-by-slot execution | ~750 |
| `optimizer_facade.py` | Public API | ~260 |
| `optimization_controller.py` | Integration glue | ~530 |
| `slots.py` | Slot data structures | ~420 |
| `slot_schedule.py` | Schedule generation | ~310 |
| `soc_simulator.py` | SOC projection | ~280 |
| `price_calculator.py` | Price threshold logic | ~420 |
| `parameters.py` | Parameter definitions | ~730 |
| `outcomes.py` | Outcome tracking | ~750 |
| `pattern_analyzer.py` | Pattern detection | ~520 |
| `pattern_types.py` | Pattern enums | ~190 |
| `spike_analyzer.py` | Price spike detection | ~150 |
| `excess_solar.py` | Excess solar handling | ~410 |
| `excess_solar_signals.py` | Solar signals | ~200 |
| `price_signal_engine.py` | Price signals | ~130 |

## Where to Look

| Task | Location |
|------|----------|
| Modify constraint logic | `optimizer_dp.py:feasible_actions()` (L1341) |
| Modify cost function | `optimizer_dp.py:stage_cost()` (L1721) |
| Modify terminal cost | `optimizer_dp.py:terminal_cost()` (L1818) |
| Add new action type | `parameters.py` + `optimizer_dp.py` |
| Change scheduling | `slot_schedule.py` |
| Modify price thresholds | `price_calculator.py` |
| Change SOC simulation | `soc_simulator.py` |

## CRITICAL: Planning Model Decision Guide

**MUST consult `docs/PLANNING_MODEL.md` before any changes.**

| Question | Answer Yes → |
|----------|-------------|
| Is it impossible/forbidden? | Add to `feasible_actions()` |
| Is it a requirement by deadline? | Add to `terminal_cost()` |
| Is it discouraged/preferred? | Add penalty to `stage_cost()` |

## Action Types

```python
class PlannerAction(Enum):
    SELF_CONSUMPTION = auto()
    GRID_CHARGE = auto()
    EXPORT_PROACTIVE = auto()
    HOLD = auto()
    EXPORT_FORCED = auto()  # Spike discharge
    IMPORT_BLOCKED = auto()  # Demand window
```

## Key Concepts

- **Slots**: 15-minute time intervals for planning
- **Horizon**: Planning window (typically 24-48 hours)
- **SOC trajectory**: Battery state-of-charge over time
- **Price forecast**: Amber Electric spot prices
- **Solar forecast**: Solcast PV generation forecast
- **Demand window**: Peak pricing period (configurable)

## Optimization Flow

1. `slot_schedule.py` generates time slots from forecasts
2. `soc_simulator.py` projects SOC trajectory
3. `optimizer_dp.py` solves for optimal action sequence
4. `optimizer_runner.py` executes slot-by-slot
5. `outcomes.py` tracks actual vs predicted

## Conventions

- **Dataclasses** for data structures (Slot, BatteryTrajectory, etc.)
- **Type hints** required (Python 3.13+)
- **Static methods** for stateless calculations
- **No external dependencies** in core optimizer (pure Python)
- **Deterministic**: Same inputs → same outputs

## Testing

- `tests/engine/test_optimizer_dp.py` - Core optimizer tests
- `tests/test_optimizer_*.py` - Various test files
- `tests/test_scenarios.py` - Scenario-based tests
- **Coverage requirement**: 95% per modified file
- Use `MockStates` and `MockState` from `tests/fixtures/`

## Anti-Patterns

- **NEVER** modify cost function without updating terminal cost
- **NEVER** add hard constraint without documenting in PLANNING_MODEL.md
- **NEVER** skip testing edge cases (empty slots, full battery, etc.)
- **AVOID** stateful optimizer - keep it pure functions

## See Also

- `../docs/PLANNING_MODEL.md` - Full planning model documentation
- `../docs/ARCHITECTURE.md` - System architecture
- `../tests/AGENTS.md` - Testing patterns
- Parent: `../AGENTS.md` - Integration overview
