# Design: Negative FIT Avoidance (Issue #719)

## Problem Statement

The DP optimizer does not proactively discharge to avoid negative FIT export costs. When the battery has ample charge and a future negative-FIT window is forecast, the optimizer may schedule later forced export at negative prices instead of earlier pre-discharge at positive prices. This occurs because:

1. **Terminal cost only penalizes shortfall**, not excess SOC before negative-FIT windows
2. **Stage cost clips negative export price** with `max(0.0, slot.sell_price)`, hiding the pain of later negative-FIT export
3. **`feasible_actions()` only allows proactive export when profitable**, blocking globally optimal pre-discharge actions

Prior attempts to fix this via terminal-cost-only modifications failed because `_backward_induction()` overwrites DP tables for all earlier slots, making intermediate terminal penalties unstable.

## Design Intent

The optimizer should:

- Keep the hard `0c` export floor (never proactively export at `sell_price <= 0`)
- Use `number.localshift_battery_target` / `demand_window_target_soc_pct` as the baseline target
- Permit temporary pre-discharge below that target only by the amount of projected overflow needed to avoid later negative-FIT export
- Not create extra headroom if there are no earlier positive-FIT slots available
- Allow any earlier positive-FIT slot to participate when it lowers projected total cost
- Apply the behavior in both self-consumption and arbitrage modes
- Retain the existing actuator-side `PROACTIVE_EXPORT` throttling behavior (`SOC - 5%`, floor `4%`)

## Architecture: Bounded First-Window Model

### Core Concept

Derive an immutable `negative_fit_avoidance` planner context before backward induction containing:

1. **First negative-FIT window**: The earliest upcoming slot where `sell_price <= 0`
2. **Conservative overflow estimate**: Forecast solar energy that would overflow before that window
3. **Maximum temporary headroom**: How far below `demand_window_target_soc_pct` the solver may discharge, capped by the conservative overflow

The optimizer allows `EXPORT_PROACTIVE` before the first negative-FIT window at any positive FIT, but only if SOC does not go below the bounded temporary floor.

### Data Flow

```
OptimizerInputs
     │
     ▼
┌─────────────────────────────────────────┐
│ _derive_negative_fit_avoidance_context() │
│   - Find first negative-FIT window       │
│   - Compute conservative overflow        │
│   - Derive allowed_headroom              │
└─────────────────────────────────────────┘
     │
     ▼
NegativeFitAvoidanceContext (immutable)
     │
     ▼
┌─────────────────────────────────────────┐
│ _backward_induction()                    │
│   - feasible_actions() uses context      │
│   - stage_cost() sees real negative FIT  │
└─────────────────────────────────────────┘
     │
     ▼
Optimal Plan (may include bounded pre-discharge)
```

## Planner Rules and Boundaries

### `custom_components/localshift/engine/core.py`

Add `_derive_negative_fit_avoidance_context()` that:

- Returns `None` if no negative-FIT window exists within the planning horizon
- Returns `None` if no conservative overflow is projected
- Returns `None` if no earlier positive-FIT slots exist
- Otherwise returns `NegativeFitAvoidanceContext` with:
  - `first_negative_fit_slot_idx`: index of first `sell_price <= 0` slot
  - `conservative_overflow_kwh`: conservative estimate of forecast overflow before that slot
  - `allowed_headroom_pct`: `min(conservative_overflow_kwh / battery_capacity_kwh * 100, max_headroom_pct)`
  - `temporary_floor_pct`: `demand_window_target_soc_pct - allowed_headroom_pct`

Call this function in `_solve()` before `_initialize_dp_tables()` and pass the context to `_backward_induction()`.

### `custom_components/localshift/engine/constraints.py`

Modify `feasible_actions()` to:

- Continue forbidding `EXPORT_PROACTIVE` at `sell_price <= 0` (hard `0c` floor remains)
- When `negative_fit_avoidance_context` is present and current slot is before `first_negative_fit_slot_idx`:
  - Allow `EXPORT_PROACTIVE` for any `sell_price > 0` (broaden eligibility)
  - But only if resulting SOC >= `temporary_floor_pct`

This replaces the current restrictive proactive-export gate for slots before the first negative-FIT window. Slots at or after the first negative-FIT window continue using existing rules.

### `custom_components/localshift/engine/cost.py`

Modify `stage_cost()` to:

- Remove the `max(0.0, slot.sell_price)` clipping for export revenue
- Calculate `export_revenue = grid_export_kwh * slot.sell_price` directly
- When `sell_price < 0`, this makes later negative-FIT export a real cost penalty

The DP optimizer will naturally prefer earlier positive-FIT export over later negative-FIT export because the cost model now represents the true economic impact.

### Boundaries

| Boundary | Rule |
|----------|------|
| Export floor | `sell_price > 0` required for proactive export (hard constraint) |
| SOC floor | `temporary_floor_pct` = `target - allowed_headroom` (bounded below-target) |
| Headroom cap | `allowed_headroom` ≤ conservative overflow estimate |
| Window scope | Only first upcoming negative-FIT window is optimized |
| Mode scope | Applies to both self-consumption and arbitrage modes |

