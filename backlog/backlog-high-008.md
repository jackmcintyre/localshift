# Proactive Export Not Using Peak FIT Prices

**ID:** backlog-high-008  
**Priority:** HIGH  
**Status:** COMPLETED
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Proactive export triggering at suboptimal prices - uses full 24h FIT window instead of focusing on current evening/night window

---

## Description

The proactive export logic in `_should_proactive_export_at_slot()` calculates the FIT threshold using the full 24-hour window (including tomorrow's solar period). This causes exports to trigger at lower prices when higher prices are still available in the current evening period.

### Problem Scenario

User's data (18:30 onwards):
- 18:30-19:45: Sell price = 0.11-0.13 (higher!)
- 20:00-20:45: Sell price = 0.13 → 0.09
- 21:00: Proactive export triggers at 0.09

**Issue**: Export triggered at 0.09 when 0.11-0.13 was available earlier. This is backwards!

### Root Cause

In `forecast_computer.py`, `_should_proactive_export_at_slot()`:

```python
# Current: Uses full 24h window (includes tomorrow's solar peak ~0.14)
max_fit_price = self._calculate_max_fit_price(
    feed_in_forecast, slot_start, hours=24
)

# Threshold = 0.14 * 0.8 = 0.112
export_threshold = max_fit_price * 0.8

# At 21:00: 0.09 >= 0.112? NO - but export still triggered!
# Need to investigate why...
```

The 24h window includes tomorrow's midday peak (~0.14) which inflates the threshold unrealistically.

### Expected Behavior

- Find MAX FIT in the **current evening period** (next 4-8 hours)
- Only export when current price is at/near that evening peak
- Don't export at 0.09 when 0.11-0.13 was available earlier

---

## Affected Files

- `custom_components/amber_powerwall/computation_engine_lib/forecast_computer.py` - `_should_proactive_export_at_slot()` method

---

## Steps to Reproduce

1. Evening period with decreasing FIT prices (e.g., 0.13 → 0.09)
2. Tomorrow has higher solar FIT period (~0.14)
3. Current logic uses tomorrow's peak (0.14) to set threshold
4. Export triggers at 0.09 when higher prices (0.11-0.13) were available

---

## Proposed Solution

1. **Change threshold calculation**: Use evening window only (next 4-6 hours) instead of full 24h
2. **Add "best price remaining" check**: Before exporting, check if a better price is coming in the next 2-3 hours
3. **Add price comparison**: If current price < best price in window, don't export

```python
# Proposed: Use shorter window for threshold
max_fit_price_evening = self._calculate_max_fit_price(
    feed_in_forecast, slot_start, hours=6  # Only evening/night
)

export_threshold = max_fit_price_evening * 0.8
```

---

## Notes

- This is a revenue optimization issue, not a bug - system still functions
- The "make space" logic (avoiding negative FIT) should be balanced with maximizing export revenue
- Consider adding configuration for export window hours

---

## Related Items

- backlog-high-009: Solar Curtailment for Negative FIT (related - negative FIT handling)
- Related to FORECAST_DRIVEN_CONTROL.md design document
