# Missing Tomorrow's Forecast Integration

**ID:** backlog-high-002  
**Priority:** HIGH  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

The solcast_tomorrow data is read but never used in solar_battery_forecast calculations.

---

## Description

The `solcast_tomorrow` data is read and stored but NEVER used in solar_battery_forecast calculations. Only today's forecast is considered, potentially missing important overnight solar contributions for early morning demand windows.

---

## Affected Files

- `custom_components/amber_powerwall/coordinator.py` - `_compute_derived_values` (Step 4: solar_battery_forecast)

---

## Proposed Solution

Modify `_sum_solar_before_target` to include tomorrow's forecast when target_hour is earlier than the current hour (e.g., target at 15:00, currently 20:00).

---

## Notes

This would improve early morning demand window predictions.
