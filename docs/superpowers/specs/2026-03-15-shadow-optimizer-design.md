# Shadow Optimizer Implementation Design

**Issue:** #300 (Part 2 - completing shadow optimizer)  
**Date:** 2026-03-15  
**Status:** Draft

## Context

This is **Phase 2** of Issue #300. Phase 1 (PR #745) added:
- Config options for pricing source and comparison mode
- Shadow price reading in state/reader.py
- CoordinatorData fields for shadow prices and comparison
- Comparison sensors (non-functional placeholder)

**This spec completes the implementation** by actually running the shadow optimizer and populating the comparison fields.

## Summary

Implement the shadow optimizer for A/B comparison between Amber and Amber Express pricing sources. The shadow optimizer runs the DP planner twice per cycle - once with primary prices and once with shadow (alternate) prices - and compares the resulting decisions.

## Background

Issue #300 added support for selecting between Amber and Amber Express pricing sources. The data collection infrastructure (shadow prices, forecasts) is in place but not yet used for comparison.

This design completes the implementation by running the optimizer twice and comparing decisions.

## Requirements

### Core Requirements

1. **Shadow Optimizer Execution** - Run DP optimizer with alternate pricing source
2. **Decision Comparison** - Compare primary vs shadow battery mode decisions
3. **Comparison Logging** - Log mismatches to decision_log
4. **Skip on Unavailable** - Skip shadow run if shadow prices unavailable

### Behavior

| Scenario | Behavior |
|----------|----------|
| Comparison disabled | Single optimizer run (existing behavior) |
| Comparison enabled + shadow available | Run optimizer twice, compare decisions |
| Comparison enabled + shadow unavailable | Skip shadow run, keep previous comparison state |

## Design

### Architecture

```
                    +---------------------+
                    |  OptimizerFacade    |
                    |  run_inline()       |
                    +---------+-----------+
                              |
                              v
                    +---------------------+
                    |  Primary Run         |
                    |  (actual prices)    |
                    +---------+-----------+
                              |
                              v
              +---------------+---------------+
              |  comparison_mode == enabled?   |
              +---------------+---------------+
                       No    |    Yes
                             v
                    +---------------------+
                    |  Shadow Run         |
                    |  (shadow prices)    |
                    +---------+-----------+
                              |
                              v
                    +---------------------+
                    |  Compare Decisions  |
                    |  Update fields      |
                    |  Log mismatch       |
                    +---------------------+
```

### Data Flow

1. **Config Phase**: `computation_engine.py` passes `pricing_source` and `comparison_mode` to optimizer
2. **Primary Run**: Uses `general_price`, `feed_in_price`, `general_forecast`, `feed_in_forecast`
3. **Shadow Run**: Uses `general_price_shadow`, `feed_in_price_shadow`, `general_forecast_shadow`, `feed_in_forecast_shadow`
4. **Comparison**: Exact battery mode string comparison
5. **Result**: Write to `comparison_match`, `primary_decision`, `shadow_decision`

### Files to Modify

| File | Responsibility |
|------|---------------|
| `computation_engine.py` | Pass pricing_source, comparison_mode to optimizer |
| `optimizer_facade.py` | Run shadow, compare decisions, update coordinator |
| `slots.py` | Accept price overrides for shadow builds |
| `coordinator/data.py` | Fields already exist |

### CoordinatorData Fields (Exist from Phase 1)

These fields were added in PR #745 (Phase 1 of Issue #300):

```python
# Shadow prices (populated by state/reader.py when comparison enabled)
general_price_shadow: float = 0.0      # Read from alternate source entity
feed_in_price_shadow: float = 0.0
general_forecast_shadow: list = field(default_factory=list)  # Embedded forecasts
feed_in_forecast_shadow: list = field(default_factory=list)

# Comparison results (exist, to be populated by this spec)
primary_decision: str = ""    # Battery mode string (e.g., "self_consumption")
shadow_decision: str = ""     # Battery mode string from shadow run
comparison_match: bool = True  # True if primary_decision == shadow_decision
price_delta: float = 0.0      # Average price difference ($/kWh) over forecast horizon
```

**No new CoordinatorData fields needed.**

