# Change Detection System Design

## Requirements

The system should:
1. **Watch for significant changes** in key variables
2. **Regenerate forecast** only when changes materially affect decisions
3. **Maintain accuracy** - forecast must be "absolutely current"
4. **Have backup timer** - regenerate at least once per minute
5. **Be efficient** - skip unnecessary recomputations (~70% waste currently)

## Variable Monitoring Strategy

### Monitored Variables

| Variable | Change Required | Reason |
|----------|----------------|---------|
| **Buy Price** (`general_price`) | ANY change | Grid charging decisions |
| **Feed-in Price** (`feed_in_price`) | ANY change | Export decisions |
| **SOC** (`soc`) | ≥1% change | Battery state affects charging |
| **Solar Forecast** | Skip detection | Caught by 1-minute timer |
| **Age** | >1 minute | Backup for missed changes |

### Rationale for Choices

**Price = ANY change:**
- Prices fluctuate frequently
- Small changes ($0.01) can change charging decisions
- No reliable threshold that captures all significant changes
- User explicitly requested: "price threshold should be very sensitive"

**SOC = 1% threshold:**
- SOC changes 0.1% → 0.2% → 0.3% happen often (noise)
- 1% change (1.35 kWh) materially affects decisions
- Battery capacity is 13.5 kWh, so 1% = 0.135 kWh
- Conservative: won't miss important changes

**Solar forecast = skip detection:**
- Solar forecasts are relatively stable
- Complex to hash arrays of dicts
- 1-minute timer catches all changes anyway
- User explicitly requested: "dont worry about solar forecast"

**Age = 1 minute backup:**
- Ensures forecast is never more than 1 minute old
- Catches any missed changes or edge cases
- Safety net for system

## Implementation

### Class Structure

**Location**: `custom_components/localshift/computation_engine_lib/change_tracker.py`

```python
from datetime import datetime, timedelta

class ForecastChangeTracker:
    """Tracks when forecast should regenerate based on significant changes."""
    
    def __init__(self) -> None:
        """Initialize change tracker."""
        self._last_soc: float = -1.0  # -1 = not initialized
        self._last_price: float = -1.0
        self._last_feed_in: float = -1.0
        self._last_forecast_time: datetime | None = None
        
        # Change thresholds (hardcoded, no config needed)
        self._SOC_THRESHOLD = 1.0  # 1% SOC change
        self._MAX_FORECAST_AGE = timedelta(minutes=1)
```

### Core Method

```python
def should_recompute_forecast(
    self,
    soc: float,
    price: float,
    feed_in_price: float,
    now_dt: datetime,
    force: bool = False,
) -> tuple[bool, str]:
    """Check if forecast should recompute.
    
    Args:
        soc: Current battery SOC percentage
        price: Current buy price ($/kWh)
        feed_in_price: Current feed-in price ($/kWh)
        now_dt: Current datetime
        force: If True, skip checks and recompute
        
    Returns:
        (should_recompute, reason)
        reason is a string for logging (e.g., "price_change_0.15")
    """
    # Force recompute (e.g., mode change, startup)
    if force:
        self._update_cache(soc, price, feed_in_price, now_dt)
        return True, "forced"
    
    # First run: no cached values
    if self._last_soc < 0:
        self._update_cache(soc, price, feed_in_price, now_dt)
        return True, "first_run"
    
    # Price changes (ANY change = recalc)
    if price != self._last_price:
        reason = f"price_change_{price:.2f}"
        self._update_cache(soc, price, feed_in_price, now_dt)
        return True, reason
    
    if feed_in_price != self._last_feed_in:
        reason = f"fit_change_{feed_in_price:.2f}"
        self._update_cache(soc, price, feed_in_price, now_dt)
        return True, reason
    
    # SOC change (1% threshold)
    soc_change = abs(soc - self._last_soc)
    if soc_change >= self._SOC_THRESHOLD:
        reason = f"soc_change_{soc_change:.1f}%"
        self._update_cache(soc, price, feed_in_price, now_dt)
        return True, reason
    
    # Age check (1-minute backup timer)
    if self._last_forecast_time is not None:
        age = now_dt - self._last_forecast_time
        if age > self._MAX_FORECAST_AGE:
            reason = f"age_{age.total_seconds():.0f}s"
            self._update_cache(soc, price, feed_in_price, now_dt)
            return True, reason
    
    # No significant changes
    return False, "no_change"

def _update_cache(
    self,
    soc: float,
    price: float,
    feed_in_price: float,
    now_dt: datetime,
) -> None:
    """Update cached values after recompute."""
    self._last_soc = soc
    self._last_price = price
    self._last_feed_in = feed_in_price
    self._last_forecast_time = now_dt
```

### Integration with Computation Engine

**Location**: `computation_engine.py` in `ComputationEngine` class (orchestration layer)

**Add to `__init__()`:**
```python
def __init__(self, ...) -> None:
    # ... existing init ...
    
    # Change tracker for forecast regeneration
    self._forecast_change_tracker = ForecastChangeTracker()
```

