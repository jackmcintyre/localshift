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
- [ ] Check log rotation (50 entries max) - **backlog-med-003**
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

##### 3.1.1 Dataclass Design (70+ fields)
- [ ] Review single responsibility principle violation
- [ ] Check if fields can be grouped into logical classes
- [ ] Validate type annotations completeness

**Potential Issue:** Massive dataclass makes testing difficult - any change affects all tests.

##### 3.1.2 Field Usage
- [ ] Verify `ChargingDecision` class is actually used
- [ ] Check if `forecast_charging_decisions` field is populated
- [ ] Review debug fields necessity vs performance impact

**Potential Issue:** Unused or unclear fields add maintenance burden.

##### 3.1.3 Default Values
- [ ] Review appropriateness of default values (0.0, False, empty lists)
- [ ] Check if None vs empty collections are used consistently
- [ ] Validate field initialization order

#### 3.2 History Fetcher (`history_fetcher.py`)

**Review Areas:**

##### 3.2.1 Database API Resilience (lines 70-100)
- [ ] Review `getattr` usage for recorder function access
- [ ] Check error handling for API changes
- [ ] Validate thread pool executor usage

**Potential Issue:** Recorder API changes could break historical data fetching silently.

##### 3.2.2 Cache Management
- [ ] Review cache invalidation at midnight logic
- [ ] Check for race conditions in cache updates
- [ ] Validate cache key generation (date-based)

**Potential Issue:** Multiple HA restarts in one day could cause cache issues.

##### 3.2.3 Statistic ID Resolution
- [ ] Check complex logic for finding correct statistic_id
- [ ] Verify fallback when entity_id doesn't match statistic_id
- [ ] Review error handling for missing statistics

#### 3.3 Solar Utils (`solar_utils.py`)

**Review Areas:**

##### 3.3.1 Price Lookup Edge Cases (lines 50-80)
- [ ] Review `get_price_for_slot` returning 0.0 for missing data
- [ ] Check if this hides forecast unavailability
- [ ] Validate fallback logic for overlapping periods

**Potential Issue:** Silent 0.0 returns could cause battery to charge at "free" price when forecast is missing.

##### 3.3.2 Solar Calculation Accuracy
- [ ] Review overlap fraction calculations for straddling periods
- [ ] Check boundary conditions (exact overlaps)
- [ ] Validate timezone handling consistency

**Potential Issue:** Complex overlap logic may have edge cases not covered by tests.

##### 3.3.3 Data Type Handling
- [ ] Check attribute name fallbacks for different Solcast versions
- [ ] Verify numeric conversion error handling
- [ ] Review None vs 0.0 return value consistency

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
- [ ] **backlog-med-012**: Remove "binary" from binary sensor names

### Phase 5: Configuration & Initialization
**Files:** `config_flow.py`, `__init__.py`, `const.py`
**Estimated Time:** 15 minutes
**Status:** 🔲 Not Started

#### 5.1 Config Flow (`config_flow.py`)

**Review Areas:**

##### 5.1.1 Entity Validation (lines 30-60)
- [ ] Review entity existence and domain validation
- [ ] Check "unavailable"/"unknown" state handling
- [ ] Verify validation timing vs entity availability

**Potential Issue:** Entity could become unavailable after validation passes.

##### 5.1.2 Notify Service Validation (lines 80-110)
- [ ] Review notify service existence checks
- [ ] Check default service selection logic
- [ ] Verify behavior when no notify services exist

**Potential Issue:** What if no notify services exist? Flow blocks?

##### 5.1.3 Multi-step Flow State (lines 150-300)
- [ ] Review `self._teslemetry_data` and `self._pricing_data` usage - **backlog-med-005**
- [ ] Check for stale data when user navigates back
- [ ] Verify data consistency across flow steps

**Potential Issue:** If user navigates back, stored data may contain stale values.

##### 5.1.4 Error Message Clarity
- [ ] Review error message specificity
- [ ] Check user-friendly error descriptions
- [ ] Verify error recovery guidance

#### 5.2 Constants (`const.py`)

