# Implementation Plan: Negative FIT Avoidance Algorithm Fix

[Overview]
Fix the negative FIT avoidance algorithm to properly time exports BEFORE the negative FIT window, decrement headroom after exports, and enforce SOC buffer constraints.

The current implementation has three critical bugs causing exports at wrong times and wrong SOC levels:
1. Headroom never decrements after exports, allowing unlimited exports
2. Exports happen AFTER the negative FIT window instead of BEFORE
3. No SOC buffer enforced (exports at min+0.6% instead of min+5%)

[Types]

No new types required. The fix modifies existing function behavior and parameters.

Constants to add:
```python
# In forecast_computer.py
PROACTIVE_EXPORT_SOC_BUFFER_PCT = 5.0  # Minimum buffer above min SOC for exports
```

[Files]

### Files to Modify

1. **`custom_components/localshift/computation_engine_lib/forecast_computer.py`**
   
   **Changes in `compute_forecast()` method:**
   - Initialize `remaining_negative_fit_headroom_kwh` as a mutable variable
   - Decrement `remaining_negative_fit_headroom_kwh` after each proactive export
   - Pass `first_negative_fit_start` datetime for timing check
   - Pass `remaining_negative_fit_headroom_kwh` (mutable) instead of static `negative_fit_headroom_kwh`
   
   **Changes in `_should_proactive_export_at_slot()` method:**
   - Add `first_negative_fit_start: datetime | None` parameter
   - Add `remaining_headroom_kwh: float` parameter (mutated by caller via return)
   - Add timing check: only export if `slot_start < first_negative_fit_start`
   - Add SOC buffer check: require `predicted_soc >= export_min_soc_pct + 5%`
   - Return updated `remaining_headroom_kwh` as third return value

[Functions]

### Modified Functions

1. **`_should_proactive_export_at_slot()`** (`forecast_computer.py`, ~line 1100)
   
   **Current signature:**
   ```python
   def _should_proactive_export_at_slot(
       self,
       slot_start: datetime,
       slot_hour: int,
       solar_kwh: float,
       slot_fit_price: float,
       predicted_soc: float,
       target_pct: float,
       in_demand_window: bool,
       forecasted_excess_kwh: float,
       remaining_export_budget_kwh: float,
       feed_in_forecast: list[dict],
       min_soc_no_exports: float,
       export_min_soc_pct: float,
       effective_cheap_price: float,
       feed_in_price_current: float,
       all_solcast: list[dict] | None = None,
       historical_avg_kw: dict[int, float] | None = None,
       current_load_kw: float = 0.0,
       recent_load_kw: float = 0.0,
       is_current_slot: bool = False,
       current_elapsed_minutes: float = 0,
       fill_point_elapsed_minutes: int | None = None,
       negative_fit_headroom_kwh: float = 0.0,
       in_negative_fit_window: bool = False,
   ) -> tuple[bool, float]:
   ```
   
   **New signature:**
   ```python
   def _should_proactive_export_at_slot(
       self,
       slot_start: datetime,
       slot_hour: int,
       solar_kwh: float,
       slot_fit_price: float,
       predicted_soc: float,
       target_pct: float,
       in_demand_window: bool,
       forecasted_excess_kwh: float,
       remaining_export_budget_kwh: float,
       feed_in_forecast: list[dict],
       min_soc_no_exports: float,
       export_min_soc_pct: float,
       effective_cheap_price: float,
       feed_in_price_current: float,
       all_solcast: list[dict] | None = None,
       historical_avg_kw: dict[int, float] | None = None,
       current_load_kw: float = 0.0,
       recent_load_kw: float = 0.0,
       is_current_slot: bool = False,
       current_elapsed_minutes: float = 0,
       fill_point_elapsed_minutes: int | None = None,
       negative_fit_headroom_kwh: float = 0.0,  # Now mutated via return
       in_negative_fit_window: bool = False,
       first_negative_fit_start: datetime | None = None,  # NEW: timing gate
   ) -> tuple[bool, float, float]:  # NEW: returns (should_export, amount, updated_headroom)
   ```
   
   **Key logic changes:**
   ```python
   # MODE 1: NEGATIVE FIT AVOIDANCE
   if negative_fit_headroom_kwh > 0 and not in_negative_fit_window:
       # NEW: Only export BEFORE the negative FIT window starts
       if first_negative_fit_start is not None and slot_start >= first_negative_fit_start:
           _LOGGER.debug(
               "PROACTIVE_EXPORT: %02d:%02d BLOCKED - slot is at/after negative FIT window start %s",
               slot_hour, slot_start.minute, first_negative_fit_start.strftime("%H:%M")
           )
           return False, 0.0, negative_fit_headroom_kwh
       
       # NEW: Require SOC buffer (min + 5%)
       required_soc_for_export = export_min_soc_pct + PROACTIVE_EXPORT_SOC_BUFFER_PCT
       if predicted_soc < required_soc_for_export:
           _LOGGER.debug(
               "PROACTIVE_EXPORT: %02d:%02d BLOCKED - SOC %.1f%% < min+buffer %.1f%%",
               slot_hour, slot_start.minute, predicted_soc, required_soc_for_export
           )
           return False, 0.0, negative_fit_headroom_kwh
       
       # ... existing FIT > 0 check ...
       
       # Calculate export amount
       export_amount = min(negative_fit_headroom_kwh, available_above_min_kwh, max_export_rate_kwh)
       
       if export_amount > 0:
           # NEW: Decrement headroom
           updated_headroom = negative_fit_headroom_kwh - export_amount
           _LOGGER.info(
               "PROACTIVE_EXPORT: %02d:%02d NEGATIVE_FIT_AVOIDANCE - FIT=$%.3f, headroom=%.2f->%.2f kWh, exporting=%.3f kWh, SOC=%.1f%%",
               slot_hour, slot_start.minute, use_price,
               negative_fit_headroom_kwh, updated_headroom, export_amount, predicted_soc
           )
           return True, round(export_amount, 3), updated_headroom
       
       return False, 0.0, negative_fit_headroom_kwh
   ```

