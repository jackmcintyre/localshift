# Backlog Item

**ID:** backlog-med-013  
**Priority:** MED  
**Status:** COMPLETED  
**Created:** 2026-02-18  
**Updated:** 2026-02-18  

---

## Summary

`Hours to DW` displays 0.0h when current time is inside the demand window instead of showing hours until tomorrow's demand window start.

---

## Description

When the current time falls within today's demand window (e.g., 20:47 with a DW of 15:00–21:00), the `hours_to_dw` display metric reports `0.0h`. It should instead show the time remaining until tomorrow's demand window start (~18 hours in this example).

The root cause is that the `hours_to_dw` calculation in `computation_engine.py` performs a simple "time until DW start today" without accounting for the day boundary when the system is already inside or past today's DW. The result clamps or falls to zero rather than rolling over to the next day's window.

Note: The `_next_demand_window_start_dt` helper in `forecast_computer.py` already handles this correctly (it always returns a future DW start), so the same day-boundary logic needs to be applied to the `hours_to_dw` metric.

---

## Affected Files

- `custom_components/ha_solar_battery_automation/computation_engine.py` (`hours_to_dw` metric calculation)
- `custom_components/ha_solar_battery_automation/forecast_computer.py` (`_next_demand_window_start_dt` — reference implementation, likely correct)

---

## Steps to Reproduce (for bugs)

1. Configure a demand window, e.g. 15:00–21:00.
2. Wait until the current time is inside the demand window (e.g. 20:47).
3. Observe the `Hours to DW` sensor/display metric.
4. Expected: ~18.2h (time until tomorrow's 15:00 DW start).
5. Actual: 0.0h.

---

## Proposed Solution

In `computation_engine.py`, replace the naïve `hours_to_dw` calculation with logic that mirrors `_next_demand_window_start_dt`: if the current time is at or past today's DW start, target tomorrow's DW start instead.

```python
# Pseudocode for hours_to_dw fix
now = datetime.now()
dw_start_today = now.replace(hour=dw_start_hour, minute=dw_start_minute, second=0, microsecond=0)

if now >= dw_start_today:
    # Already inside or past today's DW — point to tomorrow
    next_dw_start = dw_start_today + timedelta(days=1)
else:
    next_dw_start = dw_start_today

hours_to_dw = (next_dw_start - now).total_seconds() / 3600
```

Alternatively, reuse `_next_demand_window_start_dt` directly if it is accessible from `computation_engine.py`.

---

## Notes

- This is a display-only bug; it does not affect automation decisions (which rely on the correctly implemented `_next_demand_window_start_dt`).
- The fix is low-risk: a small conditional addition to the metric calculation.
- Verify the fix covers the edge case where `now` is exactly at DW start (treat as "inside").

---

## Related Items

- `forecast_computer.py` → `_next_demand_window_start_dt` (reference implementation)
