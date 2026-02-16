# Proactive Export Not Using Peak FIT Prices

**ID:** backlog-high-008  
**Priority:** HIGH  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Proactive export not triggering at financially optimal times - uses 60th percentile FIT instead of finding peak FIT

---

## Description

The proactive export logic in `_should_proactive_export_at_slot()` currently uses a 60th percentile FIT price threshold to decide when to export. This is suboptimal because:

1. **Current logic**: "Export if current FIT >= 60th percentile" - only ensures "decent" price
2. **Expected behavior**: Find the MAXIMUM FIT in the forecast window and only export when at/near that peak

### Root Cause

In `forecast_computer.py`, the method `_should_proactive_export_at_slot()` calculates:

```python
percentile_fit_price = self._calculate_percentile_fit_price(
    feed_in_forecast, slot_start, percentile=60.0, hours=24
)

if slot_fit_price < percentile_fit_price:
    return False, 0.0
```

This should instead find the MAXIMUM FIT and compare to that.

### Example from User Data

At 16:00:
- Current Sell: $0.12/kWh
- Peak FIT later: ~$0.15
- Current logic: Might export at $0.12 (if >= 60th percentile)
- Expected: Should WAIT until closer to peak ($0.15) for maximum revenue

---

## Affected Files

- `custom_components/amber_powerwall/computation_engine_lib/forecast_computer.py` - `_should_proactive_export_at_slot()` method

---

## Steps to Reproduce

1. Have battery at <100% SOC during afternoon
2. Forecast shows FIT prices rising later (e.g., $0.08 now → $0.15 peak)
3. Current logic exports at $0.08 (above 60th percentile)
4. Should wait and export at $0.15 (at peak)

---

## Proposed Solution

Replace the 60th percentile check with a maximum FIT check:

```python
def _calculate_max_fit_price(
    self,
    feed_in_forecast: list[dict],
    start_time: datetime,
    hours: int = 24,
) -> float:
    """Calculate maximum FIT price over forecast window."""
    prices = []
    base_slot = start_time.replace(minute=0, second=0, microsecond=0)

    for offset in range(hours * 12):  # 5-min intervals
        slot_time = base_slot + timedelta(minutes=5 * offset)
        price = get_price_for_slot(feed_in_forecast, slot_time)
        if price is not None:
            prices.append(price)

    if not prices:
        return 0.0

    return max(prices)
```

Then in `_should_proactive_export_at_slot()`:

```python
# Find maximum FIT in forecast window
max_fit_price = self._calculate_max_fit_price(
    feed_in_forecast, slot_start, hours=24
)

# Only export when at or near peak (e.g., within 20% of max)
# This ensures we export at financially optimal times
export_threshold = max_fit_price * 0.8  # 80% of peak

if slot_fit_price < export_threshold:
    return False, 0.0
```

---

## Notes

- This is a revenue optimization issue, not a bug - system still functions
- Self-consumption mode already handles 100% SOC case automatically (excess solar exports)
- Consider adding configuration option for export threshold (e.g., 70%, 80%, 90% of peak)

---

## Related Items

- None directly related
- Related to FORECAST_DRIVEN_CONTROL.md design document
