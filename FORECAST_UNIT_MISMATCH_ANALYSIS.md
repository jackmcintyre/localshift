# Forecast Unit Mismatch and Hybrid Timescale Bug Analysis

## Problem Statement

The battery forecast intermittently produces incorrect predictions where:
1. The battery SOC drops to the reserve limit (10%) overnight around 00:45
2. The SOC then stays flat at 10% for the remainder of the forecast
3. Grid charging is incorrectly triggered (or not triggered) based on wrong simulation results

## Units in Forecast Table

The table columns have the following units:

| Column | Unit | Description |
|--------|------|-------------|
| Solar | kWh | Energy per slot (5-min or 15-min depending on timescale region) |
| Load | kWh | Energy per slot (consumption) |
| Net | kWh | Solar - Load per slot |
| Grid In | kWh | Grid import per slot |
| Grid Out | kWh | Grid export per slot |
| Buy $ | $/kWh | Spot buy price |
| Sell $ | $/kWh | Feed-in tariff price |

**Example verification:**
- Solar at 14:00 shows 0.718 kWh for a 15-min slot
- This represents ~2.87 kW average power (0.718 kWh ÷ 0.25h)
- Load at 14:00 shows 0.186 kWh → ~0.74 kW average
- Net = 0.718 - 0.186 = 0.532 kWh ✓

## Root Cause: Hybrid Timescale Boundary Drift

### The Bug

The forecast uses a **hybrid timescale** with:
- **Near-term (0-2h):** 24 × 5-minute slots
- **Long-term (2-24h):** 88 × 15-minute slots

However, the `base_slot` is derived from the **current time** rounded to 5 minutes:

```python
# In compute_forecast()
current_5min = (now_dt.minute // 5) * 5
base_slot = now_dt.replace(minute=current_5min, second=0, microsecond=0)
near_term_end = base_slot + timedelta(minutes=5 * NEAR_TERM_COUNT)  # 120 min later
```

### Why This Causes Intermittent Failures

**When coordinator runs at 14:00:**
- `base_slot = 14:00`
- `near_term_end = 16:00` (exactly 2 hours later)
- Long-term slots start at 16:00, 16:15, 16:30... ✓ **Aligned with Solcast 30-min periods**

**When coordinator runs at 14:07:**
- `base_slot = 14:05`
- `near_term_end = 16:05` 
- Long-term slots start at 16:05, 16:20, 16:35... ✗ **MISALIGNED with Solcast periods**

### Evidence from Forecast Tables

**Good Forecast (aligned):**
```
16:00 | 100.0 | 1.082 | 0.480 | 0.602 | ...  # Slot aligned with Solcast period
16:15 | 100.0 | 1.082 | 0.480 | 0.602 |
16:30 | 100.0 | 0.937 | 0.480 | 0.457 |
```

**Bad Forecast (misaligned):**
```
16:00 | 100.0 | 0.361 | 0.163 | 0.198 | ...  # Wrong slot time, wrong values
16:10 | 100.0 | 1.082 | 0.488 | 0.594 | ...  # 10 minutes off!
16:25 | 100.0 | 0.985 | 0.488 | 0.498 |
```

Notice the bad forecast has slots at 16:00, 16:10, 16:25, 16:40... instead of 16:00, 16:15, 16:30, 16:45...

### Secondary Bug: Simulation Function Mismatch

The `_simulate_future_soc_with_solar_only()` function (used for grid charging decisions) uses **only 15-minute slots**:

```python
total_slots = int((sim_end - base_slot).total_seconds() // (15 * 60))
for offset in range(total_slots):
    slot_start = base_slot + timedelta(minutes=15 * offset)
    solar_kwh = get_solar_for_15min_slot(all_solcast, slot_start)
```

But the main forecast loop uses **hybrid timescale**:
- 5-min slots for near-term (different solar function!)
- 15-min slots for long-term

This causes the simulation to give **different SOC predictions** than the actual forecast, leading to incorrect grid charging decisions.

## Impact on Forecast Accuracy

1. **Solar Energy Miscalculation:** When slots are misaligned with Solcast's 30-min periods, the overlap calculation in `get_solar_for_15min_slot()` returns incorrect values

2. **Double-Counting or Missing Solar:** A 15-min slot straddling two Solcast periods may get partial credit for both, or miss part of the solar from one period

3. **Simulation-Forecast Divergence:** The simulation functions (`_simulate_future_soc_with_solar_only`, `_simulate_overnight_drain_to_solar`, etc.) use hardcoded 15-min slots, but the actual forecast uses hybrid timescale. This means:
   - Simulation predicts SOC will reach target → no grid charging
   - Actual forecast shows battery draining to reserve

## Why It's Intermittent

The bug only manifests when:
1. Coordinator runs at a time where `base_slot % 15 != 0` (e.g., 14:05, 14:10, 14:20, etc.)
2. AND the simulation starts from a slot that's misaligned with Solcast periods

When coordinator runs at :00, :15, :30, :45, everything aligns and forecasts are correct.

## Proposed Fix

### Option 1: Align Long-Term Slots to 15-Minute Boundaries

Round `near_term_end` to the next 15-minute boundary before starting long-term slots:

```python
# Round near_term_end UP to next 15-min boundary
near_term_end_minutes = near_term_end.hour * 60 + near_term_end.minute
aligned_minutes = ((near_term_end_minutes + 14) // 15) * 15
aligned_hour = aligned_minutes // 60
aligned_minute = aligned_minutes % 60
near_term_end_aligned = near_term_end.replace(hour=aligned_hour, minute=aligned_minute)
```

### Option 2: Use Hybrid Timescale in Simulation Functions

Update `_simulate_future_soc_with_solar_only()` and other simulation functions to use the same hybrid timescale pattern as the main forecast loop.

### Option 3: Always Use 5-Min Slots (Simpler but More Computation)

Remove hybrid timescale entirely and use 5-min slots throughout. This matches Amber's pricing granularity and ensures alignment with all data sources.

## Affected Files

- `custom_components/localshift/computation_engine_lib/forecast_computer.py`
  - `compute_forecast()` - main forecast loop
  - `_simulate_future_soc_with_solar_only()` - simulation function
  - `_simulate_overnight_drain_to_solar()` - overnight simulation
  - `_find_battery_fill_point()` - fill point calculation

## Recommendation

**Option 1** is recommended as the fix:
1. It's the minimal change required
2. It maintains the performance benefit of 15-min slots for long-term forecasting
3. It ensures alignment with Solcast 30-min periods
4. It makes simulation functions consistent with the main forecast

## Test Cases Needed

1. Coordinator firing at :00, :05, :10, :15, :20, :25, :30, :35, :40, :45, :50, :55
2. Verify all produce identical long-term slot times (aligned to :00, :15, :30, :45)
3. Verify simulation SOC predictions match main forecast SOC predictions
4. Verify grid charging decisions are consistent regardless of coordinator timing