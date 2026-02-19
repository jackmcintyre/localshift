# Backlog Item Template

**ID:** backlog-med-026  
**Priority:** MED  
**Status:** PROPOSED  
**Created:** 2026-02-20  
**Updated:** 2026-02-20  

---

## Summary

Comprehensive test suite improvements: fix existing issues, add missing tests, and create new test file for battery_controller.

---

## Description

### Current Test Analysis

**test_computation_engine.py** - Mostly valid but missing:
- ForecastChangeTracker logic tests
- _compute_daily_15min_forecast tests
- _analyze_spike tests for conservative mode

**test_coordinator.py** - Too minimal:
- Only 3 basic tests
- Missing: async_start, state change handlers, periodic tick, midnight reset, state machine evaluation

**test_forecast_computer.py** - Good but missing:
- compute_forecast() - main entry point not directly tested
- _should_grid_charge_at_slot() - complex grid charging logic not tested
- _simulate_future_soc_with_solar_only() - not tested
- _find_battery_fill_point() - not tested

**test_hybrid_timescale.py** - Excellent coverage

**test_state_machine.py** - Best coverage, minor gaps:
- Missing test for _skip_next_debounce flag behavior
- Missing test for set_manual_override_timestamp()

**test_integration.py** - Placeholder only:
- Contains only `assert True` tests
- No actual integration testing

### Test Quality Issues

1. `test_integration.py` - Placeholder tests need real implementation
2. Missing file: `test_battery_controller.py`
3. `test_computation_engine.py::test_active_mode_forecast_driven` - misleading name (tests "conditions not met" not activation)
4. `test_coordinator.py::test_coordinator_get_entity_id` - tests fixture not coordinator code

---

## Affected Files

- `tests/test_computation_engine.py` - Add ForecastChangeTracker and spike analysis tests
- `tests/test_coordinator.py` - Major additions for lifecycle and event handling
- `tests/test_forecast_computer.py` - Add compute_forecast and grid charging tests
- `tests/test_integration.py` - Complete rewrite with real tests
- `tests/test_battery_controller.py` - NEW FILE NEEDED

---

## Proposed Solution

### High Priority (Core Functionality)

1. **Create `tests/test_battery_controller.py`** - NEW FILE
   - set_self_consumption() success/failure paths
   - set_force_charge() success/failure paths
   - set_boost_charge() success/failure paths
   - set_force_discharge() with/without reserve_soc
   - set_proactive_export() dynamic reserve calculation
   - validate_transition() timeout and retry logic
   - verify_current_state() state mismatch detection

2. **Add to `test_computation_engine.py`**:
   - test_forecast_change_tracker_should_recompute
   - test_forecast_change_tracker_first_run
   - test_compute_daily_15min_forecast
   - test_analyze_spike_conservative_mode

3. **Add to `test_forecast_computer.py`**:
   - test_compute_forecast_full_cycle
   - test_should_grid_charge_at_slot_cheap_price
   - test_should_grid_charge_at_slot_overnight
   - test_simulate_future_soc_with_solar_only
   - test_find_battery_fill_point

### Medium Priority (Integration & Edge Cases)

4. **Expand `tests/test_coordinator.py`**:
   - test_async_start_initialization
   - test_handle_state_change_during_transition
   - test_handle_periodic_tick_load_refresh
   - test_midnight_reset
   - test_daily_summary

5. **Rewrite `tests/test_integration.py`**:
   - Full state machine flow with mode changes
   - Forecast → active_mode → transition pipeline
   - Error handling and recovery scenarios
   - Dry run mode verification

### Low Priority (Coverage Gaps)

6. **Edge Case Tests**:
   - Empty forecast data handling
   - Missing entity state handling
   - Timezone boundary handling
   - Day rollover scenarios

---

## Notes

This is a test improvement task - no production code changes required. Tests should validate existing functionality and ensure future changes don't break behavior.

---

## Related Items

- backlog-crit-002 (state machine tests already completed)
- backlog-high-021 (PROACTIVE_EXPORT debounce - tested in state machine)
