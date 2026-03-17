# Coordinator Refactoring Design

**Date:** 2026-03-17  
**Status:** Design  
**Related Issue:** #751 (Tech Debt - Large/Highly-Coupled Modules)

## Problem Statement

`coordinator/coordinator.py` is the worst tech debt offender with:
- **663 LOC** (before comments/blank lines)
- **29 imports** from LocalShift packages (highest coupling in codebase)
- **42 methods** handling diverse responsibilities
- **Severity score: 2113** (LOC + coupling×50)

**Pain Points:**
- Large file edit operations risk corrupting distant code
- 29 imports create unpredictable change ripple effects
- Testing requires mocking complex interdependencies
- Single 663-line file is harder to hold in AI context
- Parallel feature work causes merge conflicts

## Goals

**Primary:** Reduce AI edit risk and import coupling  
**Secondary:** Improve testability and maintainability  
**Non-goal:** Behavior changes (pure refactoring)

### Success Metrics

- Coordinator split into 4-5 files of 100-250 LOC each
- Each file imports from ≤10 packages (down from 29)
- All existing tests pass unchanged
- Coverage remains ≥95%
- No new circular dependencies

## Solution: Import-Based Decomposition

Split coordinator by **what changes together**, grouping methods by import dependencies and change patterns.

### File Structure

```
coordinator/
├── coordinator.py              # ~120 LOC - Facade & public API
├── lifecycle_manager.py        # ~200 LOC - Dependency wiring & startup
├── tick_scheduler.py           # ~220 LOC - Periodic tasks (FAST/MEDIUM/SLOW)
├── entity_monitor.py           # ~180 LOC - Entity tracking & health checks
└── data.py                     # (existing) CoordinatorData model
```

**Design Principle:** Each file owns a distinct import group, minimizing cross-file dependencies.

## Detailed Design

### 1. coordinator.py (~120 LOC)

**Role:** Thin facade for public API and listener management

**Responsibilities:**
- Public API methods exposed to platforms and config flow
- Listener registration and notification
- Delegation to managers
- Switch state bridge (get/set switch states)
- Options and entity ID helpers

**Key Methods:**
```python
class LocalShiftCoordinator:
    # Lifecycle (delegates to lifecycle_manager)
    async def async_start()
    async def async_stop()
    
    # Public API (delegates to appropriate managers)
    async def async_recompute_and_evaluate()
    async def async_evaluate_state_machine()
    async def async_set_battery_mode()
    async def async_set_self_consumption()
    async def async_clear_historical_cache()
    
    # Listener management
    def async_add_listener()
    def _notify_listeners()
    
    # Helpers (used by all managers)
    def get_option()
    def get_switch_state()
    def set_switch_state()
    @property entity_ids()
    def _get_entity_id()
```

**Imports:** ~8 packages
- coordinator.data
- coordinator.lifecycle_manager
- coordinator.tick_scheduler
- coordinator.entity_monitor
- const (configs)
- typing

**Public API Contract:** This interface is consumed by platforms (sensor.py, switch.py, etc.) and must remain stable.

---

### 2. lifecycle_manager.py (~200 LOC)

**Role:** Dependency injection and lifecycle management

**Responsibilities:**
- Initialize all helper modules during startup
- Wire dependencies between modules
- Manage startup/shutdown sequence
- Set up entity subscriptions
- Handle learning data persistence

**Key Methods:**
```python
class LifecycleManager:
    def __init__(self, hass, entry, coordinator)
    
    async def async_start()
        # Initialize modules:
        #   - EntityValidator
        #   - StateReader (with pricing provider)
        #   - CostTracker
        #   - BatteryController
        #   - NotificationService
        #   - ComputationEngine
        #   - StateMachine
        #   - LearningOrchestrator
        #   - SolarAccuracyTracker
        #   - ForecastBootstrapper
        #   - EvaluationDispatcher
        #   - SubscriptionManager
        
        # Wire dependencies:
        #   - Attach state_machine to learning_orchestrator
        #   - Set solar_accuracy_tracker in computation_engine
        #   - Set decision_tracker in state_machine
        
        # Set up subscriptions:
        #   - Subscribe to price/solcast/SOC entities
        #   - Route state changes to tick_scheduler
        
        # Schedule periodic tasks:
        #   - FAST tick (1 min)
        #   - MEDIUM tick (5 min)
        #   - SLOW tick (30 min)
        #   - Learning save (5 min)
        #   - Daily summary timer
        #   - Midnight reset timer
    
    async def async_stop()
        # Save learning data
        # Cancel all timers
        # Unsubscribe from entities
    
    async def _save_learning_data()
    
    def _handle_learning_save()  # Periodic 5-min save
```

