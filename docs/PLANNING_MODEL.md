# LocalShift Planning Model: Soft-Constrained Dynamic Programming

## Overview

LocalShift uses a **Soft-Constrained Dynamic Programming** approach to compute optimal battery control decisions. This pattern combines hard constraints (physical limits, safety bounds) with soft penalties (economic costs, preferences) to create a clean, extensible planning system.

## Core Pattern

```
┌─────────────────────────────────────────────────────────────────┐
│                     TWO-LAYER DESIGN                            │
│                                                                  │
│  LAYER 1: Hard Constraints (feasibility)                       │
│  ─────────────────────────────────────────────                  │
│  • What CAN I do?                                               │
│  • Physical limits, safety bounds                               │
│  • Prunes action space                                          │
│                                                                  │
│  LAYER 2: Soft Penalties (optimization)                        │
│  ─────────────────────────────────────────────                  │
│  • What SHOULD I do?                                            │
│  • Economic costs, preferences                                  │
│  • Guides decision via cost minimization                        │
│                                                                  │
│  TERMINAL COST: Goal State Incentive                           │
│  ─────────────────────────────────────────────                  │
│  • What MUST I achieve?                                         │
│  • Encodes requirements (e.g., demand window target)            │
│  • Creates forward-looking incentive                            │
└─────────────────────────────────────────────────────────────────┘
```

## Component Mapping

| Layer | Implementation | Location |
|-------|----------------|----------|
| Hard Constraints | `feasible_actions()` | `optimizer_dp.py:1341` |
| Soft Penalties | `stage_cost()` | `optimizer_dp.py:1721` |
| Terminal Cost | `terminal_cost()` | `optimizer_dp.py:1818` |

## Hard Constraints: `feasible_actions()`

Determines which actions are physically/legal possible for a given state.

### Current Constraints

| Constraint | Effect | Code Location |
|-----------|--------|---------------|
| SOC floor/ceiling | `can_charge = soc < max_soc`, `can_discharge = soc > min_soc` | L1373-1374 |
| Demand window | No grid import during DW slots | L1415 |
| Price thresholds | Only charge if price is cheap (self-consumption mode) | L1419-1428 |
| Solar sufficiency | Suppress grid charging when solar covers deficit | L1378-1416 |
| Export profitability | Only export if sell price exceeds threshold | L1437-1446 |
| Negative-FIT DW guardrail | In avoidance mode, DW export allowed only if net benefit >= $0.02/kWh | `engine/constraints.py` / `engine/core.py` |

### When to Add Hard Constraints

Add to `feasible_actions()` when:
- **Physical impossibility**: Battery cannot charge when full
- **Safety requirements**: Never drop below minimum SOC
- **Regulatory/contractual**: Demand window has no grid import
- **Feature gates**: Disable certain actions based on configuration

### Example: Adding a New Hard Constraint

```python
# Scenario: Never export during "quiet hours" (10 PM - 6 AM)
@staticmethod
def feasible_actions(...) -> list[PlannerAction]:
    actions = []
    
    # ... existing constraints ...
    
    # Quiet hours constraint
    slot_hour = datetime.fromisoformat(slot.timestamp_iso).hour
    is_quiet_hours = slot_hour >= 22 or slot_hour < 6
    
    # Export constraints
    if can_discharge and not is_quiet_hours:  # NEW: quiet hours check
        if config.optimization_mode == "self_consumption":
            if slot.sell_price >= min_profitable_sell:
                actions.append(PlannerAction.EXPORT_PROACTIVE)
    
    return actions
```

## Soft Penalties: `stage_cost()`

Encodes preferences, costs, and behavioral biases into a scalar cost.

### Current Penalty Terms

| Penalty | Formula | Purpose | Code Location |
|---------|---------|---------|---------------|
| `import_cost` | `grid_import × buy_price` | Direct cost of buying from grid | L1754 |
| `export_revenue` | `grid_export × sell_price` | Revenue from selling (negative cost) | L1755 |
| `switching_penalty` | `$0.02` if action ≠ current | Stability, hysteresis against flip-flopping | L1761 |
| `uncertainty_penalty` | Scales with horizon gap | Risk aversion when forecast is short | L1765-1774 |
| `self_consumption_value` | `battery_for_load × $0.15/kWh` | Opportunity cost of exporting | L1779-1814 |
| `solar_opportunity_penalty` | `grid_import × $0.03/kWh` when future solar available | Discourage grid charging when solar can charge for free | L1071-1160 |

### The Net Cost Formula

```python
net_cost = (
    import_cost 
    - export_revenue 
    + switching_penalty 
    + uncertainty_penalty 
    - self_consumption_value
    + solar_opportunity_penalty
)
```

Note: `self_consumption_value` is subtracted because it's a credit (value provided).

