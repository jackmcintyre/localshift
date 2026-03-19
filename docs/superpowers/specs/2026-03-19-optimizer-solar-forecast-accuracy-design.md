# Optimizer Solar Forecast Accuracy and Visibility Improvements

**Date:** 2026-03-19  
**Status:** Design Approved  
**Issue:** Optimizer plans insufficient charging due to over-optimistic solar forecasts and lacks real-time visibility

## Problem Statement

### Current Issues

1. **Optimizer Summary Sensor Not Updating**
   - `sensor.localshift_optimizer_summary` only updated once per day (observed: single update at 02:59 AEDT)
   - Terminal shortfall shown as 61% but not visible in real-time
   - Users cannot track plan degradation throughout the day
   - Makes debugging and monitoring impossible

2. **Over-Optimistic Solar Forecast Credits**
   - Terminal cost calculation uses `effective_soc = soc + projected_solar_gain_pct`
   - Solar forecasts consistently 37-63% MAPE (error rate)
   - Early morning forecasts show 100% accuracy, degrade to 37-51% by mid-morning
   - Optimizer thinks solar will reach 95% target when it only reaches 67%
   - Results in insufficient grid charging before demand windows

3. **Missing Diagnostic Visibility**
   - No way to see `projected_solar_gain_pct`, `effective_soc`, or accuracy discount
   - Cannot diagnose why optimizer makes specific charging decisions
   - No visibility into which constraints are blocking charging

### Real-World Impact (Yesterday's Incident)

**Timeline:**
- 03:00 AEDT: Forecast accuracy 100%, forecast battery 100%
- 08:00 AEDT: Forecast accuracy drops, forecast battery 89%
- 09:00 AEDT: Forecast accuracy 44%, **forecast battery drops to 68%** (critical)
- 11:00 AEDT: Forecast accuracy 50%, forecast battery 67%
- 11:23 AEDT: **Manual intervention required** - battery at 10% SOC, plan insufficient

**Result:** User had to manually force charging because optimizer relied on solar forecasts that didn't materialize.

## Design Philosophy

**Conservative charging bias**: When forecast quality is poor, prefer grid charging over relying on uncertain solar predictions.

**Transparency first**: Expose optimizer internals to enable validation, debugging, and user confidence.

**Adaptive behavior**: Use existing accuracy tracking to dynamically adjust solar crediting based on observed quality.

## Existing Solar Forecast Mechanisms

### Already Implemented

1. **Solar Confidence Factor** (`solar_confidence_factor`, default 1.0)
   - Static multiplicative discount, user-configurable
   - Applied when bias correction not ready (<20 samples)
   - Fallback mechanism only

2. **Solar Bias Correction** (context-aware historical tracking)
   - Tracks forecast vs actual by time-of-day, weather, season
   - Applies multiplicative correction factor [0.5, 1.5]
   - Requires ≥20 samples, exponential decay 7-day half-life
   - Applied at slot-building stage

3. **Cloud Event Scale Factor** (real-time weather adjustment)
   - 30-minute window response to detected cloud cover
   - Immediate forecast reduction when cloud events occur

4. **Solar Opportunity Penalty** (economic disincentive)
   - Discourages grid charging when future solar available
   - Uses raw forecasts, no discount applied

5. **Global Solar Sufficiency Gate** (hard constraint)
   - Simulates solar-only charging to demand window
   - Blocks grid charging if simulation reaches target
   - Uses raw forecasts, no discount applied

### Design Gap Identified

**None of the existing mechanisms apply a safety margin to the solar gains used in terminal cost calculations.**

The terminal cost calculation (`engine/core.py` lines 520-538) uses full `projected_solar_gain_pct` even when forecasts are unreliable. This is the root cause of insufficient charging.