**Modify `_compute_daily_15min_forecast()`:**
```python
def _compute_daily_15min_forecast(
    self,
    data: CoordinatorData,
    now_dt: datetime,
) -> None:
    """Compute full 24-hour forecast with 15-minute breakdown.
    
    Uses change detection to skip unnecessary recomputations.
    """
    # Check if recompute is needed
    should_recompute, reason = (
        self._forecast_change_tracker.should_recompute_forecast(
            soc=data.soc,
            price=data.general_price,
            feed_in_price=data.feed_in_price,
            now_dt=now_dt,
        )
    )
    
    if should_recompute:
        _LOGGER.info("Recomputing forecast: %s", reason)
        
        # ... existing forecast computation ...
        (
            data.daily_forecast,
            data.daily_forecast_soc_15min,
            data.forecast_consumption_source_counts,
        ) = self._forecast_computer.compute_forecast(
            data=data,
            now_dt=now_dt,
            historical_avg_kw=hourly_avg_kw,
            recent_load_kw=recent_load_kw,
            historical_load_source=self._historical_load_source,
            historical_load_sample_counts=self._historical_load_sample_counts,
        )
        
        # Also keep a compact 24-entry hourly view
        data.daily_forecast_hourly = build_hourly_forecast_summary(
            data.daily_forecast
        )
    else:
        _LOGGER.debug("Forecast unchanged, skipping recompute")
```

**Add force recompute for mode changes:**
```python
async def async_force_forecast_recompute(self) -> None:
    """Force forecast recompute (e.g., after mode change)."""
    now_dt = dt_util.now()
    should_recompute, reason = (
        self._forecast_change_tracker.should_recompute_forecast(
            soc=self._data.soc,  # Use current data
            price=self._data.general_price,
            feed_in_price=self._data.feed_in_price,
            now_dt=now_dt,
            force=True,
        )
    )
```

## Performance Considerations

### Expected Reduction in Computations

**Current behavior:**
- 1-minute periodic tick: 1 recompute/minute
- State changes: ~15-20/hour (SOC, price, etc.)
- Total: ~16-21 recomputes/hour

**With change detection:**
- Price changes: ~5-10/hour (5-minute price intervals)
- SOC changes (1%): ~5-10/hour
- 1-minute timer: ~60/hour (backup, mostly skipped)
- Total: ~10-20 recomputes/hour

**Expected savings:** ~40-60% (depends on volatility)

### Computation Cost

Each forecast recompute:
- 112 iterations (24 × 5-min near-term + 88 × 15-min long-term)
- Solar lookup per slot (`get_solar_for_5min_slot` for near-term, `get_solar_for_15min_slot` for long-term)
- Consumption estimate per slot (scaled by `slot_fraction = slot_minutes / 60`)
- Grid charging decision per slot
- Proactive export decision per slot
- Estimated time: ~5-10ms

**Annual savings:** ~40% × 60 recomputes/day × 10ms = ~240ms/day

Not a huge performance win, but:
- Reduces CPU usage
- Cleaner logging (less noise)
- Better for debugging (only see meaningful recomputes)

## Fallback Mechanisms

### Fallback 1: Mode Transition

When mode transitions happen, forecast should recompute:

```python
# In state_machine.py after successful transition
if transition_success:
    # Force forecast recompute
    await computation_engine.async_force_forecast_recompute()
```

### Fallback 2: Startup

First run always recomputes (no cached values):

```python
if self._last_soc < 0:
    return True, "first_run"
```

### Fallback 3: Manual Trigger

User can force recompute via button:

```python
# In coordinator.py
async def async_update_forecast(self) -> None:
    """Update forecast (manual trigger)."""
    await self._computation_engine.async_force_forecast_recompute()
```

### Fallback 4: Config Changes

When configuration options change:

```python
# In config flow after options update
await self._computation_engine.async_force_forecast_recompute()
```

## Debugging and Monitoring

### Logging

```python
if should_recompute:
    _LOGGER.info(
        "Forecast recomputed: %s (soc=%.1f%%, price=%.2f, fit=%.2f)",
        reason,
        soc,
        price,
        feed_in_price,
    )
else:
    _LOGGER.debug(
        "Forecast unchanged: soc=%.1f%% (Δ%.1f), price=%.2f (Δ%.2f), fit=%.2f (Δ%.2f)",
        soc,
        soc - self._last_soc,
        price,
        price - self._last_price,
        feed_in_price,
        feed_in_price - self._last_feed_in,
    )
```

### Metrics

Add to `CoordinatorData`:
```python
forecast_recompute_count: int = 0
forecast_recompute_reasons: dict[str, int] = {}
```

Track in `ComputationEngine`:
```python
if should_recompute:
    data.forecast_recompute_count += 1
    data.forecast_recompute_reasons[reason] = (
        data.forecast_recompute_reasons.get(reason, 0) + 1
    )
```

Exposes via sensor for monitoring:
```python
# In sensor.py
class ForecastRecomputeSensor(SensorEntity):
    """Sensor showing forecast recompute stats."""
    
    _attr_unique_id = "forecast_recompute_stats"
    _attr_name = "Forecast Recompute Stats"
    
    def _update_from_coordinator(self) -> None:
        d = self.coordinator.data
        self._attr_native_value = d.forecast_recompute_count
        self._attr_extra_state_attributes = {
            "total_recomputes": d.forecast_recompute_count,
            "reasons": d.forecast_recompute_reasons,
        }
```