**Anti-cycling:** Protection against wasteful cycling is handled by the `futile_cycling_penalty` term, which penalises grid charging when forward simulation shows the charged energy will drain through household load before reaching a useful period (solar surplus or demand window). The `switching_penalty` provides additional stability by discouraging frequent mode changes. There is no per-kWh cycle penalty; the former `cycle_penalty` term was removed in issue #804 because it indiscriminately penalised all charge/discharge energy regardless of whether cycling was economically beneficial.

**Minimum cycle saving (hard gate):** Separately from the soft penalties above, `_compute_best_action` drops a grid-charge action entirely when it beats the HOLD alternative by a positive but sub-threshold margin — specifically when `0 < (hold_total_cost - charge_total_cost) < config.min_cycle_saving × charge_kwh`. This rules out "micro" arbitrage (cycling the battery for a few c/kWh) while preserving genuine pre-charge and price-spike capture: because the margin is the DP's real cost difference, it already credits the demand-window target, evening-peak avoidance, and backup value via `future_cost`, and unlike a soft penalty it cannot be "paid through". Configured by `number.localshift_min_cycle_saving` (default $0.25/kWh; `0` disables).

### When to Add Soft Penalties

Add to `stage_cost()` when:
- **Economic trade-offs**: Cost vs. benefit analysis
- **Behavioral bias**: Encourage/discourage certain patterns
- **Risk management**: Account for uncertainty or risk
- **Multi-objective optimization**: Balance competing goals

### Example: Adding a New Soft Penalty

```python
# Scenario: Penalize grid charging during peak demand hours (environmental concern)
@staticmethod
def stage_cost(...) -> ObjectiveTerms:
    # ... existing terms ...
    
    # NEW: Peak demand penalty
    peak_demand_penalty = 0.0
    if action in (PlannerAction.CHARGE_GRID_NORMAL, PlannerAction.CHARGE_GRID_BOOST):
        slot_hour = datetime.fromisoformat(slot.timestamp_iso).hour
        is_peak_hour = slot_hour in range(17, 21)  # 5 PM - 9 PM
        if is_peak_hour:
            peak_demand_penalty = grid_import_kwh * 0.02  # $0.02/kWh penalty
    
    return ObjectiveTerms(
        # ... existing fields ...
        peak_demand_penalty=peak_demand_penalty,
        net_cost=net_cost + peak_demand_penalty,
    )
```

**Note**: You'll also need to update `ObjectiveTerms` dataclass to include the new field.

## Terminal Cost: `terminal_cost()`

Encodes requirements and goal states that must be achieved by a specific point.

### Current Terminal Costs

| Requirement | Formula | Applied At | Code Location |
|-------------|---------|------------|---------------|
| Demand window target | `max(0, target - SOC) × $0.03/%` | DW entry slot | L1818-1829 |

### Why Terminal Cost Works

The terminal cost creates a "backwards incentive" through DP:

```
At DW entry (slot T):
  penalty = shortfall × rate

At slot T-1:
  DP considers: "If I don't charge now, I'll pay penalty at T"
  Result: Charge if penalty > cost of charging

At slot T-2:
  DP considers: "If I wait, I might miss cheap prices before T"
  Result: Charge early when prices are cheap
```

This single penalty creates **all** the pre-charging behavior without explicit heuristics.

### When to Add Terminal Costs

Add to `terminal_cost()` when:
- **Hard requirements**: Must achieve a specific state by deadline
- **Target tracking**: SOC, energy reserves, capacity margins
- **Constraint satisfaction**: Cumulative constraints over horizon

### Example: Adding a New Terminal Cost

```python
# Scenario: Minimum SOC required for overnight backup
@staticmethod
def terminal_cost(
    final_soc_pct: float, 
    target_soc_pct: float,
    config: OptimizerConfig
) -> float:
    # Existing: demand window shortfall
    dw_shortfall = max(0.0, target_soc_pct - final_soc_pct)
    dw_penalty = dw_shortfall * config.target_shortfall_penalty_per_pct
    
    # NEW: overnight backup requirement
    backup_shortfall = max(0.0, config.overnight_min_soc_pct - final_soc_pct)
    backup_penalty = backup_shortfall * config.overnight_penalty_per_pct
    
    return dw_penalty + backup_penalty
```

**Note**: You'll need to determine where this penalty should be applied (which slot index) in `_determine_terminal_penalty_idx()`.

## Decision Flow

```
1. Backward Induction (DP)
   For each slot t from T → 0:
     For each SOC bin:
       actions = feasible_actions(soc, slot, config)  # Hard constraints
       For each action:
         next_soc = transition(soc, action, slot, config)
         cost = stage_cost(action, ...) + dp[t+1][next_bin]
       dp[t][bin] = min(cost over actions)

2. Terminal Cost
   At DW entry slot:
     dp[T][bin] = terminal_cost(soc, target, config)

3. Forward Reconstruction
   For each slot t from 0 → T:
     action = dp[t][current_bin].action  # Already optimal
     Apply action, update SOC
```

