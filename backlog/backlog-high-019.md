# Day Boundary Bug in Overnight Grid Charging Decision

**ID:** backlog-high-019
**Priority:** HIGH
**Status:** PROPOSED
**Created:** 2026-02-19
**Updated:** 2026-02-19

---

## Summary

Day boundary bug causes overnight grid charging to start at exactly midnight instead of evaluating throughout the evening.

---

## Description

The grid charging decision logic uses `is_before_dw = slot_hour < target_hour` to determine if a slot is before the demand window. This works for slots after midnight (00:00-14:59) but incorrectly blocks slots before midnight (22:00-23:59).

**Example with DW at 15:00:**
- 23:45: `slot_hour = 23`, `is_before_dw = 23 < 15 = False` → **BLOCKED** (wrong!)
- 00:00: `slot_hour = 0`, `is_before_dw = 0 < 15 = True` → **ALLOWED** (correct)

This causes overnight grid charging decisions to be delayed until midnight, when they should be evaluated throughout the evening.

---

## Affected Files

- `custom_components/localshift/computation_engine_lib/forecast_computer.py` (line ~980)

---

## Steps to Reproduce

1. Set battery target to 100%
2. Set demand window to 15:00
3. Observe that overnight grid charging starts at exactly 00:00, not earlier in the evening

---

## Proposed Solution

Replace the simple hour comparison with the existing helper method:

```python
# Current (buggy):
is_before_dw = slot_hour < target_hour

# Fixed:
next_dw_start = self._next_demand_window_start_dt(slot_start, dw_start_time)
is_before_dw = slot_start < next_dw_start
```

The `_next_demand_window_start_dt()` method already correctly handles day boundaries.

---

## Notes

This bug was discovered during investigation of overnight grid charging behavior. The fix is straightforward and uses existing infrastructure.

---

## Related Items

- Related to overnight grid charging analysis
- Affects the same logic as Issue 4 in MODE_SWITCHING_DELAY_ANALYSIS.md