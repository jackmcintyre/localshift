# Implementation Plan

[Overview]
Refactor the main forecast loop in `compute_forecast()` to use hybrid timescale slots (5-min and 30-min) instead of fixed 15-min slots.

The hybrid timescale foundation is complete (`compute_hybrid_slot_schedule()`, `get_price_for_slot_with_source()`, `get_solar_for_slot_by_interval()`), but the main forecast loop still iterates over 96 fixed 15-min slots. This plan addresses the gap to complete Issue #351.

[Types]
No new types required. The existing `hybrid_slots` structure from `compute_hybrid_slot_schedule()` is already defined:

```python
# Each slot in hybrid_slots:
{
    "start": datetime,           # Slot start time
    "interval_minutes": int,     # 5 or 30
    "price": float,              # Price in $/kWh
    "price_source": str,         # "5min" or "30min"
}

# Metadata:
{
    "timezone": str,
    "slot_intervals": {"5min": int, "30min": int},
    "transition_boundary": str | None,
    "total_slots": int,
}
```

[Files]
Single file modification required:

- **`custom_components/localshift/computation_engine_lib/forecast_computer.py`**
  - Modify `compute_forecast()` method (lines ~900-1500)
  - Replace fixed `TOTAL_SLOTS = 96` iteration with `hybrid_slots` iteration
  - Update Pass 1, Pass 2, Pass 3, Pass 4 to use variable slot durations
  - Add `slot_interval_minutes` and `price_source` to output dictionary

[Functions]
Single function modification:

- **`ForecastComputer.compute_forecast()`** (forecast_computer.py)
  - **Current behavior**: Iterates `for slot_idx in range(TOTAL_SLOTS)` with fixed 15-min slots
  - **Required changes**:
    1. Replace `for slot_idx in range(TOTAL_SLOTS)` with `for slot in hybrid_slots`
    2. Use `slot["start"]` instead of `base_slot + timedelta(minutes=15 * slot_idx)`
    3. Use `slot["interval_minutes"]` for time calculations
    4. Use `slot["price"]` and `slot["price_source"]` from hybrid slots
    5. Use `get_solar_for_slot_by_interval()` with variable duration
    6. Update `slot_fraction = slot["interval_minutes"] / 60.0`
    7. Add `slot_interval_minutes` and `price_source` to output dict

  - **Pass 1 (Grid charge candidates)**: Refactor to iterate over hybrid_slots
  - **Pass 2 (Price optimization)**: Update slot indexing for scheduled_grid_charges
  - **Pass 3 (Build forecast)**: Use hybrid_slots for output generation
  - **Pass 4 (DW verification)**: Update to work with variable slots

[Classes]
No class modifications required.

[Dependencies]
No new dependencies. All required functions already exist:
- `compute_hybrid_slot_schedule()` - Already implemented
- `get_price_for_slot_with_source()` - Already implemented
- `get_solar_for_slot_by_interval()` - Already implemented

[Testing]
Existing tests should pass with minimal changes:

1. **`tests/test_hybrid_timescale.py`** - 11 tests already pass
2. **`tests/test_forecast_computer.py`** - May need updates for new output fields
3. **`tests/test_scenarios.py`** - Scenario tests should pass

Key test considerations:
- Mock data must include `duration` field in price forecasts
- Output dict now includes `slot_interval_minutes` and `price_source`
- Grid charging logic must work with 5-min and 30-min slots

[Implementation Order]
Sequential implementation to minimize risk:

1. **Step 1: Update Pass 3 (Build forecast output)**
   - This is the simplest change - just output the hybrid slot data
   - Add `slot_interval_minutes` and `price_source` to output dict
   - Verify sensor shows correct values

2. **Step 2: Update Pass 1 (Grid charge candidates)**
   - Refactor candidate collection to iterate over hybrid_slots
   - Use variable slot durations for solar/load calculations
   - Verify grid charging decisions work correctly

3. **Step 3: Update Pass 2 (Price optimization)**
   - Update scheduled_grid_charges to use hybrid slot indexing
   - Handle variable slot durations in price sorting

4. **Step 4: Update Pass 4 (DW verification)**
   - Update DW start slot finding for variable slots
   - Verify gap filling works with hybrid timescale

5. **Step 5: Run full test suite**
   - Fix any test failures
   - Update test mocks if needed

6. **Step 6: Deploy and verify**
   - Check logs for errors
   - Verify sensor output shows correct `slot_intervals`
   - Verify `price_source` is "5min" or "30min" (not "unknown")

[Key Implementation Details]

### Slot Iteration Pattern Change

**Before (fixed 15-min):**
```python
for slot_idx in range(TOTAL_SLOTS):
    slot_start = base_slot + timedelta(minutes=15 * slot_idx)
    slot_fraction = 15 / 60.0
    # ... calculations
```

**After (hybrid):**
```python
for slot in hybrid_slots:
    slot_start = slot["start"]
    interval_minutes = slot["interval_minutes"]
    slot_fraction = interval_minutes / 60.0
    price = slot["price"]
    price_source = slot["price_source"]
    # ... calculations
```

### Grid Charging with Variable Slots

Grid charging rate calculations must scale to slot duration:
```python
max_grid_charge_kwh = CHARGE_RATE_GRID_KW * slot_fraction  # Scales with duration
```

### Solar Retrieval Change

**Before:**
```python
solar_kwh = get_solar_for_15min_slot(all_solcast, slot_start)
```

**After:**
```python
solar_kwh = get_solar_for_slot_by_interval(all_solcast, slot_start, interval_minutes)
```

### Output Dictionary Update

Add new fields to each slot in `daily_forecast`:
```python
{
    # ... existing fields
    "slot_interval_minutes": interval_minutes,  # 5 or 30
    "price_source": price_source,               # "5min" or "30min"
}
```

[Risk Assessment]

**Low Risk:**
- Output format change (additive - new fields)
- Solar retrieval function already tested

**Medium Risk:**
- Grid charging logic with variable slots
- Price optimization with variable slots

**Mitigation:**
- Implement in order (Pass 3 first for quick validation)
- Run tests after each step
- Deploy incrementally