**Review Areas:**

##### 5.2.1 Threshold Ranges
- [ ] Review `CONF_CHEAP_PRICE_PERCENTILE`: 5-50%
- [ ] Check `CONF_BATTERY_TARGET`: 50-100%
- [ ] Verify `CONF_MINIMUM_TARGET_SOC`: 5-30%
- [ ] Validate range appropriateness

**Observation:** Ranges look reasonable, defaults appropriate.

##### 5.2.2 Enum Completeness
- [ ] Review `BatteryMode` enum (7 modes)
- [ ] Verify all modes used in state machine
- [ ] Check for unused enum values

**Observation:** Complete coverage, all modes used.

##### 5.2.3 Default Values
- [ ] Review default value appropriateness
- [ ] Check consistency with documentation
- [ ] Verify safe defaults for production use

### Phase 6: Test Coverage
**Files:** `tests/*.py`
**Estimated Time:** 20 minutes
**Status:** 🔲 Not Started

#### 6.1 Test Gaps Identified

**Review Areas:**

##### 6.1.1 Missing Test Files
- [ ] No `test_state_machine.py`
- [ ] No `test_battery_controller.py`
- [ ] No `test_state_reader.py`
- [ ] No `test_forecast_computer.py` (only basic tests exist)

**Critical Gap:** Core state machine logic has no unit tests.

##### 6.1.2 Test Coverage Gaps in test_computation_engine.py
- [ ] `test_active_mode_forecast_driven` sets `grid_import_kwh=0` to prevent mode activation
- [ ] No tests for grid charging activation conditions
- [ ] No tests for proactive export activation conditions
- [ ] No tests for spike discharge with actual spike conditions

**Potential Issue:** Forecast-driven modes may have untested activation paths.

##### 6.1.3 Mock Fixture Issues (conftest.py)
- [ ] Mocks don't simulate real HA entity states
- [ ] `coordinator_data` fixture uses minimal data
- [ ] No fixtures for forecast data
- [ ] No fixtures for complex state scenarios

**Potential Issue:** Tests may pass with mocks but fail in real HA environment.

##### 6.1.4 Integration Test Gaps
- [ ] No tests for entity platform interactions
- [ ] No tests for config flow edge cases
- [ ] No tests for coordinator lifecycle

**Observation:** Heavy reliance on unit tests, limited integration coverage.

### Phase 7: Documentation & Maintenance
**Files:** `docs/*.md`, inline documentation
**Estimated Time:** 10 minutes
**Status:** 🔲 Not Started

#### 7.1 Documentation Files

**Review Areas:**

##### 7.1.1 ARCHITECTURE.md Verification
- [ ] Does it reflect current state after rebrand to LocalShift?
- [ ] Does it document the hybrid timescale forecast?
- [ ] Verify all components are documented

##### 7.1.2 ENTITY_REFERENCE.md Verification
- [ ] Does it list all 12 sensors + 8 binary sensors + 5 switches + 5 buttons + 6 numbers?
- [ ] Check entity descriptions accuracy
- [ ] Verify attribute documentation completeness

##### 7.1.3 CHANGE_DETECTION.md Verification
- [ ] Documents all places where state changes are detected
- [ ] Explains the forecast change detection mechanism
- [ ] Covers all change detection patterns

##### 7.1.4 Other Documentation Files
- [ ] DEVELOPER_GUIDE.md - config flow and setup instructions
- [ ] FORECAST_DRIVEN_CONTROL.md - forecast logic explanation
- [ ] LOAD_SHIFTING_GUIDE.md - user guide completeness

#### 7.2 Inline Documentation

**Review Areas:**

##### 7.2.1 Code Comments
- [ ] Review docstring completeness in core files
- [ ] Check complex method documentation
- [ ] Verify parameter and return value documentation

##### 7.2.2 TODO/FIXME Comments
- [ ] Search for outstanding TODO items
- [ ] Review FIXME comments for resolution status
- [ ] Check for deprecated code markers

**Observation:** Following .clinerules for inline documentation requirements.

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