**Imports:** ~15 packages (contains the heavy imports)
- computation_engine
- state.machine
- state.reader
- integration.controller
- services.notification_service
- services.evaluation_dispatcher
- services.subscription_manager
- learning.orchestrator
- forecast.bootstrapper
- forecast.solar_accuracy
- utils.validation
- utils.costs
- pricing
- const

**Storage:** Stores references to all initialized modules in coordinator:
- `coordinator._state_reader`
- `coordinator._computation_engine`
- `coordinator._state_machine`
- `coordinator._battery_controller`
- `coordinator._notification_service`
- `coordinator._entity_validator`
- `coordinator._cost_tracker`
- `coordinator._learning_orchestrator`
- `coordinator._forecast_bootstrapper`
- `coordinator._evaluation_dispatcher`
- `coordinator._subscription_manager`
- `coordinator.decision_tracker`
- `coordinator.param_optimizer`
- `coordinator.pattern_analyzer`
- `coordinator.optimization_controller`
- `coordinator.solar_accuracy_tracker`

**Key Point:** Changes here when adding new subsystems. Contains import complexity.

---

### 3. tick_scheduler.py (~220 LOC)

**Role:** Periodic task execution (tiered tick handlers)

**Responsibilities:**
- FAST tick (1 min): state machine evaluation, automation readiness checks
- MEDIUM tick (5 min): entity health, learning tasks, load data refresh
- SLOW tick (30 min): weather forecast refresh, forecast accuracy metrics
- Daily events: midnight reset, daily summary notification
- Solar energy backfill tracking
- Cost accumulation

**Key Methods:**
```python
class TickScheduler:
    def __init__(self, coordinator, lifecycle_manager)
    
    # Entity state change handler
    def _handle_state_change(event)
        # Immediate recompute when price/solcast/SOC changes
    
    # Tiered periodic handlers
    def _handle_fast_tick(now)           # 1 minute
        # Check automation ready state
        # Evaluate state machine if ready
        # Dispatch to computation engine
    
    def _handle_medium_tick(now)         # 5 minutes
        # Entity health check (via entity_monitor)
        # Load data refresh
        # Decision backfill
        # Weather learning
        # Baseline calculation
    
    def _handle_slow_tick(now)           # 30 minutes
        # Weather forecast refresh (via entity_monitor)
        # Forecast accuracy metrics
        # Forecast history save
    
    # Daily event handlers
    def _handle_midnight_reset(now)
        # Reset cost accumulators
        # Reset target_reached flag
        # Notify listeners
    
    def _handle_daily_summary(now)
        # Send daily summary notification
    
    async def _send_daily_summary()
        # Format and send end-of-day notification
    
    def reschedule_daily_summary_timer()
        # Update timer when options change
    
    # Helper methods
    def _backfill_solar_actual()
        # Calculate solar energy for completed 30-min periods
        # Update solar_accuracy_tracker
    
    def _compute_derived_values()
        # Compute all derived sensor/binary_sensor values
        # Updates coordinator.data
    
    def _accumulate_costs()
        # Accumulate per-minute energy costs
        # Updates coordinator.data cost accumulators
    
    def _is_in_startup_grace()
        # Check if state_machine has active startup grace
```

**Imports:** ~10 packages
- state.machine
- state.reader
- computation_engine
- learning.orchestrator
- forecast.pipeline
- forecast.history
- services.notification_service
- utils.costs
- const
- datetime

