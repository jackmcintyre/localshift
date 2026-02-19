# Backlog Item Template

**ID:** backlog-crit-003  
**Priority:** CRIT  
**Status:** COMPLETED  
**Created:** 2026-02-19  
**Updated:** 2026-02-19  

---

## Summary

Silent 0.0 returns hide missing forecast data.

---

## Description

The solar forecast utility functions (`get_solar_for_15min_slot()`) returned `0.0` in all of the following scenarios:

1. Empty forecast list (Solcast integration not providing data)
2. No matching period found for the requested slot
3. Genuinely zero solar forecast (nighttime)

This made it impossible to distinguish between:
- **Missing/stale data** (integration down, API errors) - requires user attention
- **Actual zero solar** (nighttime, cloudy day) - expected behavior

As a result, the system would silently continue with incorrect forecasts, potentially making bad grid charging decisions based on missing data.

### Example Log (Before Fix)
```
2026-02-19 17:05:32.333 INFO Forecasted excess: 0.00 kWh, export budget: 0.00 kWh (full 24h forecast)
2026-02-19 17:05:32.333 INFO Minimum SOC without exports: 0.0%, final SOC: 0.0%
2026-02-19 17:05:32.333 INFO Battery will not reach 100% from solar in next 24 hours
```
This could be missing data OR nighttime - no way to tell.

---

## Affected Files

- `custom_components/localshift/computation_engine_lib/solar_utils.py` (new `get_solar_for_15min_slot_or_none()` function)
- `custom_components/localshift/computation_engine_lib/forecast_computer.py` (use new function, add warning logging)

---

## Steps to Reproduce (for bugs)

1. Disable or misconfigure Solcast integration
2. Observe logs showing 0.0 kWh forecast with no warning
3. System may make incorrect grid charging decisions

---

## Proposed Solution

1. **Add `get_solar_for_15min_slot_or_none()` function** - Returns `None` when:
   - Forecast list is empty
   - No period overlaps the requested slot
   
2. **Only return `0.0` when data exists but solar is genuinely zero** (e.g., nighttime)

3. **Update `forecast_computer.py`** to:
   - Use the new `_or_none` variant
   - Track slots with missing data
   - Log a clear warning identifying the missing slots
   - Continue with graceful degradation (use 0.0 for missing data)

### Example Log (After Fix)
```
2026-02-19 17:05:32.332 WARNING 15-min forecast: no Solcast entries available - solar forecast data is missing. Check Solcast integration status.
...
2026-02-19 17:05:32.340 WARNING Solar forecast data missing for 24 slot(s): 17:05, 17:20, 17:35, ... These slots will use 0.0 kWh solar. Check Solcast integration.
```

---

## Implementation

### Changes to `solar_utils.py`

```python
def get_solar_for_15min_slot_or_none(
    solcast_forecasts: list[dict[str, Any]],
    slot_start: datetime,
) -> float | None:
    """Get solar forecast (kWh), returning None when forecast data is missing."""
    if not solcast_forecasts:
        return None
    # ... overlap logic ...
    if not found_match:
        return None
    return total_solar
```

### Changes to `forecast_computer.py`

```python
# Use _or_none variant to detect missing forecast data vs genuine zero
solar_kwh_or_none = get_solar_for_15min_slot_or_none(all_solcast, slot_start)
if solar_kwh_or_none is None:
    missing_solar_slots.append(slot_start.strftime("%H:%M"))
    solar_kwh = 0.0  # Graceful degradation
else:
    solar_kwh = solar_kwh_or_none

# At end of compute_forecast:
if missing_solar_slots:
    _LOGGER.warning(
        "Solar forecast data missing for %d slot(s): %s. "
        "These slots will use 0.0 kWh solar. Check Solcast integration.",
        len(missing_solar_slots),
        ", ".join(missing_solar_slots[:10]) + ("..." if len(missing_solar_slots) > 10 else ""),
    )
```

---

## Notes

This follows the same pattern as the existing `get_price_for_slot_or_none()` function which was already implemented for price forecasts.

The fix maintains backward compatibility - the original `get_solar_for_15min_slot()` function still exists and returns `0.0` for callers that don't need the distinction.

---

## Related Items

- None