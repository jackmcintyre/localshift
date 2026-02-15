# Hold Mode Removal - Requirements Document

## Objective

Completely remove the Hold mode functionality (SOLAR_EXPORT_HOLD, HOLD, HOLDING_FOR_SPIKE) from the amber_powerwall Home Assistant component.

## Background / Rationale

### Why Remove Hold Mode?

1. **Overuse Problem**: Hold mode was being triggered too frequently, preventing the battery from being used optimally for cost savings.

2. **Anti-Flapping is Already Handled**: The state machine's debounce system (5 minutes for price-driven transitions) already prevents rapid mode switching ("flapping").

3. **Forecast-Driven Control**: The forecast_computer.py provides superior optimization by looking ahead at price curves and solar production forecasts. Hold mode's static cannot compete thresholds with this.

4. **Redundant Functionality**: The preservation optimization in forecast_computer already handles the use case that Hold mode was meant to address.

### What Was Hold Mode Supposed to Do?

- **Original intent**: Prevent battery from discharging during high price periods or when solar export was favorable
- **Implementation**: Static thresholds (price diff, solar export) to decide when to "hold" (not discharge)

### What Replaces It?

- **Debounce system**: 5-minute debounce prevents flapping
- **Forecast-driven optimization**: Looks ahead at prices and solar to make optimal decisions
- **Preservation optimization**: Already handles the "save for later" scenarios

---

## Requirements

### 1. const.py Changes

**Remove from BatteryMode enum:**
- `SOLAR_EXPORT_HOLD`
- `HOLD`
- `HOLDING_FOR_SPIKE`

**Remove configuration options:**
- `CONF_SOLAR_EXPORT_HOLD_THRESHOLD`
- `CONF_HOLD_MAX_PRICE_DIFF`

**Remove defaults:**
- `DEFAULT_SOLAR_EXPORT_HOLD_THRESHOLD`
- `DEFAULT_HOLD_MAX_PRICE_DIFF`

### 2. computation_engine.py Changes

**Remove methods:**
- `_compute_hold_justified()` - Remove entirely

**Remove logic:**
- `hold_justified` computation from `compute_derived_values()`
- `forecast_spike_within_window` logic
- Hold mode from `active_mode` decision tree (should only have: SELF_CONSUMPTION, DEMAND_BLOCK, GRID_CHARGING, BOOST_CHARGING, SPIKE_DISCHARGE, MANUAL)

### 3. state_machine.py Changes

**Remove from `infer_current_hardware_mode()`:**
```python
# Remove this block:
if data.hold_mode:
    if data.solar_export_hold:
        return BatteryMode.SOLAR_EXPORT_HOLD
    return BatteryMode.HOLD
```

**Remove from `get_debounce_for_transition()`:**
```python
# Remove these lines:
# Solar export hold: 2 minutes
if (
    to_mode == BatteryMode.SOLAR_EXPORT_HOLD
    or from_mode == BatteryMode.SOLAR_EXPORT_HOLD
):
    return timedelta(minutes=2)
```
Also update docstring to remove "- Solar export hold → 2 minutes (A17/A18)"

**Remove from `evaluate_state_machine()`:**
```python
# Remove this block after "self._commanded_mode = desired":
# Clear hold_mode flag when transitioning away from hold modes
# This prevents flag from persisting and causing unintended
# hold mode re-entry
if desired not in (
    BatteryMode.HOLD,
    BatteryMode.SOLAR_EXPORT_HOLD,
    BatteryMode.HOLDING_FOR_SPIKE,
):
    data.hold_mode = False
```

**Remove from `_execute_mode_transition()`:**
```python
# Remove these elif blocks:
elif target == BatteryMode.HOLD:
    data.solar_export_hold = False
    transition_success = await self._battery_controller.set_hold(
        data, dry_run
    )
    ...

elif target == BatteryMode.SOLAR_EXPORT_HOLD:
    data.solar_export_hold = True
    ...

elif target == BatteryMode.HOLDING_FOR_SPIKE:
    data.solar_export_hold = False
    ...
```

**Remove from `_get_expected_state_for_mode()`:**
```python
# Remove these branches:
elif mode in (BatteryMode.HOLD, BatteryMode.HOLDING_FOR_SPIKE):
    return ("self_consumption", -1, TESLEMETRY_EXPORT_PV_ONLY)
elif mode == BatteryMode.SOLAR_EXPORT_HOLD:
    return ("self_consumption", -1, TESLEMETRY_EXPORT_PV_ONLY)
```

### 4. coordinator_data.py Changes (if needed)

- Remove `hold_mode` attribute if it exists
- Remove `solar_export_hold` attribute if it exists

### 5. config_flow.py Changes

**Remove from options flow:**
- Solar export hold threshold option
- Hold max price diff option

### 6. number.py Changes

**Remove number entities:**
- Solar export hold threshold entity
- Hold max price diff entity

### 7. battery_controller.py Changes

**Remove methods (if any):**
- `set_hold()` method - Remove if only used for hold modes

### 8. Documentation Updates

**Update TEST_SCENARIOS.md:**
- Remove all test scenarios involving Hold mode
- Update remaining scenarios to reflect 5-mode system (not 8-mode)

**Update docs/ARCHITECTURE.md:**
- Remove Hold mode from architecture diagrams
- Update mode list to show 6 modes: SELF_CONSUMPTION, DEMAND_BLOCK, GRID_CHARGING, BOOST_CHARGING, SPIKE_DISCHARGE, MANUAL

---

## Files to Modify

| File | Changes Required |
|------|-----------------|
| `const.py` | Remove enum values, config options, defaults |
| `computation_engine.py` | Remove hold_justified logic, active_mode branches |
| `state_machine.py` | Remove hold references in 5 methods |
| `coordinator_data.py` | Check/remove hold_mode, solar_export_hold |
| `config_flow.py` | Remove hold config options |
| `number.py` | Remove hold number entities |
| `battery_controller.py` | Check/remove set_hold method |
| `TEST_SCENARIOS.md` | Remove Hold test scenarios |
| `docs/ARCHITECTURE.md` | Update architecture docs |

---

## Testing Considerations

After removal, verify:

1. **No Hold mode in UI**: Configuration options should not appear
2. **State machine works**: All 6 remaining modes transition correctly
3. **Debounce works**: 5-minute debounce prevents flapping
4. **Forecast optimization works**: Cost savings still optimized
5. **No crashes**: Component loads without errors

---

## Rollback Plan

If issues arise:

1. Revert the branch
2. The Hold mode will be restored from the committed code

---

## Notes

- This change reduces complexity by removing ~3 modes that were rarely used optimally
- The debounce system + forecast optimization provides better results than static Hold thresholds ever could
- User configuration becomes simpler with fewer options
</parameter>
<task_progress>
- [x] Discard current changes and reset to clean state
- [x] Create new branch for requirements document
- [x] Write HOLD_MODE_REMOVAL_REQUIREMENTS.md with detailed requirements
</task_progress>
</invoke>
</minimax:tool_call>