# Backlog Item: hours_to_dw Calculation Bug

**ID:** backlog-high-013  
**Priority:** HIGH  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

`hours_to_dw` calculation produces 0 for all slots after demand window start, incorrectly triggering boost charging.

---

## Description

The `hours_to_dw` (hours to demand window) calculation in `forecast_computer.py` has a logic error when the current slot time is past the demand window start hour. Instead of calculating the time to the **next** demand window (tomorrow), it calculates a negative value and clamps it to 0.

This causes:
1. All slots after the demand window start get `hours_to_dw = 0`
2. The condition `hours_to_dw < 2` always evaluates to `True` for these slots
3. Boost charging rate (5kW) is incorrectly activated when it shouldn't be

**Example:**
- `slot_start` = 18:00, `target_hour` = 16 (demand window start)
- Calculation: `slot_start.replace(hour=16) - slot_start` = `16:00 - 18:00` = **-2 hours**
- `max(-2, 0)` = **0**
- `hours_to_dw < 2` → `0 < 2` → **True** (incorrectly triggers boost charging)

---

## Affected Files

- `custom_components/amber_powerwall/computation_engine_lib/forecast_computer.py` (lines 751-759)

---

## Steps to Reproduce

1. Run the forecast computation after the demand window start time (e.g., at 18:00 when DW starts at 16:00)
2. Observe that `hours_to_dw` is 0 for all remaining slots
3. Boost charging logic incorrectly activates for slots that shouldn't need it

---

## Proposed Solution

Calculate time to the **next** demand window, handling day wrap-around:

```python
# Calculate next demand window start datetime
target_dt = slot_start.replace(hour=target_hour, minute=0, second=0, microsecond=0)
if target_dt <= slot_start:
    target_dt += timedelta(days=1)

hours_to_dw = (target_dt - slot_start).total_seconds() / 3600
```

This mirrors the logic used for `is_before_dw` elsewhere in the codebase.

---

## Notes

- The `is_before_dw` check earlier in the code (around line 729) correctly handles day wrap-around
- This bug only affects the boost charging rate decision, not whether charging happens
- Low urgency since boost charging is only cosmetically different (faster rate)

---

## Related Items

- Similar day wrap-around logic exists in `_next_demand_window_start_dt()` method (lines 232-242)