**Shared State Access:**
- Reads/writes `coordinator.data` (CoordinatorData)
- Calls `coordinator._notify_listeners()`
- Accesses modules via `lifecycle_manager`: `_state_machine`, `_computation_engine`, etc.

**Key Point:** All time-based logic grouped here. Easy to adjust tick frequencies or add new periodic tasks.

---

### 4. entity_monitor.py (~180 LOC)

**Role:** Entity tracking, health checks, and validation

**Responsibilities:**
- Read external entity states
- Entity health checks (availability, staleness)
- Track broken/recovered entities
- Reset tracking on config changes
- Weather forecast refresh

**Key Methods:**
```python
class EntityMonitor:
    def __init__(self, coordinator, lifecycle_manager)
    
    def _read_all_external_state()
        # Read current state of all monitored external entities
        # Delegates to state_reader
    
    def _check_entity_health()
        # Check health of all tracked entities
        # Populate integration_status, errors, warnings in CoordinatorData
        # Track broken/recovered state for notifications
    
    def reset_entity_tracking_on_options_change()
        # Reset entity tracking when options change
        # Clear broken status to allow recovery without restart
    
    async def _refresh_weather_forecast()
        # Refresh temperature forecast from weather entity
        # Use weather.get_forecasts service (HA 2024.3+)
        # Update CoordinatorData with forecast
    
    # Helper methods
    def _parse_time_option(key, default)
        # Parse time string option (HH:MM:SS) into time object
```

**Imports:** ~6 packages
- state.reader
- utils.validation
- forecast.pipeline
- const
- datetime
- typing

**Shared State Access:**
- Reads/writes `coordinator.data` (CoordinatorData)
- Accesses `coordinator.entry` for options
- Uses `lifecycle_manager._state_reader`, `_entity_validator`

**Key Point:** Isolated entity interaction logic. Minimal imports. Easy to test with mocked entities.

---

## Dependency Flow

### Initialization Sequence

```python
# In __init__.py (integration setup)
coordinator = LocalShiftCoordinator(hass, entry)
await coordinator.async_start()

# Inside coordinator.async_start()
lifecycle_manager = LifecycleManager(hass, entry, self)
await lifecycle_manager.async_start()
    # Creates all helper modules
    # Wires dependencies
    # Stores references in coordinator
    
# lifecycle_manager creates managers
tick_scheduler = TickScheduler(coordinator, lifecycle_manager)
entity_monitor = EntityMonitor(coordinator, lifecycle_manager)

# Store manager references in coordinator
coordinator._lifecycle_manager = lifecycle_manager
coordinator._tick_scheduler = tick_scheduler
coordinator._entity_monitor = entity_monitor

# lifecycle_manager sets up subscriptions
subscription_manager.subscribe_to_entities(
    tick_scheduler._handle_state_change
)

# lifecycle_manager schedules periodic tasks
hass.async_track_time_interval(
    tick_scheduler._handle_fast_tick,
    PERIODIC_INTERVAL_FAST  # 1 min
)
hass.async_track_time_interval(
    tick_scheduler._handle_medium_tick,
    PERIODIC_INTERVAL_MEDIUM  # 5 min
)
hass.async_track_time_interval(
    tick_scheduler._handle_slow_tick,
    PERIODIC_INTERVAL_SLOW  # 30 min
)
```

### Data Flow: External Entity Change

```
External Entity Change (price/solcast/SOC)
  ↓
subscription_manager (in lifecycle_manager)
  ↓
tick_scheduler._handle_state_change()
  ↓
coordinator.async_recompute_and_evaluate()
  ↓
  1. entity_monitor._read_all_external_state()
     → state_reader reads entities
     → Updates coordinator.data
  
  2. tick_scheduler._compute_derived_values()
     → Computes derived sensors
     → Updates coordinator.data
  
  3. coordinator._notify_listeners()
     → Triggers entity state updates in HA
  
  4. state_machine.evaluate_state_machine()
     → Evaluates mode transitions
     → Issues battery commands
```

