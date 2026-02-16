# Backlog Item: Forecast Consumption Not Blending Recent Load Data

**ID:** backlog-high-016  
**Priority:** HIGH  
**Status:** COMPLETED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

All 96 forecast slots use `profile_hour` fallback instead of blending recent load data, making the forecast less responsive to current conditions.

---

## Description

The debug summary shows `Consumption Source Counts: {'profile_hour': 96}` — every single 15-min forecast slot is using the pure historical profile path. The `weighted_load` branch in `ForecastComputer._estimate_hourly_consumption_kw()` is never entered.

The `weighted_load` path requires ALL three conditions:
1. `recent_load_kw > 0` (from `recent_load_1hr_kw` on CoordinatorData)
2. `recent_weight > 0` (from `CONF_LOAD_WEIGHT_RECENT` config option)
3. Historical data exists for the hour

Condition 3 is met (all 24 hours have profile data). The issue is that `recent_load_1hr_kw` is `0.0`, meaning the 1-hour statistics query returned no data. This could be caused by:

1. The load entity's statistic ID not being found in the HA statistics system
2. The statistics recorder not having accumulated enough data for the entity
3. A misconfigured load entity that doesn't record statistics
4. The diagnostic fields not being propagated (see backlog-med-007), masking the actual error

Additionally, `Recent 1hr Samples: 0` and `Recent 1hr Statistic ID: (empty)` confirm the stats query isn't working, but these diagnostic values aren't being propagated to CoordinatorData (see backlog-med-007), so the root cause error message is hidden.

---

## Affected Files

- `custom_components/amber_powerwall/computation_engine_lib/history_fetcher.py` — `async_get_recent_load_1hr()` stats query
- `custom_components/amber_powerwall/computation_engine_lib/forecast_computer.py` — `_estimate_hourly_consumption_kw()` (lines ~93-122)
- `custom_components/amber_powerwall/coordinator.py` — load entity configuration

---

## Steps to Reproduce

1. Check debug summary for `Consumption Source Counts`
2. Observe all slots show `profile_hour` instead of `weighted_load`
3. Check `Recent 1hr Load: 0.0 kW` and `Recent 1hr Samples: 0`

---

## Proposed Solution

1. First fix backlog-med-007 to propagate diagnostic fields so the root cause error is visible
2. Investigate why `async_get_recent_load_1hr()` returns 0 — check if the configured load entity has a valid statistic ID
3. Consider adding a warning log when the recent load query fails, so the issue is visible in HA logs
4. Consider adding a fallback to use `load_power_kw` (the live sensor reading) when statistics are unavailable

---

## Notes

- The forecast still functions using historical profile data, but it cannot adapt to real-time load changes
- The `consumption_weighting` is set to 0.8, confirming the user wants weighted blending — it just can't activate
- Fixing backlog-med-007 first will reveal the actual error message from the stats query

---

## Related Items

- backlog-med-007 (Recent Load Diagnostic Fields Not Propagated to CoordinatorData)
- backlog-med-004 (Missing Cleanup for Historical Load Cache)