## Configuration

### Hardcoded Thresholds

No user configuration needed. Thresholds are hardcoded based on requirements:

```python
class ForecastChangeTracker:
    # Change thresholds
    _SOC_THRESHOLD = 1.0              # 1% SOC change
    _MAX_FORECAST_AGE_MINUTES = 1      # Backup timer (minutes)
    
    # No price thresholds - ANY change triggers recompute
```

### Rationale for No Config

1. **User explicitly requested**: "We should not need more config"
2. **Thresholds are intuitive**: 1% SOC, 1-minute timer
3. **Price threshold = any**: User wants maximum sensitivity
4. **Simple to maintain**: No need to validate config ranges
5. **Can tune later**: If needed, can add config after monitoring

## Risks and Mitigations

| Risk | Mitigation |
|------|-------------|
| Forecast becomes stale | 1-minute backup timer |
| First run has no cached values | Check `_last_soc < 0` |
| Mode transition needs fresh forecast | Force recompute after transition |
| Config change needs fresh forecast | Force recompute after update |
| User wants to tune thresholds | Can add config later (monitor first) |
| Solar forecast changes undetected | 1-minute timer catches all |

## Future Enhancements

1. **Adaptive thresholds**: Auto-tune SOC threshold based on volatility
2. **Machine learning**: Predict when forecast will materially change
3. **Multi-battery support**: Track SOC for multiple batteries
4. **Dynamic age limit**: Adjust based on time of day (e.g., shorter during peak)
5. **Cost-based trigger**: Recompute if missed opportunity cost > threshold

## Change Log

### 2026-02-23: Modular extraction of change detection (Issue #146)

**What changed:**
- `ForecastChangeTracker` was extracted from `computation_engine.py` into `computation_engine_lib/change_tracker.py`.
- `ComputationEngine` now composes this helper via dependency wiring in `__init__`.
- Runtime imports are surfaced through `computation_engine_lib/__init__.py` for stable package-level access.

**Why:**
- Keeps `ComputationEngine` focused on orchestration instead of low-level comparison logic.
- Improves maintainability and targeted testability of forecast recompute rules.

**Behavior impact:**
- No intended functional change to recompute thresholds or decision criteria.
- Existing triggers remain: price/FIT any-change, SOC ≥1%, or age >1 minute.

### 2026-02-19: Hybrid Timescale Slot Duration Fix

**Issue:** Helper functions (`_find_battery_fill_point`, `_calculate_solar_energy_between_slots`) assumed uniform 15-minute slots throughout the entire 24-hour forecast, causing SOC accumulation to be calculated **3× too fast** in the near-term window (0-2h).

**Impact:**
- Battery fill point predicted 2-3 hours too early
- Grid charging decisions incorrectly delayed
- System predicting rapid charging while battery actually draining
- User-reported symptom: "System predicts rapid charging while battery is actually draining"

**Root Cause:**
Main forecast loop used hybrid timescale (24×5min + 88×15min) but helper functions used only 15-minute slots:
```python
# WRONG (old helper code):
for offset in range(96):  # Always 96 × 15-min = 24 hours
    slot_start = base_slot + timedelta(minutes=15 * offset)
```

**Solution Implemented:**
1. Helper functions now use same hybrid timescale as main loop:
   - Near-term (0-2h): 24 × 5-minute slots
   - Long-term (2-24h): 88 × 15-minute slots

2. Functions return elapsed minutes instead of slot offsets:
   - `_find_battery_fill_point()` returns minutes until 100% SOC
   - `_calculate_solar_energy_between_slots()` accepts elapsed minutes parameters
   - Enables time-based comparisons agnostic to slot duration

3. Solar retrieval uses appropriate granularity:
   - Near-term: `get_solar_for_5min_slot()` returns 1/6 of 30-min Solcast period
   - Long-term: `get_solar_for_15min_slot()` returns 1/2 of 30-min Solcast period

**Validation:**
- All existing tests pass (47/47)
- New hybrid timescale tests added (9 tests)
- Total: 56/56 tests passing
- Verifies fill point calculations are now accurate
- Confirms solar energy calculations match expected granularity

**Performance Impact:**
- Minimal: 112 iterations vs 96 (16% increase)
- Near-term accuracy is critical for operational decisions
- Long-term efficiency maintained with 15-min slots

**Files Modified:**
- `forecast_computer.py` - Hybrid timescale implementation in helper functions
- `tests/test_hybrid_timescale.py` - New test suite for hybrid functionality
- `docs/ARCHITECTURE.md` - Documentation of hybrid architecture
- `docs/CHANGE_DETECTION.md` - This change log entry

## References

- `ARCHITECTURE.md` - Overall system architecture
- `FORECAST_DRIVEN_CONTROL.md` - Forecast-driven control design
- `HYBRID_TIMESCALE_SLOT_DURATION_ANALYSIS.md` - Detailed analysis of the bug and solution