### Fallback Behavior

| Condition | Behavior |
|-----------|----------|
| No negative-FIT window | Context disabled, current logic applies |
| No conservative overflow | Context disabled, current logic applies |
| No earlier positive-FIT slots | Context disabled, accept later negative export |
| Only partial avoidance possible | Create economically justified partial headroom, accept remaining negative export |

## Actuator-Side Safeguard

The optimizer decides *when* proactive export is justified, but hardware execution uses the existing throttled `PROACTIVE_EXPORT` path:

- `custom_components/localshift/state/machine.py:233-245`: `_build_proactive_export_config()` sets `backup_reserve = max(4.0, soc - 5.0)`
- `custom_components/localshift/integration/controller.py:596-675`: `set_proactive_export()` uses this dynamic reserve to create a "trickle export"

This ensures the battery does not dump quickly to the temporary floor. The bounded headroom rule is the planner-side guardrail; the dynamic reserve is the actuator-side guardrail.

## Type Definitions

Add to `custom_components/localshift/engine/types.py`:

```python
@dataclass(frozen=True)
class NegativeFitAvoidanceContext:
    """Immutable context for bounded first-window negative-FIT avoidance."""
    first_negative_fit_slot_idx: int
    conservative_overflow_kwh: float
    allowed_headroom_pct: float
    temporary_floor_pct: float
```

## Testing Strategy

### Unit Tests

1. **Context derivation** (`tests/test_optimizer_dp_solve.py`):
   - Returns `None` when no negative-FIT window in horizon
   - Returns `None` when no forecast overflow
   - Returns `None` when no earlier positive-FIT slots
   - Computes correct `temporary_floor_pct` when all conditions met
   - Respects conservative buffer in overflow estimate

2. **`feasible_actions()` bounded pre-discharge** (`tests/test_constraints.py`):
   - Allows `EXPORT_PROACTIVE` at positive FIT before first negative-FIT window
   - Blocks `EXPORT_PROACTIVE` at `sell_price <= 0` (hard floor preserved)
   - Blocks `EXPORT_PROACTIVE` when SOC would fall below `temporary_floor_pct`
   - Uses normal rules for slots at/after first negative-FIT window

3. **`stage_cost()` negative-FIT handling** (`tests/test_cost.py`):
   - Negative `sell_price` produces negative export revenue (real cost)
   - DP prefers earlier positive-FIT export over later negative-FIT export

### Integration Tests

4. **End-to-end DP scenarios** (`tests/test_scenarios.py`):
   - High solar, future negative-FIT, earlier positive-FIT slots available:
     - Plan schedules bounded pre-discharge at positive FIT
     - SOC may go below `demand_window_target_soc_pct` but not below `temporary_floor_pct`
     - Later negative-FIT export is reduced or eliminated
   - No earlier positive-FIT slots:
     - Plan does not create headroom
     - Accepts later negative export
   - Partial avoidance only:
     - Plan creates partial headroom
     - Accepts remaining later negative export

### Test Updates Required

- `tests/test_optimizer_dp_solve.py::test_dp_planner_negative_fit_no_export`: Update to clarify this tests the hard `0c` floor, not the bounded pre-discharge behavior
- `tests/test_scenarios.py::test_high_solar_no_export_at_negative_prices`: Ensure this tests that export at `sell_price <= 0` is still forbidden, not that no proactive export ever occurs before negative-FIT windows

## Alignment with PLANNING_MODEL.md

| Question | Answer | Implementation |
|----------|--------|----------------|
| Impossible/forbidden? | Export at `sell_price <= 0` | Hard constraint in `feasible_actions()` |
| Required by deadline? | Create bounded headroom before first negative-FIT window | Bounded floor in `feasible_actions()` |
| Discouraged/preferred? | Later negative-FIT export is costly | Real cost in `stage_cost()` |

The design follows the PLANNING_MODEL.md decision tree: hard constraints in `feasible_actions()`, soft penalties in `stage_cost()`, and deadline-like requirements expressed through bounded SOC floors rather than intermediate terminal checkpoints.

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Headroom estimate too aggressive | Use conservative buffer in overflow calculation |
| Unexpected interaction with demand window | Temporary floor respects `demand_window_target_soc_pct` as baseline |
| Test coverage gaps | Explicit test list above, update existing tests |
| Regression in normal export behavior | Fallback to current logic when context is disabled |

## Summary

This design introduces a bounded first-window negative-FIT avoidance model that:

1. Derives a conservative headroom budget before the first negative-FIT window
2. Allows earlier positive-FIT pre-discharge only up to that bounded amount
3. Models negative FIT as a real cost in `stage_cost()`
4. Preserves the hard `0c` export floor in `feasible_actions()`
5. Reuses the existing actuator-side `PROACTIVE_EXPORT` throttling for safe execution

The approach is narrow, aligned with the PLANNING_MODEL.md framework, and explicitly avoids the terminal-cost-only pattern that failed in prior attempts.
