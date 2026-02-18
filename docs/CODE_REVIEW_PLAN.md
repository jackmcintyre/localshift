# LocalShift Code Review Plan

**Created:** 2026-02-19
**Purpose:** Systematic review of the LocalShift integration to identify bugs and improvement opportunities.

---

## Overview

This document outlines a structured approach to reviewing the LocalShift Home Assistant integration for Tesla Powerwall battery automation. The review is organized into 7 phases, prioritized by impact on core functionality.

## Review Phases

### Phase 1: Core Logic & State Machine ⚠️ HIGH PRIORITY
**Files:** `state_machine.py`, `computation_engine.py`, `forecast_computer.py`
**Estimated Time:** 30-45 minutes
**Status:** 🔲 Not Started

#### 1.1 State Machine (`state_machine.py`)

**Purpose:** Manages battery mode state transitions with debounce logic.

**Review Areas:**

##### 1.1.1 Debounce Logic
- [ ] Verify debounce timers are correctly applied per transition type
- [ ] Check timer clearing logic when mode flip-flops (lines 135-140)
- [ ] Ensure 5-minute debounce for price-driven modes matches YAML behavior
- [ ] Validate immediate transitions for high-priority modes (SPIKE_DISCHARGE, PROACTIVE_EXPORT, DEMAND_BLOCK)

```python
# Current debounce implementation (lines 65-84)
def get_debounce_for_transition(self, from_mode, to_mode):
    if to_mode in (SPIKE_DISCHARGE, PROACTIVE_EXPORT, DEMAND_BLOCK, MANUAL):
        return timedelta(0)
    if from_mode in (SPIKE_DISCHARGE, PROACTIVE_EXPORT, DEMAND_BLOCK):
        return timedelta(0)
    return timedelta(minutes=5)
```

**Potential Issue:** PROACTIVE_EXPORT has 0 debounce - is this appropriate for a forecast-driven mode?

##### 1.1.2 Transition Execution
- [ ] Review `_execute_mode_transition` return value handling
- [ ] Check if failed transitions are properly retried
- [ ] Verify `_in_mode_transition` flag prevents re-entrant calls

**Potential Issue:** When a transition fails, the debounce timer is cleared but there's no automatic retry mechanism beyond the next evaluation cycle.

##### 1.1.3 Health Check Logic
- [ ] Verify 5-minute cooldown prevents command spam
- [ ] Check `_get_expected_state_for_mode` covers all modes
- [ ] Validate health check runs every minute regardless of mode

**Potential Issue:** Health check only verifies operation_mode and backup_reserve, not export_mode.

##### 1.1.4 Startup Grace Period
- [ ] Verify 30-second grace period allows entities to populate
- [ ] Check that grace period is cleared after startup
- [ ] Validate inferred mode logic matches hardware state

#### 1.2 Computation Engine (`computation_engine.py`)

**Purpose:** Computes all derived sensor values from raw state.

**Review Areas:**

##### 1.2.1 Method Complexity
- [ ] Review `compute_derived_values` method (400+ lines) for decomposition opportunities
- [ ] Check if step ordering is correct (dependencies between steps)
- [ ] Verify all derived values are computed exactly once per cycle

**Potential Issue:** Large method with multiple responsibilities - hard to test and maintain.

##### 1.2.2 Forecast Integration
- [ ] Verify forecast change detection works correctly
- [ ] Check that forecast is computed before dependent values (solar_can_reach_target)
- [ ] Validate forecast caching doesn't cause stale data issues

**Potential Issue:** Forecast change tracker uses private attribute access to force recompute.

##### 1.2.3 Active Mode Computation
- [ ] Review `_compute_active_mode` logic flow
- [ ] Verify forecast-driven control takes precedence over reactive modes
- [ ] Check fallback logic when forecast is unavailable

**Potential Issue:** Complex priority chain - ensure all conditions are mutually exclusive.

##### 1.2.4 Decision Logging
- [ ] Verify decision log captures mode changes and periodic updates
- [ ] Check log rotation (50 entries max)
- [ ] Validate timestamp formatting

#### 1.3 Forecast Computer (`forecast_computer.py`)

**Purpose:** Computes 24-hour battery SOC forecast with grid charging decisions.

**Review Areas:**

##### 1.3.1 Hybrid Timescale Logic
- [ ] Verify 5-min near-term + 15-min long-term slot handling
- [ ] Check slot boundary calculations
- [ ] Validate elapsed time calculations for comparisons

**Potential Issue:** Multiple SOC simulation methods with similar logic - opportunity for consolidation.

##### 1.3.2 Grid Charging Logic
- [ ] Review `_should_grid_charge_at_slot` decision criteria
- [ ] Verify spot price preference over forecast
- [ ] Check overnight charging simulation accuracy

