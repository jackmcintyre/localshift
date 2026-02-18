# Hybrid Timescale Slot Duration Analysis

## Summary

The forecast system uses **hybrid timescales**:
- **Near-term (0-2h):** 24 × 5-min slots
- **Long-term (2-24h):** 88 × 15-min slots

Several helper functions **always assume 15-min slots**, creating misalignment in calculations for the near-term zone.

## Problem Description

### The Hybrid Timescale Design

From `compute_forecast()` in `forecast_computer.py`:

```python
NEAR_TERM_COUNT = 24   # 24 × 5 min  = 120 min = 2 h
LONG_TERM_COUNT = 88   # 88 × 15 min = 1320 min = 22 h   (24 h total)
near_term_end = base_slot + timedelta(minutes=5 * NEAR_TERM_COUNT)

# Build slots list
slots: list[tuple[datetime, int]] = []
for i in range(NEAR_TERM_COUNT):
    slots.append((base_slot + timedelta(minutes=5 * i), 5))  # 5-min slots
for i in range(LONG_TERM_COUNT):
    slots.append((near_term_end + timedelta(minutes=15 * i), 15))  # 15-min slots
```

### What's Handled Correctly

The main SOC tracking loop correctly handles slot durations:

```python
slot_fraction = slot_minutes / 60.0  # 0.083 for 5-min, 0.25 for 15-min
consumption_kwh = load_kw * slot_fraction
max_solar_charge_kwh = CHARGE_RATE_SOLAR_KW * slot_fraction
```

Solar retrieval is also correct:
- 5-min slots use `get_solar_for_5min_slot()` (returns 1/6 of 30-min Solcast period)
- 15-min slots use `get_solar_for_15min_slot()` (overlap-weighted)

### Functions That Always Use 15-Min Slots

| Function | Location | Impact |
|----------|----------|--------|
| `_find_battery_fill_point()` | Lines 829-883 | SOC accumulation wrong for first 2h |
| `_calculate_solar_energy_between_slots()` | Lines 884-928 | Solar totals wrong for near-term exports |
| `_simulate_future_soc_with_solar_only()` | Lines 97-189 | SOC simulation wrong if starts in near-term |
| `_simulate_overnight_drain_to_solar()` | Lines 257-308 | Drain simulation slightly off |
| `_simulate_minimum_soc_without_exports()` | Lines 710-771 | Min SOC calculation wrong |

## Concrete Example of the Bug

**Scenario:** Battery at 60% SOC at 08:00 with good solar forecast.

### What `_find_battery_fill_point()` Does (Currently)

```python
for offset in range(96):  # Always 96 × 15-min slots
    slot_start = base_slot + timedelta(minutes=15 * offset)  # Always 15-min
    solar_kwh = get_solar_for_15min_slot(all_solcast, slot_start)  # 15-min solar
    consumption_kwh = load_kw / 4  # 15-min consumption
    # ... SOC accumulation
```

**Problem:** The first 8 slots (08:00-10:00) should be 5-min slots, not 15-min:
- Each 15-min slot in simulation covers 3× the actual time
- SOC accumulation per slot is 3× too large
- Fill point is calculated **too early**

### What Actually Happens in Main Loop

```python
for slot_idx, (slot_start, slot_minutes) in enumerate(slots):
    # Slots 0-23: 5-min each (08:00-10:00)
    # Slots 24-111: 15-min each (10:00-08:00 next day)
```

The `effective_15min_offset` conversion:
```python
effective_15min_offset = int(
    (slot_start - base_slot).total_seconds() // (15 * 60)
)
```

For slot 12 (08:10):
- Elapsed: 10 minutes
- `effective_15min_offset = 10 / 15 = 0`

This means slot 12 is compared against fill point offset as if it covers 08:00-08:15, but the fill point was calculated with wrong slot durations.

## Impact Assessment

### High Impact

1. **Fill point calculation** - Battery fill time may be off by 1-2 slots
2. **Proactive export decisions** - Solar recharge calculation may be wrong for near-term exports

### Medium Impact

3. **Minimum SOC simulation** - May slightly overestimate SOC
4. **Export budget calculation** - Totals may be slightly off

### Low Impact (for current use case)

5. **Overnight grid charging** - Simulations start from solar_start (~06:00-07:00), which is well into 15-min zone
6. **Overnight drain simulation** - Runs from midnight to dawn, all 15-min slots

## Recommended Fix: Option B

**Strategy:** Pass slot duration context to helper functions.

Each function that iterates over slots receives a `slot_duration_minutes` parameter (defaulting to 15 for backward compatibility) and scales its calculations accordingly.

### Implementation Details

#### 1. Modify `_find_battery_fill_point()`

```python
def _find_battery_fill_point(
    self,
    start_soc: float,
    start_slot: datetime,
    all_solcast: list[dict],
    historical_avg_kw: dict[int, float],
    current_load_kw: float,
    recent_load_kw: float,
    slot_duration_minutes: int = 15,  # NEW PARAMETER
) -> tuple[int | None, int]:  # Return (elapsed_minutes, slot_offset)
    """Find when battery reaches 100% from solar charging.
    
    Returns:
        (elapsed_minutes, slot_offset): Minutes until fill and corresponding slot offset
    """
    soc = start_soc
    base_slot = start_slot.replace(second=0, microsecond=0)
    
    # Calculate max slots based on duration
    max_slots = int(24 * 60 / slot_duration_minutes)  # 288 for 5-min, 96 for 15-min
    
    for offset in range(max_slots):
        slot_start = base_slot + timedelta(minutes=slot_duration_minutes * offset)
        slot_hour = slot_start.hour
        
        # Use appropriate solar function based on slot duration
        if slot_duration_minutes == 5:
            solar_kwh = get_solar_for_5min_slot(all_solcast, slot_start)
        else:
            solar_kwh = get_solar_for_15min_slot(all_solcast, slot_start)
        
        load_kw, _ = self._estimate_hourly_consumption_kw(...)
        consumption_kwh = load_kw * (slot_duration_minutes / 60.0)  # Scale by duration
        
        # ... rest of SOC calculation
        
        if soc >= 100.0:
            elapsed_minutes = offset * slot_duration_minutes
            return elapsed_minutes, offset
    
    return None, 0
```