## Solution Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────┐
│                    Optimizer Facade                          │
│  - Runs DP optimizer every 1-5 minutes                      │
│  - NEW: Updates summary sensor after each run               │
│  - NEW: Computes diagnostic metrics                         │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                 DP Planner (core.py)                         │
│  - Terminal cost calculation                                 │
│  - NEW: Applies forecast accuracy discount to solar gains   │
│  - NEW: Returns diagnostic data with result                 │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│            Solar Accuracy Tracker (existing)                 │
│  - Tracks forecast vs actual (MAPE)                         │
│  - NEW: Exposes accuracy for terminal cost discount         │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│         Optimizer Summary Sensor (sensor.py)                 │
│  - NEW: Real-time updates after each optimization           │
│  - NEW: Diagnostic attributes (solar gains, discounts)      │
└─────────────────────────────────────────────────────────────┘
```

### Three Components

## Component 1: Optimizer Summary Sensor Real-Time Updates

### Objective

Update `sensor.localshift_optimizer_summary` immediately after each optimizer run so users can track plan changes in real-time.

### Current Behavior

Sensor is updated as part of coordinator data cycle, which may not align with optimizer computation frequency. Results in stale data being displayed.

### New Behavior

After each successful optimizer run:
1. Extract metrics from `OptimizerResult`
2. Build attributes dict with all metrics
3. Update sensor via coordinator method
4. Home Assistant displays updated state immediately

### New Sensor Attributes

```python
{
    # Existing
    "terminal_shortfall_pct": 26.75,
    "computed_at": "2026-03-19T13:38:00",
    "solve_status": "optimal",
    
    # NEW: Plan summary metrics
    "peak_soc_pct": 94.51,              # Maximum SOC in plan
    "dw_entry_soc_pct": 92.31,          # SOC at demand window entry
    
    # NEW: Solar forecast diagnostics
    "projected_solar_gain_pct": 27.5,   # Raw solar projection
    "forecast_accuracy": 0.37,           # Current accuracy (0-1)
    "accuracy_discount_factor": 0.5,     # Applied discount (clamped)
    "adjusted_solar_gain_pct": 13.75,   # Discounted projection
    "effective_soc_at_terminal": 78.0,  # SOC used for terminal cost
}
```

### Implementation

**Location:** `engine/optimizer_facade.py`

**New Method:**
```python
def _update_optimizer_summary_sensor(
    self,
    result: OptimizerResult,
    diagnostics: dict[str, Any],
) -> None:
    """Update optimizer summary sensor with latest plan metrics."""
    attributes = {
        "terminal_shortfall_pct": result.terminal_shortfall_pct,
        "computed_at": result.computed_at.isoformat(),
        "solve_status": result.solve_status,
        **diagnostics,
    }
    
    self._coordinator.async_set_sensor_attributes(
        "optimizer_summary",
        attributes
    )
```

**Integration Point:** Call after successful optimization in `run_optimizer()` method.

## Component 2: Forecast Accuracy Discount in Terminal Cost

### Objective

Apply a dynamic discount to `projected_solar_gain_pct` based on observed forecast accuracy before using it in terminal cost calculations.

### Current Terminal Cost Calculation

**Location:** `engine/core.py` lines 520-538

```python
# Current (over-credits solar)
projected_solar_gain_pct = DPPlanner._projected_solar_soc_gain_pct(
    slot_idx=0,
    slots=inputs.slots,
    terminal_penalty_idx=terminal_penalty_idx,
    battery_capacity_kwh=config.battery_capacity_kwh,
)

for bin_idx, soc in enumerate(soc_grid):
    effective_soc = soc + future_solar_gain_pct + projected_solar_gain_pct
    if effective_soc < target:
        shortfall = target - effective_soc
        penalty = shortfall * penalty_per_pct
```

**Problem:** Uses full `projected_solar_gain_pct` even when forecasts are 37% accurate.

### New Terminal Cost Calculation

```python
# NEW: Get forecast accuracy
forecast_accuracy = self._get_forecast_accuracy(inputs.solar_accuracy_tracker)
accuracy_discount = max(0.5, min(1.0, forecast_accuracy))

# Apply discount
projected_solar_gain_pct = DPPlanner._projected_solar_soc_gain_pct(...)
adjusted_solar_gain_pct = projected_solar_gain_pct * accuracy_discount