### Shared State Access

All managers have access to:
- `coordinator.data` (CoordinatorData) - read/write shared state
- `coordinator.hass` - Home Assistant instance
- `coordinator.entry` - Config entry
- `coordinator.get_option()` / `get_switch_state()` - configuration helpers
- `lifecycle_manager._state_machine`, `_computation_engine`, etc. - helper modules

**Pattern:** Managers call back into coordinator for shared resources, avoiding tight coupling between managers.

## Migration Strategy

### Phase 1: Extract Managers (No Behavior Change)

**Goal:** Create manager classes and move methods without changing behavior.

**Steps:**

1. **Create empty manager classes**
   - `coordinator/lifecycle_manager.py` - empty class
   - `coordinator/tick_scheduler.py` - empty class
   - `coordinator/entity_monitor.py` - empty class

2. **Move methods to managers (one file at a time)**
   - Copy methods from coordinator.py to manager
   - Update method signatures to accept coordinator reference
   - Keep all imports in coordinator.py initially
   - Verify tests pass

3. **Update coordinator to delegate**
   - Replace method bodies with delegation calls
   - Example: `async def async_start() → await self._lifecycle_manager.async_start()`
   - Verify tests pass

4. **Repeat for each manager**
   - lifecycle_manager → tick_scheduler → entity_monitor
   - Run full test suite after each file

**Verification:** All tests pass after each manager extraction.

### Phase 2: Update Imports

**Goal:** Move imports from coordinator.py to appropriate managers.

**Steps:**

1. **Analyze import usage**
   - For each import in coordinator.py
   - Determine which manager(s) use it
   - Move import to manager file

2. **Update TYPE_CHECKING blocks**
   - Keep TYPE_CHECKING imports in coordinator.py for type hints
   - Use forward references in manager type hints

3. **Verify no circular dependencies**
   - Run: `python -m custom_components.localshift.coordinator`
   - Check for ImportError

4. **Run tests**
   - Full test suite must pass
   - No new warnings about imports

**Verification:** Import count in coordinator.py drops to ~8, no circular deps.

### Phase 3: Cleanup

**Goal:** Remove old code, update documentation, verify coverage.

**Steps:**

1. **Remove old methods from coordinator.py**
   - Delete method bodies that now delegate
   - Keep only facade methods

2. **Update docstrings**
   - coordinator.py: Document facade pattern
   - Managers: Document responsibilities

3. **Add unit tests for managers**
   - `tests/coordinator/test_lifecycle_manager.py`
   - `tests/coordinator/test_tick_scheduler.py`
   - `tests/coordinator/test_entity_monitor.py`
   - Focus on isolated behavior, mock dependencies

4. **Verify coverage**
   - Run: `pytest --cov=custom_components/localshift/coordinator --cov-report=term-missing`
   - Target: ≥95% coverage (current baseline)

5. **Update ARCHITECTURE.md**
   - Document new coordinator structure
   - Explain manager responsibilities

**Verification:** Coverage ≥95%, all docs updated.

## Testing Strategy

### Existing Test Coverage

**Current:** 20 integration tests in `tests/coordinator/test_coordinator.py` (1366 LOC)

**Approach:** Keep existing tests unchanged. They verify coordinator behavior end-to-end through the public API.

### Refactoring Test Strategy

1. **Integration tests (existing)**
   - Test through public API only: `coordinator.async_start()`, etc.
   - Should pass throughout refactoring (regression safety)
   - No changes to these tests during refactoring

2. **New unit tests for managers**
   ```
   tests/coordinator/
   ├── test_coordinator.py          # (existing) Integration tests
   ├── test_lifecycle_manager.py    # New unit tests
   ├── test_tick_scheduler.py       # New unit tests
   └── test_entity_monitor.py       # New unit tests
   ```

3. **Unit test focus**
   - Mock coordinator and helper modules
   - Test each manager method in isolation
   - Verify method behavior, not integration
   - Aim for 95% coverage on new files

