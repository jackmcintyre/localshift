# Spot Price vs Forecast Price Mismatch for Proactive Export

**ID:** backlog-high-020  
**Priority:** HIGH  
**Status:** PROPOSED  
**Created:** 2026-02-17  
**Updated:** 2026-02-17  

---

## Summary

Proactive export decisions rely on forecast FIT prices only, missing opportunities when actual spot price is positive but forecast predicted otherwise.

---

## Description

This morning, the system missed an opportunity to export proactively while the feed-in price was positive. The root cause is that the proactive export logic in `_should_proactive_export_at_slot()` uses only forecast prices (`feed_in_forecast`), not the current spot price (`feed_in_price`).

### Problem Scenario

1. Amber's forecast predicted FIT would stay positive → no negative windows found
2. System's `_find_negative_fit_windows()` returned empty → no proactive export triggered
3. Actual spot price from Amber differed from forecast (was positive, allowing export)
4. User missed revenue opportunity

### Asymmetry in Code

| Decision | Buy Price (Grid Charging) | Sell Price (Proactive Export) |
|----------|---------------------------|-------------------------------|
| **Primary** | Forecast (`general_forecast`) | Forecast (`feed_in_forecast`) |
| **Fallback** | ✅ Uses spot (`general_price`) | ❌ **Missing** |
| **Location** | `computation_engine.py` Ln ~520 | `forecast_computer.py` Ln ~450 |

The grid charging logic in `_compute_active_mode()` has a fallback that uses current spot price (`general_price`) when forecast is unavailable. However, the proactive export logic has NO such fallback.

### Affected Files

- `custom_components/amber_powerwall/computation_engine_lib/forecast_computer.py` - `_should_proactive_export_at_slot()` method
- `custom_components/amber_powerwall/computation_engine.py` - `_compute_active_mode()` method

---

## Steps to Reproduce

1. Forecast shows no negative FIT window coming (so proactive export not planned)
2. Actual spot price from Amber differs from forecast
3. Spot price is positive and would allow profitable export
4. System doesn't export because forecast predicted no need

---

## Proposed Solution

Add spot price fallback for proactive export decisions:

### Option 1: In `_should_proactive_export_at_slot()`

Add current spot price check alongside forecast:

```python
def _should_proactive_export_at_slot(
    self,
    slot_start: datetime,
    # ... existing params ...
    feed_in_price_current: float,  # NEW: Current spot price
) -> tuple[bool, float]:
    # Check if current spot price indicates export opportunity
    # even if forecast shows no negative window
    
    # Existing forecast-based logic
    negative_windows = self._find_negative_fit_windows(...)
    
    # NEW: If forecast says no negative, but spot is positive,
    # still consider export based on spot price
    if not negative_windows and feed_in_price_current > 0:
        # Use spot price for decision instead
        # Similar threshold logic but using feed_in_price_current
```

### Option 2: In `_compute_active_mode()`

Add secondary check after forecast-based logic:

```python
# In _compute_active_mode(), after forecast_entry check:
# FORECAST-DRIVED: Proactive export (before negative feed-in prices)
elif forecast_entry.get("proactive_export"):
    # ... existing logic ...

# NEW: Spot price fallback for proactive export
# If forecast didn't trigger export, but current spot is good, consider it
elif self._should_export_on_spot_price(data):
    data.active_mode = BatteryMode.PROACTIVE_EXPORT
    data.proactive_export_active = True
```

---

## Notes

- This is related to but different from backlog-high-008 (Proactive Export Not Using Peak FIT Prices)
- backlog-high-008 deals with threshold calculation (24h vs 6h window)
- This item deals with forecast vs spot price mismatch
- Consider adding configuration option to enable/disable spot price fallback

---

## Related Items

- backlog-high-008: Proactive Export Not Using Peak FIT Prices (completed)
- backlog-high-009: Solar Curtailment for Negative FIT (proposed)
