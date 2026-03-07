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
| `cycle_penalty` | `(import + export) × $0.05/kWh` | Anti-wear, discourages frivolous cycling | L1757 |
| `switching_penalty` | `$0.02` if action ≠ current | Stability, hysteresis against flip-flopping | L1761 |
| `uncertainty_penalty` | Scales with horizon gap | Risk aversion when forecast is short | L1765-1774 |
| `self_consumption_value` | `battery_for_load × $0.15/kWh` | Opportunity cost of exporting | L1779-1814 |
| `solar_opportunity_penalty` | `grid_import × $0.03/kWh` when future solar available | Discourage grid charging when solar can charge for free | L1071-1160 |

### The Net Cost Formula

```python
net_cost = (
    import_cost 
    - export_revenue 
    + cycle_penalty 
    + switching_penalty 
    + uncertainty_penalty 
    - self_consumption_value
    + solar_opportunity_penalty
)
```

Note: `self_consumption_value` is subtracted because it's a credit (value provided).

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
    cycle_penalty_per_kwh: float = 0.01              # $/kWh
    switching_penalty: float = 0.05                  # $ per mode switch
    self_consumption_value_per_kwh: float = 0.25     # $/kWh
    export_price_margin: float = 0.02                # $/kWh above self-consumption
```

Tuning guidelines:
- **Increase penalty** → Stronger discouragement (e.g., higher cycle penalty = less cycling)
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