4. **Verification checkpoints**
   - After each manager extraction: run full test suite
   - After moving methods: verify no behavior change
   - After import updates: verify no circular deps
   - Final: coverage check ≥95%

**Critical Constraint:** No new behavior changes during refactoring. Pure code movement. Any behavior fixes should be separate commits after refactoring is complete.

## Risk Mitigation

### Identified Risks

1. **Circular import dependencies**
   - **Likelihood:** Medium
   - **Impact:** High (breaks imports)
   - **Mitigation:** 
     - Use TYPE_CHECKING guards for type hints
     - Dependency injection pattern (pass coordinator to managers)
     - Avoid manager-to-manager imports
   - **Verification:** `python -m custom_components.localshift.coordinator` after each change

2. **Broken entity platform references**
   - **Likelihood:** Low
   - **Impact:** High (platforms can't access coordinator)
   - **Mitigation:** Keep coordinator.py public API unchanged
   - **Verification:** Run platform tests (`tests/test_sensor.py`, `tests/test_switch.py`)

3. **State machine timing regressions**
   - **Likelihood:** Medium
   - **Impact:** High (battery mode transitions break)
   - **Mitigation:** Keep tick scheduling logic identical, no timing changes
   - **Verification:** Run `tests/state/test_machine.py`

4. **Test coverage drop**
   - **Likelihood:** Low
   - **Impact:** Medium (harder to catch bugs)
   - **Mitigation:** Refactor is code movement only, coverage should stay same or improve
   - **Verification:** Coverage report before/after shows ≥95%

5. **Merge conflicts during parallel work**
   - **Likelihood:** Low (single developer)
   - **Impact:** Medium (manual merge required)
   - **Mitigation:** Complete refactoring in single PR before other work
   - **Verification:** No open PRs when starting refactoring

### Rollback Plan

Each phase is a separate commit. If tests fail at any point:

1. Revert the failed commit
2. Investigate root cause
3. Fix the issue in isolation
4. Retry the migration step

**Atomic commits ensure safe rollback.**

## Success Criteria

**Technical:**
- ✅ Coordinator split into 4 files of 100-220 LOC each
- ✅ Each file imports from ≤10 packages (down from 29)
- ✅ All existing tests pass unchanged
- ✅ Coverage remains ≥95%
- ✅ No circular dependencies
- ✅ No behavior changes (verified by tests)

**AI Maintainability:**
- ✅ Edit operations on <250 LOC files (safer edits)
- ✅ Import impact analysis localized to specific files
- ✅ Clear ownership: "tick changes go in tick_scheduler.py"
- ✅ Reduced merge conflict risk for parallel work

**Documentation:**
- ✅ ARCHITECTURE.md updated with new structure
- ✅ Each manager has clear docstring explaining role
- ✅ Migration preserved git history (no "big bang" rewrite)

## Out of Scope

**Not included in this refactoring:**

1. **Behavior changes** - Pure code movement only
2. **Performance optimizations** - No changes to tick frequencies or logic
3. **New features** - No new functionality added
4. **Other large modules** - Only coordinator.py (engine/core.py, state/machine.py addressed separately)
5. **Architectural redesign** - Not event-driven, not dependency inversion (keep it simple)

**These can be addressed in future work after refactoring is stable.**

## Future Considerations

After this refactoring is complete and stable, consider:

1. **Apply pattern to other large modules**
   - `engine/core.py` (1629 LOC, 3 imports) - split by optimizer phase
   - `state/machine.py` (938 LOC, 15 imports) - split by mode transition handlers
   - See issue #751 for full list

2. **Extract common patterns**
   - Subscription management (already done via SubscriptionManager)
   - Evaluation dispatch (already done via EvaluationDispatcher)

3. **Improve manager testability**
   - Consider dependency injection for helper modules
   - Reduce reliance on coordinator.data global state

**But not yet. Prove this pattern works first.**

## Related

- **Issue:** #751 (Tech Debt - Large/Highly-Coupled Modules)
- **Analysis:** `tmp/large_coupled.tsv` (severity scoring)
- **Other candidates:** engine/core.py, state/machine.py, computation_engine.py
