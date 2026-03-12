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

## Key Concepts

- **Slots**: 15-minute intervals
- **Horizon**: Planning window (24-48 hours)
- **SOC trajectory**: Battery state-of-charge over time
- **Demand window**: Peak pricing period

## Anti-Patterns

- NEVER modify cost without updating terminal cost
- NEVER add hard constraint without documenting
- Keep optimizer pure (deterministic, stateless)

## See Also

- `../AGENTS.md` - Root rules
- `../../docs/PLANNING_MODEL.md` - Full guide
