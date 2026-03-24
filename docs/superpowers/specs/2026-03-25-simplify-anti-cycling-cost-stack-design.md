# Design Spec: Simplify Anti-Cycling Cost Stack

**Issue:** #804  
**Date:** 2026-03-25  
**Status:** Approved  

---

## Background

Over several rounds of tuning the LocalShift optimizer accumulated three overlapping anti-cycling mechanisms in `stage_cost()`:

1. **Cycle penalty** — flat 8c/kWh on every kWh of grid import + export
2. **Reduced self-consumption credit** — battery covering house load credited at `buy_price - cycle_penalty_per_kwh` instead of full `buy_price`
3. **Futile-cycling penalty** — context-aware penalty: only fires when simulation shows charged energy would drain through load before reaching a useful period (solar surplus or demand window)

### The Double-Counting Problem

Mechanisms 1 and 2 tax the same physical round-trip cost twice:

| Scenario | Mechanism 1 | Mechanism 2 | Total tax |
|----------|------------|------------|-----------|
| Import 1 kWh at 17c, self-consume at 22c | +8c at import | +8c lost from credit | +16c |

A useful pre-charge that saves 5c net (22c - 17c) becomes a net loss (-11c) after both penalties fire. This is economically incorrect and suppresses genuinely beneficial charging.

### Why Mechanism 3 is Sufficient

The futile-cycling penalty is:
- **Context-aware**: it models whether energy drains before reaching a useful period
- **Targeted**: it fires only on grid charge actions, only when energy is actually futile
- **Non-redundant**: it doesn't penalize charging that will be used beneficially

With issues #801, #802, and #805 resolved, the planner now has accurate load data, solar credit, and deadline scoping. Mechanism 3 can do its job correctly without needing a blunt fallback.

---

## Decision

**Keep only mechanism 3 (futile-cycling penalty). Remove mechanisms 1 and 2 entirely.**

The `cycle_penalty_per_kwh` field is removed completely — not defaulted to zero. This is a clean break, not a vestigial field.

**Rationale for clean removal over zeroing:**
- A field defaulted to zero creates confusion about whether to tune it
- If load prediction proves unreliable in the future, that problem should be fixed in load prediction, not papered over by a blunt cycle tax

---

## Scope

### Files Modified

| File | Change |
|------|--------|
| `custom_components/localshift/engine/cost.py` | Remove cycle penalty calc; restore full buy_price SC credit |
| `custom_components/localshift/engine/types.py` | Remove `cycle_penalty_per_kwh` from `OptimizerConfig`; remove `cycle_penalty` from `ObjectiveTerms` |
| `custom_components/localshift/engine/optimizer_runner.py` | Remove cycle penalty config reading |
| `custom_components/localshift/const.py` | Remove `CONF_CYCLE_PENALTY`, `DEFAULT_CYCLE_PENALTY`, entry from `THRESHOLD_RANGES` |
| `custom_components/localshift/number.py` | Remove cycle penalty number entity from `NUMBER_DEFINITIONS` |
| `custom_components/localshift/config_flow/__init__.py` | Remove `CONF_CYCLE_PENALTY` from `_build_advanced_schema()` and defaults |
| `custom_components/localshift/computation_engine.py` | Remove `CONF_CYCLE_PENALTY` from config dict |
| `docs/PLANNING_MODEL.md` | Update cost model table and formula |
| `docs/ENTITY_REFERENCE.md` | Remove `number.localshift_cycle_penalty` entity section; remove from example config |
| `AGENTS.md` (root) | Update entity count: Numbers 4 → 3 |
| `.opencode/skills/dp-optimizer-modification/SKILL.md` | Update cost model reference |

### Tests Modified

| File | Change |
|------|--------|
| `tests/engine/test_cost.py` | Remove cycle penalty tests; update SC credit expectations |
| `tests/test_cost.py` | Remove cycle_penalty assertion; update SC credit test |
| `tests/test_optimizer_dp_solve.py` | Remove `cycle_penalty` from `OptimizerConfig` constructor calls |
| `tests/test_optimizer_runner_integration.py` | Remove cycle_penalty config read/default tests |
| `tests/test_self_consumption_philosophy.py` | Remove `cycle_penalty_per_kwh` fixture param; remove `DEFAULT_CYCLE_PENALTY` import/assertion from `test_penalties_are_not_recently_reduced()`; update SC credit assertion |
| `tests/test_solar_opportunity_penalty.py` | Update SC credit expectations (now full buy_price, not reduced) |
| `tests/test_futile_cycling_penalty.py` | Update SC credit expectations |
| `tests/engine/test_terminal_cost_accuracy.py` | Remove `cycle_penalty_per_kwh` from config |
| `tests/engine/test_terminal_cost_regression.py` | Remove `cycle_penalty_per_kwh` from config |
| `tests/engine/test_reason_codes.py` | Remove `cycle_penalty_per_kwh` from config |
| `tests/engine/test_solar.py` | Remove `cycle_penalty_per_kwh` from config |
| `tests/test_switching_penalty.py` | Remove `cycle_penalty_per_kwh=0.0` from config |
| `tests/test_optimizer_scaffold.py` | Remove `cycle_penalty` from `ObjectiveTerms` constructor |

