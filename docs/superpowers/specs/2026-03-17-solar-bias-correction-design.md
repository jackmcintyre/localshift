# Solar Bias Correction Fix Design

**Date**: 2026-03-17
**Issues**: #756, #757
**Status**: Approved

## Problem Statement

The solar forecast bias correction system is overcorrecting forecasts by approximately 44% higher than Solcast predictions. Root cause analysis identified:

1. **No minimum sample gate** — Corrections are applied with only 1 sample collected
2. **Double correction** — Both `solar_confidence_factor` and `bias_correction` are applied to the same forecast
3. **Extreme bias values** — Current production shows `overall_bias: -7.23` (723% overcorrection)

### Current Production State

```
sensor.localshift_solar_forecast_accuracy:
  sample_count: 1
  overall_bias: -7.23
  accuracy: 12.15%
  mape: 87.85%
```

### Comparison with Other Subsystems

| Subsystem | Minimum Samples |
|-----------|-----------------|
| `forecast/corrections.py` | 10 |
| `engine/pattern_analyzer.py` | 10 |
| `engine/parameters.py` | 50 |
| `learning/correlation.py` | 7 days |
| **`forecast/solar_accuracy.py`** | **0** ❌ |

## Solution Design

### 1. Add Minimum Sample Gate

Add `MIN_SOLAR_CORRECTION_SAMPLES = 20` constant and enforce it in all correction methods.

**Rationale**: 20 samples provides statistical significance while not requiring excessive training time. With 48 half-hour periods per day, 20 samples represents approximately 10 days of matching time/weather/season contexts.

### 2. Add Bounds to Bias Correction

Clamp the multiplicative correction factor to `[0.5, 1.5]`, matching the existing `solar_confidence_factor` bounds.

**Rationale**: Even with 20+ samples, systematic errors in data collection could produce misleading bias values. Bounds ensure we never make catastrophic adjustments.

### 3. Bias Correction Supersedes solar_confidence_factor

When bias correction has ≥20 samples, use it exclusively. When <20 samples, fall back to `solar_confidence_factor`.

**Rationale**: Prevents double correction. The bias correction system is more accurate (context-aware by time/weather/season) when it has sufficient data.

### 4. Keep Additive Correction (Deferred to RFC #760)

Additive correction remains in place with the same sample gate. Removal is tracked separately in GitHub issue #760.

## API Changes

### New Constant

```python
# In forecast/solar_accuracy.py
MIN_SOLAR_CORRECTION_SAMPLES = 20
```

### New Method

```python
class SolarAccuracyTracker:
    def has_sufficient_samples(self) -> bool:
        """Check if we have enough samples for bias correction."""
        return self._metrics.sample_count >= MIN_SOLAR_CORRECTION_SAMPLES
```

### Modified Methods

#### `SolarAccuracyTracker.get_bias_correction()`

```python
def get_bias_correction(self, time_of_day, weather, season=None) -> float:
    """Get bias correction factor for given context.
    
    Returns a multiplier clamped to [0.5, 1.5].
    Returns 1.0 if insufficient samples (< MIN_SOLAR_CORRECTION_SAMPLES).
    """
    result = self._compute_context_bias(time_of_day, weather, season)
    if result is None:
        return 1.0
    
    weighted_bias, sample_count = result
    if sample_count < MIN_SOLAR_CORRECTION_SAMPLES:
        return 1.0  # Not enough data
    
    correction = 1.0 - weighted_bias
    return max(0.5, min(1.5, correction))  # Clamp to bounds
```

#### `SolarAccuracyTracker.get_additive_correction()`

```python
def get_additive_correction(self, time_of_day, weather, season=None) -> float:
    """Get additive correction offset for given context.
    
    Returns 0.0 if insufficient samples (< MIN_SOLAR_CORRECTION_SAMPLES).
    """
    result = self._compute_context_additive_bias(time_of_day, weather, season)
    if result is None:
        return 0.0
    
    weighted_bias, sample_count = result
    if sample_count < MIN_SOLAR_CORRECTION_SAMPLES:
        return 0.0  # Not enough data
    
    return max(-MAX_ADDITIVE_OFFSET_KWH, min(MAX_ADDITIVE_OFFSET_KWH, weighted_bias))
```