```python
# If user selects "amber", shadow reads from "amber_express" entities
# If user selects "amber_express", shadow reads from "amber" entities
shadow_prefix = "amber_express_100h_" if primary == "amber" else "100h_"
general_price_shadow = self._read_float_optional(f"sensor.{shadow_prefix}general_price")
```

### Decision Comparison Logic

```python
# Compare exact battery mode strings
primary_mode = primary_result.decisions[0].battery_mode
shadow_mode = shadow_result.decisions[0].battery_mode
comparison_match = (primary_mode == shadow_mode)

# Store decisions
data.primary_decision = primary_mode
data.shadow_decision = shadow_mode

# Calculate price delta (average difference over forecast period)
if data.general_forecast and data.general_forecast_shadow:
    total_delta = 0.0
    count = min(len(data.general_forecast), len(data.general_forecast_shadow))
    for i in range(count):
        primary_price = data.general_forecast[i].get("price", 0)
        shadow_price = data.general_forecast_shadow[i].get("price", 0)
        total_delta += abs(primary_price - shadow_price)
    data.price_delta = total_delta / count if count > 0 else 0.0
else:
    # Fallback: use current prices
    data.price_delta = abs(data.general_price - data.general_price_shadow)
```

### Logging Mismatches

Only log when comparison is enabled AND there's a mismatch:

```python
if not comparison_match:
    entry = {
        "timestamp": now.isoformat(),
        "old_mode": primary_mode,  # e.g., "self_consumption"
        "new_mode": shadow_mode,   # e.g., "grid_charging" 
        "reason": f"Decision mismatch: Primary={primary_mode}, Shadow={shadow_mode}, Delta=${price_delta:.2f}",
    }
    data.decision_log.append(entry)
```

**Note:** Uses actual mode names, not generic strings. The decision_log format is flexible (string fields), so this works.

### Handling Unavailable Prices

**Shadow unavailable (primary valid):**
- If comparison enabled but shadow prices are 0.0 (unavailable):
  - Skip shadow run
  - Reset comparison_match to True (neutral state)

**Primary unavailable (shadow valid):**
- If primary prices are unavailable, the primary optimizer should already handle this
- The safety gate will block optimization if prices are invalid
- In this case, skip comparison entirely (can't compare if primary failed)

**Note on state persistence:** CoordinatorData is recreated each cycle, so "previous comparison_match state" means resetting to neutral (True) when unavailable. This is acceptable behavior.

```python
# In optimizer_facade.run_inline()
if comparison_enabled:
    # Check if we have valid prices for both runs
    primary_valid = data.general_price > 0 and data.prices_available
    shadow_valid = data.general_price_shadow > 0
    
    if not primary_valid:
        # Can't run comparison - primary failed
        return
    
    if not shadow_valid:
        # Skip shadow, keep previous state
        return
```

### Config Options Passing

In `computation_engine.py`, add to `_build_optimizer_config_options()`:

```python
return {
    # ... existing options ...
    "pricing_source": self.entry.options.get(
        CONF_PRICING_DATA_SOURCE, DEFAULT_PRICING_DATA_SOURCE
    ),
    "comparison_mode": self.entry.options.get(
        CONF_COMPARISON_MODE, DEFAULT_COMPARISON_MODE
    ),
}
```

**Clarification:** The spec references fields that exist in the issue-300 worktree (not the main branch). These were added in PR #745.

## Acceptance Criteria

1. When comparison_mode is "disabled": Single optimizer run, comparison fields unchanged
2. When comparison_mode is "enabled" + shadow available: Dual runs, compare, log mismatches
3. When comparison_mode is "enabled" + shadow unavailable: Skip shadow run, keep previous state
4. Mismatch only when exact battery_mode differs
5. Shadow run uses alternate source prices (amber <-> amber_express)

## Testing

### Unit Tests

- Test shadow run produces different decision when prices differ significantly
- Test comparison_match = True when modes match
- Test comparison_match = False when modes differ
- Test skip when shadow prices unavailable (0.0)

### Integration Tests

- Full flow with comparison enabled
- Full flow with comparison disabled
- Transition from disabled to enabled