# Use adjusted value
for bin_idx, soc in enumerate(soc_grid):
    effective_soc = soc + future_solar_gain_pct + adjusted_solar_gain_pct
    if effective_soc < target:
        shortfall = target - effective_soc
        penalty = shortfall * penalty_per_pct
```

### Accuracy Discount Formula

```
accuracy_discount = clamp(forecast_accuracy, 0.5, 1.0)
```

**Examples:**
- Forecast accuracy 100% → discount = 1.0 (no reduction)
- Forecast accuracy 75% → discount = 0.75 (use 75% of solar)
- Forecast accuracy 50% → discount = 0.5 (use 50% of solar)
- Forecast accuracy 37% → discount = 0.5 (clamped minimum)
- Forecast accuracy 0% → discount = 1.0 (early return: no data, don't discount)
- Negative accuracy → discount = 1.0 (invalid data, treat as no data)

**Minimum 0.5 Rationale:**
- Prevents completely ignoring solar (which could over-charge unnecessarily)
- Provides safety margin even with very poor forecasts
- Conservative but not overly pessimistic

**Maximum 1.0 Rationale:**
- Never amplify solar beyond forecast (accuracy can't exceed 100%)
- Trust forecasts when they're proven accurate

### Helper Method

**Location:** `engine/core.py`

```python
def _get_forecast_accuracy(
    self,
    solar_accuracy_tracker: SolarAccuracyTracker | None,
) -> float:
    """Get overall forecast accuracy from tracker.
    
    Returns:
        float: Accuracy as decimal (0.0 to 1.0), or 1.0 if unavailable/invalid
    """
    if solar_accuracy_tracker is None:
        return 1.0  # No tracker, don't apply discount
    
    accuracy_pct = solar_accuracy_tracker.get_overall_accuracy()
    
    # No data, invalid data, or explicitly zero: treat as no data
    if accuracy_pct is None or accuracy_pct <= 0:
        return 1.0  # No reliable data, don't apply discount
    
    return accuracy_pct / 100.0  # Convert percentage to decimal
```

### Integration with Existing Mechanisms

**Complementary to:**
- **Solar Confidence Factor**: Still used as fallback when bias correction not ready
- **Bias Correction**: Applied at slot-building stage, this discount applied at terminal cost stage
- **Solar Opportunity Penalty**: Uses raw forecasts (unchanged)
- **Global Solar Sufficiency**: Uses raw forecasts (unchanged)

**Why not discount everywhere?**
- Bias correction already adjusts slot-level forecasts
- Terminal cost is the critical decision point for charging adequacy
- Over-discounting in constraints could prevent charging even when beneficial
- Focused fix with minimal blast radius

## Component 3: Diagnostic Sensors

### Objective

Expose internal optimizer state for debugging, validation, and user confidence.

### New Diagnostic Helper

**Location:** `engine/core.py`

```python
def _get_terminal_diagnostics(
    self,
    soc_pct: float,
    target: float,
    projected_solar_gain_pct: float,
    accuracy_discount: float,
    future_solar_gain_pct: float,
    slots: list[SlotContext],
    terminal_penalty_idx: int | None,
) -> dict[str, Any]:
    """Extract diagnostic metrics for terminal cost calculation.
    
    Args:
        soc_pct: Current state of charge percentage
        target: Target SOC percentage
        projected_solar_gain_pct: Raw solar projection
        accuracy_discount: Applied discount factor
        future_solar_gain_pct: Beyond-horizon solar gain
        slots: All time slots in plan
        terminal_penalty_idx: Index of terminal penalty slot
        
    Returns:
        Dictionary of diagnostic metrics
    """
    adjusted_solar_gain = projected_solar_gain_pct * accuracy_discount
    effective_soc = soc_pct + future_solar_gain_pct + adjusted_solar_gain
    
    # Find peak SOC from slots
    peak_soc = max(slot.predicted_soc for slot in slots) if slots else soc_pct
    
    # Find demand window entry SOC
    dw_entry_soc = None
    if terminal_penalty_idx is not None and slots:
        dw_entry_soc = slots[terminal_penalty_idx].predicted_soc
    
    return {
        "projected_solar_gain_pct": round(projected_solar_gain_pct, 2),
        "accuracy_discount_factor": round(accuracy_discount, 2),
        "adjusted_solar_gain_pct": round(adjusted_solar_gain, 2),
        "effective_soc_at_terminal": round(effective_soc, 2),
        "peak_soc_pct": round(peak_soc, 2),
        "dw_entry_soc_pct": round(dw_entry_soc, 2) if dw_entry_soc else None,
    }