2. **`compute_forecast()`** (`forecast_computer.py`, ~line 1400)
   
   **Changes:**
   ```python
   # Find first negative FIT window start time
   first_negative_fit_start = None
   if negative_fit_windows:
       first_negative_fit_start = negative_fit_windows[0][0]  # (start, end, min_price)
   
   # Initialize mutable headroom
   remaining_negative_fit_headroom_kwh = negative_fit_headroom_kwh
   
   # In the slot loop, update call:
   should_proactive_export, proactive_export_amount, remaining_negative_fit_headroom_kwh = (
       self._should_proactive_export_at_slot(
           # ... existing params ...
           negative_fit_headroom_kwh=remaining_negative_fit_headroom_kwh,  # Pass mutable
           in_negative_fit_window=in_negative_fit_window,
           first_negative_fit_start=first_negative_fit_start,  # NEW
       )
   )
   ```

[Classes]

No class changes required. Only method modifications.

[Dependencies]

No new dependencies.

[Testing]

### Test Cases Required

1. **Headroom decrements after export**
   - Given: headroom = 5.0 kWh
   - When: export 2.0 kWh
   - Then: remaining_headroom = 3.0 kWh

2. **No export after negative FIT window starts**
   - Given: negative FIT window starts at 08:00
   - When: slot_time = 08:15
   - Then: should_export = False

3. **Export before negative FIT window**
   - Given: negative FIT window starts at 08:00, headroom = 5 kWh, SOC = 50%
   - When: slot_time = 07:45, FIT = $0.10
   - Then: should_export = True

4. **No export when SOC below buffer**
   - Given: min_soc = 10%, buffer = 5%, SOC = 12%
   - When: headroom > 0, FIT > 0
   - Then: should_export = False (SOC < 15%)

5. **Export stops when headroom exhausted**
   - Given: headroom = 1.0 kWh
   - When: export 1.0 kWh
   - Then: remaining_headroom = 0, subsequent exports blocked

[Implementation Order]

1. Add `PROACTIVE_EXPORT_SOC_BUFFER_PCT = 5.0` constant
2. Modify `_should_proactive_export_at_slot()` signature and logic
3. Modify `compute_forecast()` to track first_negative_fit_start
4. Modify `compute_forecast()` to use mutable headroom variable
5. Update the function call in the slot loop
6. Add/update unit tests
7. Run integration tests with high-solar-negative-fit scenario