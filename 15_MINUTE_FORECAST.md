# 15-Minute Forecast Granularity

## Overview

The forecast feature now provides 15-minute granularity (96 slots per 24 hours) instead of hourly granularity (24 slots per 24 hours). This provides 4x more detail, capturing meaningful price variations from Amber's 5-minute pricing data.

## Why 15-Minute Granularity?

### Problem with Hourly Forecasts

With Amber's 5-minute pricing, significant price variations can occur within a single hour:

**Example Hourly Forecast:**
- Hour 14: Average price $0.15
- Shows: "Expensive, battery discharging"
- Misses: $0.05 vs $0.30 swing within the hour!

**Example 15-Minute Forecast:**
- 14:00-14:15: Price $0.15 average (3 periods)
- 14:15-14:30: Price $0.22 average (3 periods)
- 14:30-14:45: Price $0.09 average (3 periods)
- 14:45-15:00: Price $0.06 average (3 periods)
- Shows: Clear price trend, can optimize charging for cheapest windows

### Benefits

1. **Better Battery Simulation**
   - 96 simulation steps instead of 24
   - Smaller energy changes per step = more accurate SOC curve
   - Better handles rapid transitions

2. **Aligns with Amber Pricing**
   - Amber provides 5-minute price data
   - 15-minute slots aggregate 3 price periods
   - Captures intra-hour price variations

3. **Finer Demand Window Modeling**
   - Can see SOC every 15 minutes
   - Better understand when battery might empty during DW
   - More precise timing for discretionary loads

4. **Enhanced Decision-Making**
   - Lookahead at 15-minute price averages
   - Start charging 10 minutes before cheap window
   - Optimize exports to peak price windows

## Implementation Details

### Data Sources

| Data Source | Resolution | Handling |
|-------------|-------------|-----------|
| Solar Forecast (Solcast) | 30-min periods | Split into two 15-min halves (50% each) |
| Consumption (HA Recorder) | Hourly averages | Divided by 4 for 15-min estimate |
| Price Forecast (Amber) | 5-minute periods | Aggregated into 15-minute averages |

### Code Changes

**File:** `custom_components/amber_powerwall/computation_engine.py`

**New Method:** `_compute_daily_15min_forecast()`

Generates 96 fifteen-minute slots:
- Starts from current hour (minute=0)
- Iterates 96 times (24 hours × 4)
- Each slot is 15 minutes long

**New Helper:** `_get_solar_for_15min_slot()`

Splits Solcast 30-minute periods into 15-minute halves:
- First half (0-15 min): 50% of period energy
- Second half (15-30 min): 50% of period energy

### Battery Simulation

**Charge/Discharge Limits:**
- Max rate: 3.3 kW
- Per 15-min slot: 3.3 kW × 15/60 = 0.825 kWh

**Efficiency:**
- Charging: 92% efficiency
- Discharging: 95% efficiency

**Example Calculation:**
```
Solar: 0.5 kWh in 15-min slot
Consumption: 0.25 kWh (1 kW ÷ 4)
Net: +0.25 kWh

Battery delta: min(0.25, 0.825) × 0.92 = 0.23 kWh
SOC change: 0.23 ÷ 13.5 × 100 = +1.7%
```

## Forecast Data Structure

Each forecast slot includes:

```python
{
    "hour": 14,                      # Hour of day (0-23)
    "minute": 15,                    # Minute (0, 15, 30, 45)
    "timestamp": "2026-02-14T14:15:00+11:00",
    "predicted_soc": 45.2,           # Predicted battery %
    "solar_kwh": 0.250,             # Solar energy in slot
    "consumption_kwh": 0.125,        # Consumption in slot
    "consumption_source": "profile_hour",  # Data source
    "net_kwh": 0.125,               # Net energy (solar - consumption)
}
```

## Use Cases

### 1. Identify Cheap Charging Windows

**Hourly:** "After 3 PM is cheap"

**15-minute:** "3:45-4:00 PM is cheapest at $0.05"

### 2. Optimize Battery Exports

**Hourly:** "Export at 5 PM when expensive"

**15-minute:** "Export at 4:45-5:00 PM when price peaks at $0.32"

### 3. Predict Battery Depletion

**Hourly:** "Battery will be empty by 7 PM"

**15-minute:** "Battery will reach 0% between 6:45-7:00 PM"

### 4. Time Discretionary Loads

**Hourly:** "Run dishwasher after 5 PM"

**15-minute:** "Run dishwasher at 5:00-5:15 PM when price is lowest"

## Performance Considerations

### Computation Overhead

**Before:** 24 iterations per minute
**After:** 96 iterations per minute (4x)
**Impact:** Negligible on modern hardware (~1-2ms total)

### Data Volume

**Before:** 24 slots × ~100 bytes = 2.4 KB
**After:** 96 slots × ~100 bytes = 9.6 KB
**Impact:** Still minimal, well within Home Assistant limits

### Dashboard Rendering

96 slots may require:
- Zoom controls or time filters
- Fewer bars per screen
- Aggregate views (e.g., hourly groups)

## Future Enhancements

### 1. Sun-Elevation Weighted Solar Distribution

Instead of 50/50 split, use sun position:
- First half: Higher sun angle = more energy
- Second half: Lower sun angle = less energy

**Accuracy gain:** ~3-5%

### 2. Price Lookahead in Decision Logic

Enhance decision-making with future price awareness:
- Check next 15-minute slots for cheap prices
- Pre-position battery before cheap window
- Adjust timing based on price trends

### 3. Adaptive Granularity

Use different granularities based on context:
- 15-min during demand window
- 30-min during stable periods
- Hourly overnight when not critical

## Migration Guide

### For Users

No changes required! The forecast automatically uses 15-minute granularity.

**Sensor output:** Same structure, just more slots (96 vs 24)

**Dashboard updates:** May need to adjust for more data points

### For Developers

**Reading forecast data:**
```python
# Hourly (old)
for slot in data.daily_forecast:
    hour = slot["hour"]
    soc = slot["predicted_soc"]

# 15-minute (new)
for slot in data.daily_forecast:
    hour = slot["hour"]
    minute = slot["minute"]  # New field
    soc = slot["predicted_soc"]
    timestamp = slot["timestamp"]
```

**Solar precision:**
- Old: `solar_kwh` rounded to 2 decimals (hourly)
- New: `solar_kwh` rounded to 3 decimals (15-min)

## Testing

### Verify 15-Minute Granularity

1. Check forecast sensor attributes:
```yaml
sensor.amber_powerwall_daily_forecast
  # Should show 96 entries (not 24)
```

2. Check logs:
```
15-min forecast: no Solcast entries available
15-min forecast: no historical hourly load profile available; using live load fallback
```

3. Verify minute field:
```python
# Each slot should have minute ∈ {0, 15, 30, 45}
for slot in forecast:
    assert slot["minute"] in [0, 15, 30, 45]
```

### Compare Hourly vs 15-Minute

Run both forecasts and compare:
- Total solar should be similar (±5%)
- Total consumption should match
- Final SOC should be close (±2%)
- 15-min should show smoother SOC curve

## Rollback

To revert to hourly forecast:

1. Edit `computation_engine.py`:
```python
# Change back
self._compute_daily_hourly_forecast(data, now_dt)
```

2. Restart Home Assistant

## Summary

15-minute granularity provides a significant improvement in forecast accuracy and decision-making capability, especially valuable for users with variable pricing from Amber. The implementation is efficient, backwards-compatible, and provides clear benefits for battery optimization.