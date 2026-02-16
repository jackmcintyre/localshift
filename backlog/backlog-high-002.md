# Missing Tomorrow's Forecast Integration

**ID:** backlog-high-002  
**Priority:** HIGH  
**Status:** COMPLETED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Consolidated forecast sources - derived simple values from detailed 15-min forecast which already includes tomorrow's data.

---

## Analysis

The original backlog item noted that `solcast_tomorrow` was not used in the simple `solar_battery_forecast` calculation.

However, during analysis we found:

1. **Simple forecast** (`_compute_solar_battery_forecast`): Only used `solcast_today` - this is the issue the backlog item describes
2. **Detailed 15-min forecast** (`forecast_computer.compute_forecast`): Already uses BOTH `solcast_today` and `solcast_tomorrow`:
   ```python
   all_solcast = [*data.solcast_today, *data.solcast_tomorrow]
   ```

---

## Resolution

Rather than fixing the simple forecast to include tomorrow's data, we consolidated to use the detailed 15-min forecast as the single source of truth.

**Changes made:**

1. Added `_get_forecast_at_demand_window()` helper method to find forecast entry at DW time
2. Reordered computation steps so detailed forecast runs first (Step 4/16)
3. Derived `solar_can_reach_target` and `boost_charge_needed` from detailed forecast (Step 5)
4. Kept legacy `_compute_solar_battery_forecast()` for backwards compatibility (Step 4)

**Benefits:**
- Single source of truth for all forecast values
- Tomorrow's solar data is now automatically included
- Binary sensors continue to work identically
- Better code maintainability (no duplicate logic)

---

## Affected Files

- `custom_components/amber_powerwall/computation_engine.py` - consolidated forecast computation