#### `SlotBuilder._get_solar_kwh()`

```python
def _get_solar_kwh(
    self,
    all_solcast: list[dict[str, Any]],
    slot_start: datetime,
    interval_minutes: int,
    solar_confidence_factor: float,
) -> float:
    """Get solar kWh for a slot.
    
    If bias correction has sufficient samples, return raw solar
    (bias correction will be applied by OptimizerFacade).
    Otherwise, apply solar_confidence_factor as fallback.
    """
    raw_solar = get_solar_for_slot_by_interval(all_solcast, slot_start, interval_minutes)
    
    # Check if bias correction is ready
    if self._solar_accuracy_tracker and self._solar_accuracy_tracker.has_sufficient_samples():
        # Bias correction will handle it - return raw
        return max(0.0, raw_solar)
    
    # Fall back to solar_confidence_factor
    return max(0.0, raw_solar * solar_confidence_factor)
```

#### `SlotBuilder.__init__()`

```python
def __init__(
    self,
    config_options: dict[str, Any],
    ha_timezone: str,
    solar_accuracy_tracker: SolarAccuracyTracker | None = None,
) -> None:
    """Initialize SlotBuilder.
    
    Args:
        config_options: Integration config options.
        ha_timezone: Home Assistant timezone string.
        solar_accuracy_tracker: Optional tracker for bias correction readiness check.
    """
    self._config_options = config_options
    self._ha_timezone = ha_timezone
    self._solar_accuracy_tracker = solar_accuracy_tracker
```

## Data Flow

### Before (Current)

```
Solcast forecast
    ↓
SlotBuilder: solar_kwh *= solar_confidence_factor  ← Always applied
    ↓
OptimizerFacade: apply_bias_correction(multiplicative + additive)  ← Always applied
    ↓
Optimizer
```

### After (Proposed)

```
Solcast forecast
    ↓
SlotBuilder:
    if sample_count >= 20:
        return raw_solar  # Bias correction will handle it
    else:
        return raw_solar * solar_confidence_factor  # Fallback
    ↓
OptimizerFacade:
    if sample_count >= 20:
        apply_bias_correction(multiplicative + additive, clamped [0.5, 1.5])
    else:
        no-op (already handled by solar_confidence_factor)
    ↓
Optimizer
```

## Error Handling

| Scenario | Handling |
|----------|----------|
| Tracker not set | Fall back to `solar_confidence_factor` |
| Sample count drops | Sample gate prevents correction |
| Extreme bias values | Bounds [0.5, 1.5] prevent catastrophic corrections |
| Context not found | Return 1.0 (no correction) |

## Migration

- **No data migration needed** — Existing stored samples remain valid
- **Immediate effect** — Systems with <20 samples will stop overcorrecting immediately
- **Gradual activation** — Systems will naturally activate bias correction as they accumulate 20+ samples

## Testing Strategy

| Test | Description |
|------|-------------|
| `test_get_bias_correction_returns_1_with_insufficient_samples` | Verify sample gate works |
| `test_get_bias_correction_clamped_to_bounds` | Verify [0.5, 1.5] bounds |
| `test_get_bias_correction_returns_1_with_no_data` | Verify None handling |
| `test_get_additive_correction_returns_0_with_insufficient_samples` | Verify additive gate |
| `test_has_sufficient_samples_true_when_enough` | Verify helper method |
| `test_has_sufficient_samples_false_when_not_enough` | Verify helper method |
| `test_slot_builder_uses_solar_confidence_factor_when_bias_not_ready` | Verify fallback |
| `test_slot_builder_skips_solar_confidence_factor_when_bias_ready` | Verify precedence |
| `test_end_to_end_correction_flow` | Integration test |

## Out of Scope

The following are tracked separately:

- **RFC #760**: Remove additive correction entirely
- Changes to how bias is calculated
- Changes to the multiplicative + additive formula

## References

- Issue #756: Solar forecast showing ~44% higher than Solcast
- Issue #757: Root cause analysis and proposed solution
- RFC #760: Remove additive correction
