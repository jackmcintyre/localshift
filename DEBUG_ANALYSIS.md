# Debug Analysis - 2026-02-14 14:48

## Issues Identified from Debug Summary

### 1. ❌ SOC Forecast Over 100% (CRITICAL)
**Symptom**: `Solar Battery Forecast SOC: 114.0%`

**Root Cause**: In `_compute_solar_battery_forecast()` (computation_engine.py, line ~200):
```python
predicted_soc = data.soc + net_solar_pct
```
This line does NOT clamp the predicted SOC to 100%, unlike the daily forecast which properly clamps:
```python
predicted_soc = max(0.0, min(100.0, predicted_soc))
```

**Impact**: 
- Causes unrealistic forecast data
- May affect decision logic (though it seems to not break things due to later clamping in daily forecast)
- Confusing for users

**Fix**: Add clamping in `_compute_solar_battery_forecast()`:
```python
predicted_soc = max(0.0, min(100.0, data.soc + net_solar_pct))
```

---

### 2. ❌ Missing FIT Data
**Symptom**: `Solar Weighted Avg FIT: $unknown/kWh`

**Root Cause**: In `_compute_solar_weighted_avg_fit()` (computation_engine.py, line ~580):
- The function checks `if after_dw:` and returns 0.0 if true
- But the real issue is likely that the `feed_in_forecast` data is empty or not matching
- The sensor shows "unknown" which suggests `data.solar_weighted_avg_fit` is not being set

Looking at the code, if `total_solar > 0`, it sets `data.solar_weighted_avg_fit`. Otherwise it sets 0.0.
The "unknown" suggests the sensor entity itself might have an issue, OR the computation is returning None.

**Impact**:
- Solar export hold decisions may be suboptimal
- Cannot determine if FIT is above average

**Investigation Needed**:
- Check if `feed_in_forecast` has data
- Check if `data.solar_weighted_avg_fit` is actually being set

---

### 3. ❌ Empty Decision Log
**Symptom**: `Latest Decision: None`, `Recent History: [empty]`

**Root Cause**: Decision log only updates when mode CHANGES:
```python
if (
    data.active_mode != self._previous_active_mode
    and self._previous_active_mode is not None
):
    self._add_to_decision_log(data, now_dt)
```

The system is stuck in `self_consumption` mode and never transitions to `hold` even though `hold_justified: on`. Since there's no mode change, no decision log entry is created.

**Impact**:
- No visibility into why decisions are (or aren't) being made
- Difficult to debug

**Fix**: Add decision log entries even when no transition occurs, explaining WHY no transition happened.

---

### 4. ❌ Hold Mode Not Triggered Despite Hold Justified
**Symptom**: 
- `Hold Justified: on`
- `Hold Mode: False`
- `Active Mode: self_consumption`

**Root Cause**: In `_compute_active_mode()` (computation_engine.py, line ~620):

The logic checks conditions in this order:
1. Price below effective cheap price → go to GRID_CHARGING or BOOST_CHARGING
2. Price below cheap charge stop price → if currently charging, stay charging; else check if hold_justified
3. Forecast spike → HOLDING_FOR_SPIKE
4. Else → SELF_CONSUMPTION

**Current State**:
- `Buy Price: $0.11/kWh`
- `Effective Cheap Price: $0.08/kWh`
- `Cheap Charge Stop Price: $0.11/kWh`
- `Hold Justified: on`

The price ($0.11) is **NOT** below effective cheap price ($0.08), so it doesn't trigger charging logic.
The price ($0.11) is **equal to** cheap charge stop price ($0.11), so it should check hold_justified.

**BUT**: Looking at the code more carefully:
```python
elif data.general_price < data.cheap_charge_stop_price:
    if data.force_charge_active:
        # stay in charging
    else:
        if data.hold_justified:
            data.active_mode = BatteryMode.HOLD
        else:
            data.active_mode = BatteryMode.SELF_CONSUMPTION
```

The condition is `data.general_price < data.cheap_charge_stop_price` (STRICT LESS THAN).

Since $0.11 is NOT strictly less than $0.11, it skips this entire block and falls through to:
```python
else:
    data.active_mode = BatteryMode.SELF_CONSUMPTION
```

**Impact**:
- System stays in self_consumption when it should be holding
- Battery continues discharging when it should preserve charge
- Suboptimal cost savings

**Fix**: Change condition to `data.general_price <= data.cheap_charge_stop_price` (LESS THAN OR EQUAL) OR add an explicit check for `hold_justified` in the final else block.

---

## Summary of Required Fixes

1. **Clamp SOC forecast** in `_compute_solar_battery_forecast()`
2. **Investigate FIT forecast data** - may not be a code fix but a data issue
3. **Add decision log entries** even when no transition occurs
4. **Fix hold mode logic** - use `<=` instead of `<` OR add explicit hold_justified check

## Priority

1. **HIGH**: Fix #4 (Hold mode not triggered) - affects cost savings
2. **MEDIUM**: Fix #1 (SOC forecast) - affects forecast accuracy
3. **MEDIUM**: Fix #3 (Decision log) - affects debuggability
4. **LOW**: Fix #2 (FIT data) - may be environmental, not code