```

### Sensor Attribute Updates

**Location:** `sensor.py` - `LocalShiftOptimizerSummary` class

```python
@property
def extra_state_attributes(self) -> dict[str, Any]:
    """Return optimizer summary attributes."""
    summary = self.coordinator.data.optimizer_summary
    
    return {
        # Existing
        "terminal_shortfall_pct": summary.terminal_shortfall_pct,
        "computed_at": summary.computed_at,
        "solve_status": summary.solve_status,
        
        # NEW: Diagnostic attributes
        "peak_soc_pct": summary.peak_soc_pct,
        "dw_entry_soc_pct": summary.dw_entry_soc_pct,
        "projected_solar_gain_pct": summary.projected_solar_gain_pct,
        "forecast_accuracy": summary.forecast_accuracy,
        "accuracy_discount_factor": summary.accuracy_discount_factor,
        "adjusted_solar_gain_pct": summary.adjusted_solar_gain_pct,
        "effective_soc_at_terminal": summary.effective_soc_at_terminal,
    }
```

## Error Handling and Edge Cases

### Edge Case 1: Solar Accuracy Tracker Not Ready

**Condition:** `solar_accuracy_tracker.get_overall_accuracy()` returns `None` (< 20 samples)

**Behavior:** Return `forecast_accuracy = 1.0` (no discount applied)

**Rationale:** Don't apply discount until we have sufficient data. Matches existing bias correction behavior.

### Edge Case 2: Forecast Accuracy is 0% or Negative

**Condition:** Tracker reports 0% or negative accuracy (invalid data)

**Behavior:** Return `forecast_accuracy = 1.0` (no discount)

**Rationale:** Assume forecasts are usable until proven otherwise. Conservative default.

### Edge Case 3: Terminal Penalty Index is None

**Condition:** No demand window in forecast

**Behavior:** Diagnostics show `dw_entry_soc_pct = None`, other metrics still computed

**Rationale:** Provide diagnostic data even when terminal cost isn't applied.

### Edge Case 4: Empty or Invalid Slots

**Condition:** Slots list is empty or None

**Behavior:** Use fallback values (current soc_pct for peak_soc)

**Rationale:** Graceful degradation during startup or data issues.

### Edge Case 5: Accuracy > 100%

**Condition:** Tracker bug or data error

**Behavior:** Clamp to 1.0 maximum

**Rationale:** Never amplify solar beyond forecast.

## Data Flow

### Optimizer Run Sequence

```
1. OptimizerFacade.run_optimizer()
   ↓
2. Prepares OptimizerInputs (includes solar_accuracy_tracker)
   ↓
3. DPPlanner.plan(inputs)
   ↓
4. In _initialize_dp_tables():
   a. Call _get_forecast_accuracy(inputs.solar_accuracy_tracker)
   b. Compute accuracy_discount = clamp(accuracy, 0.5, 1.0)
   c. Compute adjusted_solar_gain_pct = projected_solar_gain_pct * accuracy_discount
   d. Use adjusted value in effective_soc calculation
   e. Call _get_terminal_diagnostics() to build metrics dict
   ↓
5. Return OptimizerResult with diagnostics
   ↓
6. OptimizerFacade receives result
   ↓
7. Call _update_optimizer_summary_sensor(result, diagnostics)
   ↓
8. CoordinatorData.optimizer_summary updated
   ↓
