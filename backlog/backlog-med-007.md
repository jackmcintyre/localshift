# Backlog Item: Recent Load Diagnostic Fields Not Propagated to CoordinatorData

**ID:** backlog-med-007  
**Priority:** MED  
**Status:** COMPLETED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

`recent_load_1hr_statistic_id`, `recent_load_1hr_samples`, and `recent_load_1hr_last_error` are computed by `HistoryFetcher` but never written to `CoordinatorData`, making it impossible to diagnose consumption forecast issues from the dashboard.

---

## Description

The data flow for recent load diagnostics is broken:

1. **`HistoryFetcher.async_get_recent_load_1hr()`** correctly computes and stores results on its own instance:
   - `self._recent_load_1hr_kw` — the kW value
   - `self._recent_load_1hr_statistic_id` — the resolved statistic ID
   - `self._recent_load_1hr_samples` — number of samples found
   - `self._recent_load_1hr_last_error` — any error message

2. **`ComputationEngine`** has properties that proxy these values from the `HistoryFetcher`.

3. **`ForecastComputer.compute_forecast()`** transfers the values to `CoordinatorData` — but **only `recent_load_1hr_kw`** is written. The other three diagnostic fields are never transferred.

4. **`DailyForecastSensor`** reads from `CoordinatorData` to expose these as entity attributes, so the dashboard always shows defaults: empty string for statistic ID, 0 for samples, empty string for error.

The debug output confirms this:
```
Recent 1hr Load: 0.0 kW        ← propagated (but 0 because query failed)
Recent 1hr Statistic ID:        ← NOT propagated (always empty)
Recent 1hr Samples: 0           ← NOT propagated (always 0)
Recent 1hr Error:                ← NOT propagated (always empty)
```

This makes it impossible to determine WHY the recent load query is failing, which blocks investigation of backlog-high-016.

---

## Affected Files

- `custom_components/amber_powerwall/computation_engine_lib/forecast_computer.py` — `compute_forecast()` (~line 520) — needs to propagate the three additional fields
- `custom_components/amber_powerwall/coordinator_data.py` — fields exist but are never written to
- `custom_components/amber_powerwall/computation_engine.py` — has the proxy properties that need to be called

---

## Steps to Reproduce

1. Check debug summary for `Recent 1hr Statistic ID`, `Recent 1hr Samples`, `Recent 1hr Error`
2. Observe they are always empty/zero regardless of actual HistoryFetcher state

---

## Proposed Solution

In `ForecastComputer.compute_forecast()`, after writing `recent_load_1hr_kw`, also propagate the diagnostic fields:

```python
# Already exists:
data.recent_load_1hr_kw = self._engine.recent_load_1hr_kw

# Add these:
data.recent_load_1hr_statistic_id = self._engine.recent_load_1hr_statistic_id
data.recent_load_1hr_samples = self._engine.recent_load_1hr_samples
data.recent_load_1hr_last_error = self._engine.recent_load_1hr_last_error
```

---

## Notes

- This is a prerequisite for properly debugging backlog-high-016 (forecast not blending recent load)
- The fields already exist on `CoordinatorData` with sensible defaults — they just need to be written to

---

## Related Items

- backlog-high-016 (Forecast Consumption Not Blending Recent Load Data)
- backlog-med-004 (Missing Cleanup for Historical Load Cache)