**Potential Issue:** Complex overnight simulation logic - verify edge cases.

##### 1.3.3 Proactive Export Logic
- [ ] Review `_should_proactive_export_at_slot` decision criteria
- [ ] Verify fill-point based export strategy
- [ ] Check overnight drain simulation accuracy
- [ ] Validate throttling reserve calculation

**Potential Issue:** Very complex logic with multiple safety checks - ensure all conditions work together.

##### 1.3.4 Forecast Data Structure
- [ ] Verify 112-slot forecast structure (24×5min + 88×15min)
- [ ] Check forecast entry fields are consistent
- [ ] Validate forecast export budget calculation

### Phase 2: Battery Control & Validation ⚠️ HIGH PRIORITY
**Files:** `battery_controller.py`, `state_reader.py`
**Estimated Time:** 20 minutes
**Status:** 🔲 Not Started

#### 2.1 Battery Controller (`battery_controller.py`)

**Review Areas:**
- [ ] Command validation and timeout logic
- [ ] Error handling for failed commands
- [ ] Synchronous entity calls in async context

#### 2.2 State Reader (`state_reader.py`)

**Review Areas:**
- [ ] Entity state reading robustness
- [ ] Fallback logic for missing entities
- [ ] Data type validation

### Phase 3: Data Handling & Edge Cases
**Files:** `coordinator_data.py`, `history_fetcher.py`, `solar_utils.py`
**Estimated Time:** 20 minutes
**Status:** 🔲 Not Started

#### 3.1 Coordinator Data (`coordinator_data.py`)

**Review Areas:**
- [ ] Data class field completeness
- [ ] Type annotations accuracy
- [ ] Default value appropriateness

#### 3.2 History Fetcher (`history_fetcher.py`)

**Review Areas:**
- [ ] Database query error handling
- [ ] Cache invalidation logic
- [ ] Thread pool usage safety

#### 3.3 Solar Utils (`solar_utils.py`)

**Review Areas:**
- [ ] Price lookup edge cases
- [ ] Solar forecast overlap calculations
- [ ] Timezone handling consistency

### Phase 4: Entity Platforms
**Files:** `sensor.py`, `binary_sensor.py`, `switch.py`, `button.py`, `number.py`
**Estimated Time:** 15 minutes
**Status:** 🔲 Not Started

#### 4.1 Sensor Platform (`sensor.py`)

**Review Areas:**
- [ ] Large attribute dictionaries performance
- [ ] Update frequency optimization
- [ ] State class correctness

#### 4.2 Other Platforms

**Review Areas:**
- [ ] Button press throttling
- [ ] Switch state persistence
- [ ] Number entity validation

### Phase 5: Configuration & Initialization
**Files:** `config_flow.py`, `__init__.py`, `const.py`
**Estimated Time:** 15 minutes
**Status:** 🔲 Not Started

#### 5.1 Config Flow (`config_flow.py`)

**Review Areas:**
- [ ] Multi-step flow validation
- [ ] Entity existence checks
- [ ] Error message clarity

#### 5.2 Constants (`const.py`)

**Review Areas:**
- [ ] Configuration option ranges
- [ ] Default value appropriateness
- [ ] Enum completeness

### Phase 6: Test Coverage
**Files:** `tests/*.py`
**Estimated Time:** 20 minutes
**Status:** 🔲 Not Started

#### 6.1 Test Structure

**Review Areas:**
- [ ] Unit test coverage gaps
- [ ] Mock fixture completeness
- [ ] Edge case testing

### Phase 7: Documentation & Maintenance
**Files:** `docs/*.md`, inline documentation
**Estimated Time:** 10 minutes
**Status:** 🔲 Not Started

#### 7.1 Documentation

**Review Areas:**
- [ ] Architecture diagram accuracy
- [ ] Code comment currency
- [ ] API documentation completeness

---

## Success Criteria

- [ ] All critical bugs identified and documented
- [ ] Performance bottlenecks identified
- [ ] Code maintainability issues documented
- [ ] Test coverage gaps identified
- [ ] Documentation accuracy verified

## Risk Assessment

**High Risk Areas:**
- State machine transition logic (battery damage potential)
- Forecast computation accuracy (financial impact)
- Battery controller command validation (hardware reliability)

**Medium Risk Areas:**
- Entity platform performance
- Configuration validation
- Error handling completeness

**Low Risk Areas:**
- Documentation accuracy
- Code style consistency
- Test coverage completeness

---

## Next Steps

1. Begin with Phase 1 (Core Logic & State Machine) - highest impact area
2. Document findings in issue tracker or backlog
3. Prioritize fixes by risk level and user impact
4. Update this plan as new issues are discovered