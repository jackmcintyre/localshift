# Demand Window Block Logic Priority Issue

**ID:** backlog-high-006  
**Priority:** HIGH  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Price spike discharge should take priority over demand window block, but the logic may not be correct.

---

## Description

The current logic sets DEMAND_BLOCK whenever active, even if price_spike is detected. For financial optimization, spike discharge should take priority over demand block - exporting during a spike is more valuable than avoiding imports.

Note: Looking at current code, spike IS checked first - this issue may already be resolved.

---

## Affected Files

- `custom_components/amber_powerwall/coordinator.py` (Step 14: active_mode calculation, line ~475)

---

## Proposed Solution

Reorder priority check so price_spike is evaluated BEFORE demand_window_active:
```python
elif d.price_spike and spike_discharge_enabled and in_discharge_window:
    d.active_mode = BatteryMode.SPIKE_DISCHARGE
elif d.demand_window_active:
    d.active_mode = BatteryMode.DEMAND_BLOCK
```

---

## Notes

May already be resolved - verify in code.