9. LocalShiftOptimizerSummary sensor state/attributes updated
   ↓
10. Home Assistant UI displays updated metrics
```

## Logging Strategy

### Debug Logging

**Key Log Points:**

```python
_LOGGER.debug(
    "Terminal cost discount: accuracy=%.1f%%, discount=%.2f, "
    "raw_solar_gain=%.1f%%, adjusted=%.1f%%",
    forecast_accuracy * 100,
    accuracy_discount,
    projected_solar_gain_pct,
    adjusted_solar_gain_pct,
)
```

**When to log:**
- When accuracy discount is calculated and applied
- When sensor attributes are updated
- When diagnostic metrics are computed

**Log Level:** DEBUG (not INFO) to avoid spam, visible when troubleshooting.

## Testing Strategy

### Unit Tests

**File:** `tests/engine/test_terminal_cost_accuracy.py` (new)

**Test Cases:**

1. **test_forecast_accuracy_retrieval**
   - accuracy = 100% → returns 1.0
   - accuracy = 50% → returns 0.5
   - accuracy = 37% → returns 0.37
   - accuracy = 0% → returns 1.0 (no data)
   - accuracy = None → returns 1.0 (tracker unavailable)

2. **test_accuracy_discount_clamping**
   - accuracy = 1.5 → discount = 1.0 (clamped to max)
   - accuracy = 0.3 → discount = 0.5 (clamped to min)
   - accuracy = 0.75 → discount = 0.75 (normal range)

3. **test_terminal_cost_with_discount**
   - Verify effective_soc uses adjusted_solar_gain_pct
   - Verify terminal penalty increases when accuracy is low
   - Verify behavior unchanged when accuracy = 1.0

4. **test_diagnostic_metrics_extraction**
   - Verify all diagnostic fields populated correctly
   - Verify None handling for missing demand window
   - Verify peak_soc and dw_entry_soc calculations

5. **test_sensor_update_integration**
   - Mock OptimizerResult with diagnostics
   - Verify sensor attributes are updated
   - Verify all fields present in sensor state

### Integration Tests

**File:** `tests/test_optimizer_facade.py` (extend existing)

**Test Cases:**

1. **test_optimizer_run_with_accuracy_discount**
   - Set up solar accuracy tracker with MAPE = 60%
   - Run optimizer
   - Verify sensor updated with diagnostic metrics
   - Verify terminal shortfall reflects accuracy discount

2. **test_degrading_forecast_accuracy**
   - Start with high accuracy (90%)
   - Simulate accuracy drop to 40%
   - Verify optimizer plans more aggressive charging
   - Verify sensor reflects accuracy change

3. **test_sensor_update_frequency**
   - Run optimizer multiple times
   - Verify sensor updates after each run
   - Verify timestamps are current

### Manual Testing Checklist

After deploying to live system:

- [ ] Verify `sensor.localshift_optimizer_summary` updates every 1-5 minutes
- [ ] Verify new diagnostic attributes appear in Home Assistant UI
- [ ] Compare `projected_solar_gain_pct` vs `adjusted_solar_gain_pct` when accuracy is low
- [ ] Verify optimizer plans more charging when forecast accuracy drops
- [ ] Monitor logs for accuracy discount debug messages
- [ ] Verify no errors during startup or when tracker has <20 samples
- [ ] Test behavior when demand window exists vs doesn't exist
- [ ] Validate that historical incident (yesterday's 11:23 manual intervention) would be prevented

## Files Modified

### `custom_components/localshift/engine/core.py`

**Changes:**
1. Add `_get_forecast_accuracy()` helper method
2. Modify terminal cost calculation to apply accuracy discount
3. Add `_get_terminal_diagnostics()` helper method
4. Pass diagnostics with OptimizerResult

**Lines affected:** ~520-544 (terminal cost initialization)

### `custom_components/localshift/engine/optimizer_facade.py`

**Changes:**
1. Add `_update_optimizer_summary_sensor()` method
2. Call sensor update after successful optimization
3. Pass solar_accuracy_tracker in OptimizerInputs (verify already exists)

**Lines affected:** ~250-280 (after optimizer run)

### `custom_components/localshift/sensor.py`

**Changes:**
1. Add diagnostic attributes to `LocalShiftOptimizerSummary.extra_state_attributes`
2. Handle None values gracefully

**Lines affected:** Optimizer summary sensor definition

### `custom_components/localshift/coordinator/coordinator.py`

**No changes required.** The `optimizer_summary` is already stored in `data.optimizer_summary` (dict type) which is automatically picked up by the sensor on the next coordinator update cycle. The implementation will add diagnostic fields directly to this dict in `optimizer_facade.py`.

## Backward Compatibility

### Configuration

**No breaking changes:**
- No config schema changes
- No new required configuration options
- All changes are internal implementation details

### Behavior

**When forecast accuracy is high (>90%):**
- Behavior unchanged from current implementation
- Full solar credit applied (discount = 1.0)

**When forecast accuracy is low (<50%):**
- More conservative charging (higher terminal penalty)
- May charge earlier/more than current implementation
- **This is the intended fix**

### API

**Sensor attributes:**
- Existing attributes unchanged
- New attributes are additive only
- Scripts/automations reading old attributes continue to work

## Deployment Strategy

### Phase 1: Implementation
1. Implement Component 2 (accuracy discount in terminal cost)
2. Implement Component 3 (diagnostic helper)
3. Add unit tests
4. Add integration tests

### Phase 2: Sensor Updates
1. Implement Component 1 (sensor update mechanism)
2. Test sensor update frequency
3. Validate diagnostic attributes appear in UI

### Phase 3: Testing
1. Run unit tests (target: 95% coverage)
2. Run integration tests
3. Deploy to test branch
4. Monitor logs and sensor states
5. Validate charging behavior

### Phase 4: Production
1. Merge to main
2. Deploy to production
3. Monitor for 24-48 hours
4. Validate prevents historical incident
5. Collect user feedback

## Success Criteria

### Functional Requirements

1. ✅ Optimizer summary sensor updates within 5 minutes of each plan computation
2. ✅ Forecast accuracy discount applied to terminal cost calculation
3. ✅ All diagnostic attributes visible in Home Assistant UI
4. ✅ No errors or crashes during normal operation
5. ✅ Graceful handling of edge cases (no tracker data, no demand window)

### Performance Requirements

1. ✅ Sensor update latency < 1 second
2. ✅ No measurable impact on optimizer computation time
3. ✅ No memory leaks from diagnostic data retention

### Validation Requirements

1. ✅ Yesterday's incident (insufficient charging at 11:23) would be prevented
2. ✅ Optimizer plans more charging when forecast accuracy < 50%
3. ✅ Behavior unchanged when forecast accuracy > 90%
4. ✅ Test coverage ≥ 95% for new code

## Future Enhancements

### Not Included in This Design

1. **Time-of-day solar weighting** - Early morning forecasts less reliable than afternoon
2. **Horizon-based degradation** - Discount forecasts by distance from now
3. **Weather-conditional margins** - Larger buffer on "cloudy" days
4. **Alerting** - Notify user when terminal shortfall exceeds threshold

### Rationale for Deferral

Focus on the minimum viable fix that addresses the root cause. These enhancements can be added incrementally if needed based on observed behavior.

## References

### Code Locations

- Terminal cost calculation: `engine/core.py:520-544`
- Solar accuracy tracker: `forecast/solar_accuracy.py`
- Optimizer facade: `engine/optimizer_facade.py`
- Sensor definitions: `sensor.py`
- Existing solar adjustments: `engine/slots.py:224-398`

### Related Issues

- Solar forecast accuracy tracking (existing)
- Bias correction implementation (existing)
- Hard constraint penalty Issue #624 (existing)

### Documentation

- `docs/PLANNING_MODEL.md` - Terminal cost explanation
- `docs/ENTITY_REFERENCE.md` - Sensor documentation (update after implementation)
- `docs/ARCHITECTURE.md` - System architecture (update if needed)