---

## Detailed Code Changes

### `engine/cost.py`

**Remove cycle penalty calculation (around line 51-52):**
```python
# REMOVE these lines:
cycle_kwh = grid_import_kwh + grid_export_kwh
cycle_penalty = cycle_kwh * config.cycle_penalty_per_kwh
```

**Restore full buy_price self-consumption credit (around line 104-107):**
```python
# BEFORE:
sc_multiplier = max(0.0, slot.buy_price - config.cycle_penalty_per_kwh)
self_consumption_value = battery_for_load * sc_multiplier

# AFTER:
self_consumption_value = battery_for_load * slot.buy_price
```

**Remove from ObjectiveTerms construction (around line 136):**
```python
# REMOVE:
cycle_penalty=cycle_penalty,
```

**Remove comment explaining the reduced credit (around line 81):**
```
# REMOVE comment block that explains cycle_penalty subtraction
```

### `engine/types.py`

**Remove from `OptimizerConfig`:**
```python
# REMOVE:
cycle_penalty_per_kwh: float = 0.08  # $/kWh (battery wear + round-trip efficiency)
```

**Remove from `ObjectiveTerms`:**
```python
# REMOVE field:
cycle_penalty: float = 0.0

# REMOVE from net_cost property:
+ self.cycle_penalty

# REMOVE from to_dict():
"cycle_penalty": self.cycle_penalty,
```

### `const.py`

```python
# REMOVE:
CONF_CYCLE_PENALTY = "cycle_penalty"
DEFAULT_CYCLE_PENALTY = 0.08

# REMOVE from THRESHOLD_RANGES:
CONF_CYCLE_PENALTY: {"min": 0.00, "max": 0.20, "step": 0.01, "unit": "$/kWh"},
```

### `optimizer_runner.py`

```python
# REMOVE:
cycle_penalty = float(config_options.get(CONF_CYCLE_PENALTY, DEFAULT_CYCLE_PENALTY))

# REMOVE from OptimizerConfig constructor:
cycle_penalty_per_kwh=cycle_penalty,
```

---

## Updated Cost Model

After this change, `net_cost` in `ObjectiveTerms` is:

```
net_cost = import_cost
         - export_revenue
         - self_consumption_value       # battery_for_load × buy_price (full rate)
         + shortfall_penalty
         + uncertainty_penalty
         + switching_penalty
         + solar_opportunity_penalty
         + futile_cycling_penalty       # sole anti-cycling mechanism
```

The futile-cycling penalty remains unchanged. It is the only mechanism that prevents wasteful charging.

---

## What Does NOT Change

- Futile-cycling penalty logic (`penalties.py`, `get_futile_cycling_penalty_factor()`)
- Target shortfall penalty
- Switching penalty
- Solar opportunity penalty
- All constraint logic (`feasible_actions()`, `check_global_solar_sufficiency()`)
- Arbitrage mode behavior: cycle penalty was not mode-specific; its removal applies equally to all modes. Futile-cycling penalty is self-consumption-oriented but was already the behavior in arbitrage mode too.
- Export actions: the old cycle penalty taxed `grid_export_kwh` too, but this was always incidental — the export path is primarily governed by sell price vs buy price margin. Export revenue and the export margin gate remain unchanged.

### User-Visible Changes

- The `number.localshift_cycle_penalty` entity (Numbers slider) is removed from HA
- The cycle penalty field is removed from config flow advanced settings
- Entity count: Numbers 4 → 3
- Existing integrations that set `cycle_penalty` in their config will have that key silently ignored (HA `entry.options.get()` pattern returns the default which no longer exists)

---

## Expected Behavioral Impact

- **Useful overnight pre-charging** becomes more attractive when buy price is low and futile-cycling factor is low (energy will be used)
- **Wasteful cycling** (charge-then-drain) still penalized by futile-cycling penalty
- **Self-consumption value** is now credited at full buy price, reflecting the true economic value of using stored solar
- **No change** to behavior when futile-cycling factor is already 0 (energy will be used regardless)

---

## Risk

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Load prediction underestimates consumption → futile-cycling penalty doesn't fire → spiky charging | Medium (futile-cycling sim depends on load accuracy) | Monitor overnight SOC patterns post-deploy; fix load prediction if patterns emerge |
| External configs referencing `cycle_penalty` break | Known; intentional | Breaking change accepted. Clean removal preferred over vestigial field |
| SC credit increase causes over-discharge | Low (SOC min constraint still enforced) | Scenario tests must verify floor-bounce behavior is unchanged |

---

## Related Issues

- Parent: #800
- Resolved by prerequisites: #801, #802, #805 (fixes that improve planner accuracy, making this simplification safe)
- Companion cleanup: #806 (prune other low-value tuning/diagnostic cruft)
