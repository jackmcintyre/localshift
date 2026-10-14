# DP Optimizer

## Core Pattern: Soft-Constrained DP

```
feasible_actions() → What CAN I do? (hard constraints)
stage_cost()       → What SHOULD I do? (soft penalties)
terminal_cost()    → What MUST I achieve? (deadline)
```

## Files

| File | Purpose |
|------|---------|
| `optimizer_facade.py` | **Live entry point.** `OptimizerFacade.run_inline` is what `computation_engine.py` calls every cycle: builds slots, runs the planner, writes the `optimizer_*` fields, runs the shadow comparison. Change execution behaviour here |
| `optimizer_runner.py` | Helpers, no entry point: `_build_optimizer_config`, `_build_summary`, `OptimizerSafetyGate`, result serialization. Imported by `optimizer_facade.py`, `computation_engine.py` and `forecast/load_deviation.py` |
| `core.py` | `DPPlanner`, the DP solver |
| `constraints.py` | `feasible_actions()` and the solar gates |
| `cost.py` | `stage_cost()` and `terminal_cost()` |
| `transitions.py` | `transition()`, the SOC and grid-flow model for one slot |
| `types.py` | `OptimizerConfig`, `SlotContext`, `ObjectiveTerms` and the other planner types |
| `slots.py` | `SlotBuilder`: turns raw coordinator data into `SlotContext` objects |
| `slot_schedule.py` | Hybrid 5-minute / 30-minute slot schedule built from the price forecast |
| `price_calculator.py` | Price and feed-in calculation helpers for the computation engine |

## Decision Guide

| Question | Action |
|----------|--------|
| Impossible/forbidden? | Add to `feasible_actions()` |
| Required by deadline? | Add to `terminal_cost()` |
| Discouraged/preferred? | Add penalty to `stage_cost()` |

**MUST consult** `docs/PLANNING_MODEL.md` before changes.

**See also:** `docs/INDEX.md` for complete documentation map.

## Key Concepts

- **Slots**: 15-minute intervals
- **Horizon**: Planning window (24-48 hours)
- **SOC trajectory**: Battery state-of-charge over time
- **Demand window**: Peak pricing period

## Anti-Patterns

- NEVER modify cost without updating terminal cost
- NEVER add hard constraint without documenting
- Keep optimizer pure (deterministic, stateless)
- NEVER add reserve-holding behavior in self_consumption without explicit request

## Self-Consumption Philosophy

In `self_consumption` mode, the optimizer's goal is to minimize cost, not to keep the battery full.

- **Low overnight SOC is acceptable.** The battery spending hours at minimum SOC is correct if that is the cheapest path.
- **Grid import is acceptable.** If the battery runs out overnight, importing from the grid is fine. Do not fix this by adding reserve-seeking behavior.
- **Proactive charging needs strong justification.** Only justified when a real deadline/target demands it. Avoid adding it "just in case."
- **Anti-goal: do not optimize for overnight reserve.** If a change makes overnight charging easier or adds reserve-holding, it is almost certainly wrong for self_consumption unless explicitly requested.

See `docs/PLANNING_MODEL.md` "Control Philosophy" section for the full policy.

## See Also

- `../AGENTS.md` - Root rules
- `../../docs/PLANNING_MODEL.md` - Full guide
