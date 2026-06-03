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
| `optimizer_dp.py` | Core DP solver (~865 lines) |
| `optimizer_runner.py` | Slot-by-slot execution |
| `slots.py` | Slot data structures |
| `price_calculator.py` | Price threshold logic |
| `parameters.py` | Parameter definitions |

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