## Extending the Model: Decision Guide

When adding a new feature, ask:

| Question | Yes → | No → |
|----------|-------|------|
| Is it physically impossible? | Hard constraint | Continue |
| Is it unsafe/forbidden? | Hard constraint | Continue |
| Is it economically discouraged? | Soft penalty | Continue |
| Is it a behavioral bias? | Soft penalty | Continue |
| Is it a requirement by deadline? | Terminal cost | Continue |
| Is it an optional goal? | Soft penalty | Continue |

### Decision Tree

```
New Feature
    │
    ├─► Impossible/Forbidden?
    │       └─► Add to feasible_actions()
    │
    ├─► Requirement by deadline?
    │       └─► Add to terminal_cost()
    │
    └─► Otherwise
            └─► Add penalty term to stage_cost()
```

## Configuration: Tuning Parameters

All penalty rates are configurable via `OptimizerConfig`:

```python
@dataclass
class OptimizerConfig:
    # Penalty rates
    target_shortfall_penalty_per_pct: float = 0.030  # $/%-point
    switching_penalty: float = 0.05                  # $ per mode switch
    self_consumption_value_per_kwh: float = 0.25     # $/kWh
    export_price_margin: float = 0.02                # $/kWh above self-consumption
```

Tuning guidelines:
- **Increase penalty** → Stronger discouragement (e.g., higher switching penalty = fewer mode changes)
- **Decrease penalty** → Weaker discouragement (e.g., lower export margin = more exports)
- **Set to zero** → Disable penalty (use caution, may lead to undesired behavior)

## Testing New Features

When adding a constraint or penalty:

1. **Unit tests**: Test `feasible_actions()` or `stage_cost()` in isolation
   ```python
   def test_quiet_hours_constraint():
       slot = SlotContext(timestamp_iso="2024-01-01T23:00:00", ...)
       actions = DPPlanner.feasible_actions(soc=50, slot=slot, config=config)
       assert PlannerAction.EXPORT_PROACTIVE not in actions
   ```

2. **Scenario tests**: Test end-to-end behavior in `tests/test_scenarios_dp.py`
   ```python
   def test_peak_demand_penalty_discourages_charging():
       # Peak hour slot with cheap price
       # Verify optimizer still prefers waiting over charging
   ```

3. **Comparison tests**: Compare with baseline (no penalty) to validate effect
   ```python
   # Run with penalty enabled vs disabled
   # Verify behavior change is as expected
   ```

## Control Philosophy

These rules define the intended operating philosophy for `self_consumption` mode.
They are **not** just preferences — they are part of the system's identity.
Future changes should not violate them without explicit discussion.

### Principles

1. **Economics over comfort.** The goal is to minimize electricity cost, not to keep the battery full.
2. **Grid import is acceptable overnight.** If the battery runs down and the house uses the grid, that is often the correct outcome. Overnight charging is generally wasteful and should stay penalized.
3. **Low overnight SOC is not a bug.** The battery spending hours at 10% SOC is acceptable if that is the cheapest path. Do not interpret this as a problem that needs fixing.
4. **Proactive charging needs strong justification.** Charging before a deadline is justified only when target-feasibility says so, not just to avoid low SOC or to feel "ready."
5. **Anti-goal: do not optimize for overnight reserve.** In `self_consumption`, there is no implicit objective to "hold reserve overnight at all costs." If you find yourself adding reserve-holding behavior, stop and check whether it was requested.

### Anti-Goals

These are behaviors that should **not** appear in `self_consumption` unless explicitly requested:

- Holding a meaningful reserve overnight (e.g., 25–35%) without explicit reason
- Charging from the grid to avoid falling below minimum SOC
- Reducing anti-cycling penalties to enable more overnight charging
- Interpreting low overnight SOC as a reason to change the planning model

### Guardrail Questions

Before proposing changes to optimizer behavior, ask:

- "Is this adding reserve-holding behavior?" If yes, it needs explicit approval.
- "Am I treating low overnight SOC as a bug?" If yes, reconsider the framing.
- "Does this change make proactive charging easier?" If yes, make sure it is gated by a real deadline, not just comfort.

## References

- `optimizer_dp.py` - Core DP implementation
- `ARCHITECTURE.md` - System architecture overview
- `FORECAST_DRIVEN_CONTROL.md` - Design principles
- `OPTIMIZER_DP_ROLLOUT.md` - Rollout history

## Academic Background

This pattern is known in the literature as:

- **Penalty Methods in Dynamic Programming** - Soft constraints via cost function
- **Discrete-Time Optimal Control** - Sequential decision-making with constraints
- **Economic Model Predictive Control (EMPC)** - Cost-optimized control for energy systems

Key papers:
- Bertsekas, D. P. "Dynamic Programming and Optimal Control" - Chapter on constrained DP
- Rawlings, J. B. & Mayne, D. Q. "Model Predictive Control: Theory and Design" - EMPC foundations
