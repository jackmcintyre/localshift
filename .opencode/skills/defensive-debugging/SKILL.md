---
name: defensive-debugging
description: Use when debugging fails - initial fix makes things worse, type migrations break existing code, or thresholds need tuning based on observation
---

# Defensive Debugging

## Overview

Debugging is iterative. The key insight from real failures: **know when to revert vs iterate**. Three proven patterns from production debugging: revert discipline, type migration safety, and empirical tuning.

## Pattern 1: Revert Discipline

### The Problem

When an initial fix makes things worse, the instinct is to "just add another condition to fix it." This compounds complexity until the code is unreadable.

### The Fix

**Revert first, redesign second.**

| Signal | Action |
|--------|--------|
| First fix causes new failure | Revert immediately |
| "I'll just add one more condition" | Revert and redesign |
| Fix is bigger than the original code | Revert and start over |
| You're explaining more than fixing | Revert |

### Real Example: Negative-FIT Avoidance

```bash
# First implementation was reverted
commit 154dbf1: "Revert 'feat: implement negative FIT avoidance algorithm...'"
# 325 lines removed, started over with recoverability model

# Second attempt - redesigned from scratch
commit a0efe5c: "fix: redesign negative-FIT avoidance with recoverability model"
# Replace fixed floor with dynamic recoverability floor
# 458 insertions, 130 deletions - but now it worked
```

**Why it worked:** First attempt made incorrect assumptions about risk. Second attempt computed actual recovery potential from future solar.

### Red Flags

- First fix causes regressions elsewhere
- You're adding more conditions than the original code
- Tests pass but production fails
- The "simple fix" requires explaining for 10+ minutes

---

## Pattern 2: Type Migration Safety

### The Problem

Introducing new types (dataclasses, objects) breaks existing code that assumes dicts or primitives. The compiler doesn't catch it - runtime does.

### The Fix

**Add dict compatibility, never assume callers updated.**

| Strategy | When |
|---------|------|
| Add `.get()` method | New object replaces dict usage |
| Helper functions | Gradual migration needed |
| Keep returning dicts | Full codebase not yet migrated |
| Type aliases | When migration is optional |

### Real Example: ForecastSlot Migration

```bash
# What happened:
# 1. New ForecastSlot dataclass introduced
# 2. Changed code to return ForecastSlot instead of dict
# 3. 21+ places in codebase still called .get() on forecasts
# 4. Runtime AttributeError: 'ForecastSlot' has no attribute 'get'

# The fix (first attempt - incomplete):
# Added .get() method to ForecastSlot
commit a18119d: "feat: add .get() method to ForecastSlot for dict compatibility"

# But more places still assumed dict...

# The fix (worked):
# Reverted to always returning dicts for backwards compatibility
# Added helper function _get_slot_attr for gradual migration
commit 88d03b3: "Fix AttributeError: ForecastSlot has no attribute 'get'"
```

**Why it worked:** Reverted the big-bang refactor, added helper function for code that needed gradual migration.

### Red Flags

- "The type is better, callers should update"
- Adding type checks in 10+ places
- Tests need updating but tests were passing before
- You're fixing the same error in multiple places

---

## Pattern 3: Empirical Debugging

### The Problem

Thresholds and constants are often set to "obvious" values that don't match reality. Without observation, you're guessing.

### The Fix

**Measure first, tune second.**

| Step | Action |
|------|--------|
| 1. Observe | What's the actual value/behavior? |
| 2. Measure | Get concrete numbers, not "seems" |
| 3. Tune | Adjust based on observation |
| 4. Verify | Confirm improvement |

### Real Example: Solcast Staleness Threshold

```bash
# Original: 1 hour threshold
# Problem: Shows "degraded" status frequently

# Investigation:
# commit c11bd42: "Based on observed update frequency of 1-2 hours"
# - Solcast actually updates every 1-2 hours
# - 1 hour was too aggressive (false positives)
# - Increased to 2 hours to match reality

# Result: Reduced false degraded status while maintaining planning functionality
```

### Real Example: Load Forecasting Blend

```bash
# Problem: Load forecaster underestimated when instantaneous was low

# Investigation:
# commit 013cd72: "Load forecaster used instantaneous load directly"
# - Instantaneous: 0.526 kW
# - Recent 1hr avg: 1.272 kW
# - Old forecast: 0.526 kW (too low!)

# Fix: Blend 30% instantaneous + 70% recent
# - New forecast: 1.048 kW (accurate)

# Result: Prevented incorrect solar surplus predictions
```

### Red Flags

- "Should be fine at X" without observation
- Threshold chosen arbitrarily
- "Seems to work" without metrics
- Different values in different places

---

## Combined Pattern

Real debugging often requires all three:

1. **Observe** what's actually happening (empirical)
2. **Revert** if your fix made it worse (revert discipline)
3. **Migrate safely** if you're changing types (type safety)

Example flow:

```
Bug reported → Measure actual behavior → Try fix → 
  If broken: Revert → Redesign based on observation → 
  If changing types: Add compatibility layer → Verify
```

---

## Quick Reference

| Situation | Pattern | Key Action |
|-----------|---------|------------|
| Fix causes new failures | Revert discipline | Revert immediately |
| Type changes break callers | Type migration | Add .get() or helpers |
| Threshold feels wrong | Empirical | Measure before tuning |
| Multiple places broken | Type migration | Check all callers |
| First fix didn't work | Revert discipline | Start over with new design |

---

## Common Mistakes

### Mistake 1: "I'll Just Add a Condition"

```python
# BAD: Piling on complexity
if condition_a:
    do_something()
elif condition_b:
    do_something_else()
elif condition_c and condition_d:
    do_third_thing()
# ... 10 more conditions later
```

**Fix:** Revert. The design is wrong.

### Mistake 2: "The Type Is Better"

```python
# BAD: Breaking existing callers
def get_forecast() -> ForecastSlot:
    return ForecastSlot(...)
    
# Caller: forecast.get('price', 0)  # AttributeError!
```

**Fix:** Add dict compatibility or keep returning dicts.

### Mistake 3: "One Hour Should Be Fine"

```bash
# BAD: No observation
STALENESS_THRESHOLD = timedelta(hours=1)  # Why 1? shrug

# GOOD: Based on observation  
STALENESS_THRESHOLD = timedelta(hours=2)  # Solcast updates every 1-2 hours
```

**Fix:** Measure actual behavior before setting constants.
