# Backlog Item Template

**ID:** backlog-high-019  
**Priority:** HIGH  
**Status:** COMPLETED  
**Created:** 2026-02-17  
**Updated:** 2026-02-17  

---

## Summary

Allow entry into Demand Window under target when solar forecast shows we can reach target within the window.

---

## Description

Currently, the automation requires the battery to be at or above target before entering the Demand Window (DW). This can lead to unnecessary grid charging when solar forecast shows sufficient generation within the DW to reach target.

**Example Scenario:**
- Time: 12pm, Battery: 80%, Target: 100%
- Forecast: Cloudy 12-3pm, Full sun 3-6pm (DW ends at 8pm)

**Current Behavior:**
- Grid charges at cheap prices 12-3pm to ensure 100% at DW entry
- Reaches 100% at 3pm
- Enters DW, no more charging
- Result: Wastes money on grid charging when solar could do it

**Desired Behavior:**
- Skips grid charging (solar forecast shows 6 hours of sun in DW)
- Enters DW at 3pm at 80%
- Solar charges 80% → 100% by ~4:30pm
- Enters DW earlier, saves money, still meets target

---

## Affected Files

- `custom_components/amber_powerwall/coordinator_data.py` - Add `solar_can_reach_target_in_dw` flag
- `custom_components/amber_powerwall/computation_engine_lib/forecast_computer.py` - Modify `_simulate_future_soc_with_solar_only` to simulate during DW
- `custom_components/amber_powerwall/computation_engine.py` - Add computation for new flag
- `custom_components/amber_powerwall/state_machine.py` - Modify `_compute_active_mode` to use new flag

---

## Steps to Reproduce

1. Set battery target to 100%
2. Have battery at 80% at midday
3. Forecast shows cloudy period 12-3pm, then full sun 3-6pm
4. Current automation will grid charge to reach 100% by 3pm
5. This wastes money when solar could charge during DW

---

## Proposed Solution

### 1. New Forecast Logic
Modify `_simulate_future_soc_with_solar_only` in `forecast_computer.py` to:
- Accept an additional `simulate_during_dw` parameter
- When True, simulate solar charging **during** the DW period, not just before it
- Return `(soc_at_end, max_soc, can_reach_target_in_dw)`

### 2. Add New Flag to CoordinatorData
Add `solar_can_reach_target_in_dw: bool = False` to `CoordinatorData` class

### 3. Compute New Flag in ComputationEngine
In `computation_engine.py`, add computation logic:
- Simulate solar-only charging through the entire DW period
- Calculate if solar alone can reach target with a safety buffer (e.g., 110% of needed energy)
- Set `data.solar_can_reach_target_in_dw` accordingly

### 4. Modify Demand Window Mode Evaluation
In `state_machine.py`, update `_compute_active_mode`:
- When evaluating `DEMAND_BLOCK` mode:
  - If `soc >= target` → Enter DW (existing behavior)
  - **ELSE IF** `solar_can_reach_target_in_dw` is True → Enter DW (new behavior)
  - ELSE → Continue grid charging to ensure target (existing behavior)

### 5. Safety Considerations
- Add confidence buffer: require 110% of energy needed to reach target
- Monitor actual vs forecast during DW
- If solar underperforms, the system can still boost charge during DW if needed (though this violates zero-import principle)

```python
# Pseudocode for mode evaluation
if data.demand_window_active:
    # Can enter DW if already at target OR solar can reach target during DW
    if data.soc >= target_pct or data.solar_can_reach_target_in_dw:
        data.active_mode = BatteryMode.DEMAND_BLOCK
    else:
        # Continue grid charging to meet target before DW
        if data.general_price <= data.effective_cheap_price:
            data.active_mode = BatteryMode.GRID_CHARGING
        else:
            data.active_mode = BatteryMode.SELF_CONSUMPTION
```

---

## Notes

### Priority Assessment
**HIGH** priority because:
- Direct cost savings for users by avoiding unnecessary grid charging
- Leverages existing forecast infrastructure
- Logical extension of current solar forecasting capabilities
- No risk to target achievement (solar forecast must be confident)

### Open Questions
1. **Safety buffer**: How much buffer should we require? Suggest 110% of needed energy
2. **Fallback behavior**: What if solar forecast fails during DW (clouds persist)? The system could boost charge during DW, but this violates the zero-import principle
3. **Configurability**: Should this be a configurable option? Could be a toggle like `allow_dw_entry_under_target`
4. **Confidence threshold**: What minimum solar forecast confidence is needed?

### Related Forecast Work
- Builds on existing `_simulate_future_soc_with_solar_only` function
- Complements `solar_can_reach_target` flag (which checks before DW)
- Should consider the 15-minute forecast granularity

---

## Related Items

- backlog-med-008 - `can_reach_target` Inconsistency Between Legacy and Detailed Forecast (related to solar forecasting)
- backlog-high-013 - hours_to_dw Calculation Bug (Boost Charging) (related to timing calculations)