#### 2. Modify `_calculate_solar_energy_between_slots()`

```python
def _calculate_solar_energy_between_slots(
    self,
    start_offset: int,
    end_offset: int,
    base_slot: datetime,
    all_solcast: list[dict],
    historical_avg_kw: dict[int, float],
    current_load_kw: float,
    recent_load_kw: float,
    slot_duration_minutes: int = 15,  # NEW PARAMETER
) -> float:
    """Calculate net solar energy between two slot offsets."""
    net_energy = 0.0
    max_slots = int(24 * 60 / slot_duration_minutes)
    
    for offset in range(start_offset, min(end_offset, max_slots)):
        slot_start = base_slot + timedelta(minutes=slot_duration_minutes * offset)
        slot_hour = slot_start.hour
        
        # Use appropriate solar function
        if slot_duration_minutes == 5:
            solar_kwh = get_solar_for_5min_slot(all_solcast, slot_start)
        else:
            solar_kwh = get_solar_for_15min_slot(all_solcast, slot_start)
        
        load_kw, _ = self._estimate_hourly_consumption_kw(...)
        consumption_kwh = load_kw * (slot_duration_minutes / 60.0)
        net_kwh = solar_kwh - consumption_kwh
        
        if net_kwh > 0:
            net_energy += net_kwh * 0.92  # Charging efficiency
    
    return net_energy
```

#### 3. Modify `_simulate_future_soc_with_solar_only()`

```python
def _simulate_future_soc_with_solar_only(
    self,
    actual_current_soc: float,
    start_slot: datetime,
    target_pct: float,
    all_solcast: list[dict],
    historical_avg_kw: dict[int, float],
    current_load_kw: float,
    recent_load_kw: float,
    dw_start_time: time,
    end_time: datetime,
    min_soc_pct: float = 0.0,
    slot_duration_minutes: int = 15,  # NEW PARAMETER
) -> tuple[float, float, bool]:
    """Simulate future SOC trajectory with solar only."""
    # ... existing logic, but scale by slot_duration_minutes
    total_slots = int((sim_end - base_slot).total_seconds() // (slot_duration_minutes * 60))
    
    for offset in range(total_slots):
        slot_start = base_slot + timedelta(minutes=slot_duration_minutes * offset)
        # ... use appropriate solar function and scale consumption
```

#### 4. Update `compute_forecast()` Call Sites

```python
# Calculate fill point with 5-min slots for near-term accuracy
# Note: We need to handle the hybrid timescale properly
# For simplicity, use 5-min granularity for entire calculation
fill_point_elapsed_minutes, fill_point_offset = self._find_battery_fill_point(
    start_soc=current_soc,
    start_slot=base_slot,
    all_solcast=all_solcast,
    historical_avg_kw=historical_avg_kw,
    current_load_kw=data.load_power_kw,
    recent_load_kw=recent_load_kw,
    slot_duration_minutes=5,  # Use 5-min for precision
)
```

### Alternative: Unified Elapsed Time Approach

Rather than tracking slot offsets, convert everything to elapsed minutes:

```python
# Instead of:
if current_offset >= fill_point_offset:
    # Block export

# Use:
elapsed_minutes = slot_idx * slot_minutes
if elapsed_minutes >= fill_point_elapsed_minutes:
    # Block export
```

This avoids slot offset conversion complexity entirely.

## Implementation Order

1. **Modify `_find_battery_fill_point()`** first (most critical)
   - Return elapsed minutes instead of slot offset
   - Add slot duration parameter

2. **Update `compute_forecast()`** 
   - Pass 5-min slot duration
   - Convert elapsed minutes to appropriate slot index for comparisons

3. **Modify `_calculate_solar_energy_between_slots()`**
   - Add slot duration parameter
   - Scale calculations

4. **Modify simulation functions** for consistency
   - `_simulate_future_soc_with_solar_only()`
   - `_simulate_minimum_soc_without_exports()`
   - `_simulate_overnight_drain_to_solar()`

5. **Add unit tests** for 5-min slot calculations

6. **Run full test suite** to verify no regressions

## Testing Strategy

### Unit Tests

1. Test `_find_battery_fill_point()` with 5-min slots
2. Test `_calculate_solar_energy_between_slots()` with 5-min slots
3. Test elapsed minutes conversion

### Integration Tests

1. Compare fill point with 5-min vs 15-min calculations
2. Verify proactive export decisions are correct for near-term slots
3. Verify overnight grid charging still works correctly

## Backward Compatibility

All changes add optional parameters with default values of 15 minutes, ensuring backward compatibility with any code that doesn't specify slot duration.

## Related Files

- `custom_components/localshift/computation_engine_lib/forecast_computer.py` - Main file to modify
- `custom_components/localshift/computation_engine_lib/solar_utils.py` - Already supports both 5-min and 15-min solar retrieval

## References

- `15_MINUTE_FORECAST.md` - Original design documentation
- `MODE_SWITCHING_DELAY_ANALYSIS.md` - Related timing analysis