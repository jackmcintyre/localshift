# Coordinator Refactoring Plan - ADDENDUM

**Purpose:** Add LifecycleManager extraction tasks to complete 4-file refactoring per spec.

**Insert Location:** After Task 1.7 in main plan, before Phase 2.

---

## Task 1.8: Create LifecycleManager Skeleton

**Goal:** Create empty LifecycleManager class to manage async_start initialization logic.

**Files:**
- Create: `custom_components/localshift/coordinator/lifecycle_manager.py`

- [ ] **Step 1: Create LifecycleManager class file**

Create file: `custom_components/localshift/coordinator/lifecycle_manager.py`

```python
"""Lifecycle management and dependency injection for LocalShift coordinator.

Responsibilities:
- Initialize all helper modules during startup
- Wire dependencies between modules
- Manage entity subscriptions
- Handle learning data persistence
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import LocalShiftCoordinator

_LOGGER = logging.getLogger(__name__)


class LifecycleManager:
    """Manages coordinator lifecycle and dependency injection."""

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
    ) -> None:
        """Initialize lifecycle manager.

        Args:
            coordinator: Parent coordinator instance
        """
        self._coordinator = coordinator
        self._hass: HomeAssistant = coordinator.hass
        self._entry: ConfigEntry = coordinator.entry
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/coordinator/test_coordinator.py -v`
Expected: PASS (no behavior change yet)

- [ ] **Step 3: Commit**

```bash
git add custom_components/localshift/coordinator/lifecycle_manager.py
git commit -m "refactor(coordinator): create LifecycleManager skeleton

- Add LifecycleManager class with basic structure
- No behavior change - skeleton only
- Part of coordinator refactoring (#751)"
```

---

## Task 1.9: Extract Module Initialization to LifecycleManager

**Goal:** Move module initialization logic from coordinator.async_start to LifecycleManager.initialize_modules().

**Files:**
- Modify: `custom_components/localshift/coordinator/lifecycle_manager.py`
- Modify: `custom_components/localshift/coordinator/coordinator.py`

- [ ] **Step 1: Add initialize_modules method to LifecycleManager**

Add to `lifecycle_manager.py`:

```python
    async def initialize_modules(self) -> None:
        """Initialize all helper modules.
        
        Creates and wires:
        - Entity validator
        - State reader (with pricing provider)
        - Cost tracker
        - Battery controller
        - Notification service
        - Computation engine
        - State machine
        - Learning orchestrator
        - Solar accuracy tracker
        """
        # Import all required modules
        from ..computation_engine import ComputationEngine
        from ..integration.controller import BatteryController
        from ..services.notification_service import NotificationService
        from ..state.machine import StateMachine
        from ..state.reader import StateReader
        from ..utils.costs import CostTracker
        from ..utils.validation import EntityValidator
        from ..learning.orchestrator import LearningOrchestrator
        from ..forecast.solar_accuracy import SolarAccuracyTracker
        from ..const import (
            CONF_PRICING_DATA_SOURCE,
            DEFAULT_PRICING_DATA_SOURCE,
            CONF_BATTERY_TARGET,
            DEFAULT_BATTERY_TARGET,
        )
        from ..pricing import create_provider

        _LOGGER.info("Initializing helper modules")

        # Initialize entity validator
        self._coordinator._entity_validator = EntityValidator(
            self._hass, self._coordinator._get_entity_id
        )

        # Initialize pricing provider
        pricing_source = self._entry.data.get(
            CONF_PRICING_DATA_SOURCE, DEFAULT_PRICING_DATA_SOURCE
        )
        _pricing_provider = create_provider(pricing_source)

        # Initialize state reader
        self._coordinator._state_reader = StateReader(
            self._hass,
            self._entry,
            self._coordinator._entity_validator,
            _pricing_provider,
        )

        # Initialize cost tracker
        self._coordinator._cost_tracker = CostTracker(self._hass)

        # Initialize battery controller
        self._coordinator._battery_controller = BatteryController(
            self._hass, self._coordinator._get_entity_id
        )

        # Initialize notification service
        self._coordinator._notification_service = NotificationService(
            self._hass,
            self._entry,
            self._coordinator._get_entity_id,
            self._coordinator.get_switch_state,
        )

        # Initialize computation engine
        self._coordinator._computation_engine = ComputationEngine(
            self._hass,
            self._entry,
            self._coordinator._get_entity_id,
            self._coordinator.get_switch_state,
        )

        # Initialize state machine
        self._coordinator._state_machine = StateMachine(
            self._coordinator._battery_controller,
            self._coordinator._notification_service,
            self._coordinator.get_switch_state,
            self._coordinator.get_option,
            self._coordinator._entity_validator,
            decision_tracker=None,  # Will be set after tracker is initialized
        )

        # Issue #551: Set startup grace period IMMEDIATELY
        self._coordinator._state_machine.set_startup_grace(30)

        # Initialize learning orchestrator
        self._coordinator._learning_orchestrator = LearningOrchestrator(
            self._hass,
            self._entry,
            self._coordinator.get_switch_state,
        )
        await self._coordinator._learning_orchestrator.async_initialize()

        # Expose learning components
        self._coordinator.decision_tracker = (
            self._coordinator._learning_orchestrator.decision_tracker
        )
        self._coordinator.param_optimizer = (
            self._coordinator._learning_orchestrator.param_optimizer
        )
        self._coordinator.pattern_analyzer = (
            self._coordinator._learning_orchestrator.pattern_analyzer
        )
        self._coordinator.optimization_controller = (
            self._coordinator._learning_orchestrator.optimization_controller
        )

        # Initialize solar accuracy tracker
        self._coordinator.solar_accuracy_tracker = SolarAccuracyTracker(
            self._hass, self._entry.entry_id
        )
        await self._coordinator.solar_accuracy_tracker.async_load()

        # Wire dependencies
        if self._coordinator._computation_engine is not None:
            self._coordinator._computation_engine.set_solar_accuracy_tracker(
                self._coordinator.solar_accuracy_tracker
            )

        if self._coordinator._learning_orchestrator is not None:
            self._coordinator._learning_orchestrator.attach_state_machine(
                self._coordinator._state_machine
            )

        # Set battery target SOC
        self._coordinator.data.battery_target_soc = float(
            self._coordinator.get_option(CONF_BATTERY_TARGET, DEFAULT_BATTERY_TARGET)
        )

        _LOGGER.info("Helper modules initialized successfully")
```

- [ ] **Step 2: Update coordinator.async_start to delegate**

Replace module initialization section in `coordinator.py` async_start (lines 182-258) with:

```python
    async def async_start(self) -> None:
        """Start listening to entity changes and periodic timer."""
        # Import LifecycleManager
        from .lifecycle_manager import LifecycleManager
        
        # Create lifecycle manager
        self._lifecycle_manager = LifecycleManager(self)
        
        # Initialize all modules
        await self._lifecycle_manager.initialize_modules()
        
        # Create managers (entity_monitor, tick_scheduler already created earlier)
        # ... rest of async_start continues below ...
```

- [ ] **Step 3: Add lifecycle_manager reference to coordinator __init__**

In `coordinator.py` `__init__`, add:

```python
        # Lifecycle manager (created in async_start)
        self._lifecycle_manager: LifecycleManager | None = None
```

- [ ] **Step 4: Run integration tests**

Run: `pytest tests/coordinator/test_coordinator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/coordinator/lifecycle_manager.py custom_components/localshift/coordinator/coordinator.py
git commit -m "refactor(coordinator): extract module initialization to LifecycleManager

- Move module initialization logic to LifecycleManager.initialize_modules
- Create 15+ helper modules in LifecycleManager
- Update coordinator.async_start to delegate
- All integration tests pass"
```

---

## Task 1.10: Extract Subscription Setup to LifecycleManager

**Goal:** Move subscription and timer setup logic to LifecycleManager.

**Files:**
- Modify: `custom_components/localshift/coordinator/lifecycle_manager.py`
- Modify: `custom_components/localshift/coordinator/coordinator.py`

- [ ] **Step 1: Add setup_subscriptions method to LifecycleManager**

Add to `lifecycle_manager.py`:

```python
    def setup_subscriptions(self) -> None:
        """Set up entity subscriptions and periodic timers.
        
        Subscribes to:
        - Price entities (general, feed-in, spike)
        - Solcast entities (today, tomorrow)
        
        Schedules:
        - FAST tick (1 min): evaluation dispatch
        - MEDIUM tick (5 min): health checks, learning
        - SLOW tick (30 min): weather, metrics
        - Midnight reset
        - Daily summary
        - Learning save (5 min)
        """
        from ..const import (
            CONF_PRICING_GENERAL_PRICE,
            CONF_PRICING_FEED_IN_PRICE,
            CONF_PRICING_GENERAL_FORECAST,
            CONF_PRICING_FEED_IN_FORECAST,
            CONF_PRICING_PRICE_SPIKE,
            CONF_SOLCAST_FORECAST_TODAY,
            CONF_SOLCAST_FORECAST_TOMORROW,
            CONF_DEMAND_WINDOW_END,
            DEFAULT_DEMAND_WINDOW_END,
        )
        from ..services.evaluation_dispatcher import EvaluationDispatcher
        from ..services.subscription_manager import SubscriptionManager

        _LOGGER.info("Setting up subscriptions and timers")

        # Collect monitored entities
        monitored_entities = [
            # Price entities - trigger mode decisions on price changes
            self._coordinator._get_entity_id(CONF_PRICING_GENERAL_PRICE),
            self._coordinator._get_entity_id(CONF_PRICING_FEED_IN_PRICE),
            self._coordinator._get_entity_id(CONF_PRICING_GENERAL_FORECAST),
            self._coordinator._get_entity_id(CONF_PRICING_FEED_IN_FORECAST),
            self._coordinator._get_entity_id(CONF_PRICING_PRICE_SPIKE),
            # Solcast entities - trigger forecast recomputation
            self._coordinator._get_entity_id(CONF_SOLCAST_FORECAST_TODAY),
            self._coordinator._get_entity_id(CONF_SOLCAST_FORECAST_TOMORROW),
        ]

        # Create evaluation dispatcher
        from ..coordinator.coordinator import STALE_PRICE_THRESHOLD
        
        self._coordinator._evaluation_dispatcher = EvaluationDispatcher(
            self._hass,
            self._coordinator._get_entity_id,
            self._coordinator._read_all_external_state,
            self._coordinator._notify_listeners,
            self._coordinator._evaluate_state_machine,
            self._coordinator._state_machine,
            STALE_PRICE_THRESHOLD,
        )

        # Parse demand window end time
        dw_end = self._coordinator._entity_monitor.parse_time_option(
            CONF_DEMAND_WINDOW_END,
            DEFAULT_DEMAND_WINDOW_END,
        )

        # Create subscription manager
        from ..coordinator.coordinator import (
            PERIODIC_INTERVAL_FAST,
            PERIODIC_INTERVAL_MEDIUM,
            PERIODIC_INTERVAL_SLOW,
            LEARNING_SAVE_INTERVAL,
        )
        
        self._coordinator._subscription_manager = SubscriptionManager(
            self._hass,
            self._coordinator._tick_scheduler.handle_state_change,
            self._coordinator._tick_scheduler.handle_fast_tick,
            self._coordinator._tick_scheduler.handle_medium_tick,
            self._coordinator._tick_scheduler.handle_slow_tick,
            self._coordinator._tick_scheduler.handle_midnight_reset,
            self._coordinator._tick_scheduler.handle_daily_summary,
            self._handle_learning_save,
            PERIODIC_INTERVAL_FAST,
            PERIODIC_INTERVAL_MEDIUM,
            PERIODIC_INTERVAL_SLOW,
            LEARNING_SAVE_INTERVAL,
        )
        self._coordinator._subscription_manager.start(monitored_entities, dw_end)

        _LOGGER.info("Subscriptions and timers set up successfully")

    def _handle_learning_save(self, now) -> None:
        """Periodic save of learning data."""
        self._hass.async_create_task(self._save_learning_data())

    async def _save_learning_data(self) -> None:
        """Save all learning system data to storage."""
        if self._coordinator._learning_orchestrator is None:
            return

        _LOGGER.debug("Saving learning data to storage")
        await self._coordinator._learning_orchestrator.async_save_all()
```

- [ ] **Step 2: Update coordinator.async_start to delegate**

Replace subscription setup section in `coordinator.py` async_start (lines 260-314) with:

```python
        # Set up subscriptions and timers
        self._lifecycle_manager.setup_subscriptions()
```

- [ ] **Step 3: Move _handle_learning_save and _save_learning_data**

Remove `_handle_learning_save` and `_save_learning_data` from coordinator.py (they're now in lifecycle_manager).

- [ ] **Step 4: Run integration tests**

Run: `pytest tests/coordinator/test_coordinator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/coordinator/lifecycle_manager.py custom_components/localshift/coordinator/coordinator.py
git commit -m "refactor(coordinator): extract subscription setup to LifecycleManager

- Move subscription and timer setup to LifecycleManager.setup_subscriptions
- Move _handle_learning_save and _save_learning_data
- Update coordinator.async_start to delegate
- All integration tests pass"
```

---

## Task 1.11: Extract Initial State Read to LifecycleManager

**Goal:** Move initial state read logic to LifecycleManager.

**Files:**
- Modify: `custom_components/localshift/coordinator/lifecycle_manager.py`
- Modify: `custom_components/localshift/coordinator/coordinator.py`

- [ ] **Step 1: Add read_initial_state method to LifecycleManager**

Add to `lifecycle_manager.py`:

```python
    def read_initial_state(self) -> None:
        """Read initial state and trigger evaluation if ready.
        
        Issue #349: Validates that all required inputs are populated.
        Issue #551: Suppresses warning during startup grace.
        Issue #478: Triggers immediate evaluation if automation ready.
        """
        _LOGGER.info("Reading initial state")

        # Read all external entity states
        if self._coordinator._entity_monitor is not None:
            self._coordinator._entity_monitor.read_all_external_state()

        # Check if automation is ready
        if self._coordinator._state_reader is not None:
            self._coordinator._state_reader.check_automation_ready(
                self._coordinator.data, suppress_warning=True
            )

        # Trigger immediate evaluation if automation ready
        if self._coordinator._evaluation_dispatcher is not None:
            self._coordinator._evaluation_dispatcher.maybe_trigger_on_startup_ready(
                lambda: (
                    self._coordinator.data.automation_ready
                    if self._coordinator.data
                    else False
                )
            )
```

- [ ] **Step 2: Update coordinator.async_start to delegate**

Replace initial state read section in `coordinator.py` async_start (lines 316-329+) with:

```python
        # Read initial state
        self._lifecycle_manager.read_initial_state()

        _LOGGER.info("Coordinator started successfully")
```

- [ ] **Step 3: Run integration tests**

Run: `pytest tests/coordinator/test_coordinator.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add custom_components/localshift/coordinator/lifecycle_manager.py custom_components/localshift/coordinator/coordinator.py
git commit -m "refactor(coordinator): extract initial state read to LifecycleManager

- Move read_initial_state logic to LifecycleManager
- Update coordinator.async_start to delegate
- async_start now delegates all initialization to LifecycleManager
- All integration tests pass"
```

---

## Task 1.12: Extract async_stop to LifecycleManager

**Goal:** Move shutdown logic to LifecycleManager for symmetry with async_start.

**Files:**
- Modify: `custom_components/localshift/coordinator/lifecycle_manager.py`
- Modify: `custom_components/localshift/coordinator/coordinator.py`

- [ ] **Step 1: Add async_stop method to LifecycleManager**

Add to `lifecycle_manager.py`:

```python
    async def async_stop(self) -> None:
        """Stop lifecycle manager and clean up.
        
        Saves learning data and stops all subscriptions.
        """
        _LOGGER.info("Stopping lifecycle manager")

        # Save learning data before shutdown
        await self._save_learning_data()

        # Stop subscriptions
        if self._coordinator._subscription_manager is not None:
            self._coordinator._subscription_manager.stop()

        _LOGGER.info("Lifecycle manager stopped")
```

- [ ] **Step 2: Update coordinator.async_stop to delegate**

Replace body of `coordinator.py` async_stop with:

```python
    async def async_stop(self) -> None:
        """Stop listening and clean up."""
        if self._lifecycle_manager is not None:
            await self._lifecycle_manager.async_stop()
```

- [ ] **Step 3: Run integration tests**

Run: `pytest tests/coordinator/test_coordinator.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add custom_components/localshift/coordinator/lifecycle_manager.py custom_components/localshift/coordinator/coordinator.py
git commit -m "refactor(coordinator): extract async_stop to LifecycleManager

- Move shutdown logic to LifecycleManager.async_stop
- Update coordinator.async_stop to delegate
- Complete LifecycleManager extraction
- All integration tests pass"
```

---

## Phase 3 Addition: Add LifecycleManager Unit Tests

**Insert after Task 3.2 in main plan**

### Task 3.3: Add LifecycleManager Unit Tests

**Goal:** Add focused unit tests for LifecycleManager in isolation.

**Files:**
- Create: `tests/coordinator/test_lifecycle_manager.py`

- [ ] **Step 1: Create test file**

Create file: `tests/coordinator/test_lifecycle_manager.py`

```python
"""Unit tests for LifecycleManager."""

from unittest.mock import MagicMock, AsyncMock, patch
import pytest
from custom_components.localshift.coordinator.lifecycle_manager import LifecycleManager


@pytest.fixture
def mock_coordinator():
    """Create mock coordinator."""
    coordinator = MagicMock()
    coordinator.hass = MagicMock()
    coordinator.entry = MagicMock()
    coordinator.entry.data = {"pricing_data_source": "amber"}
    coordinator.entry.entry_id = "test_entry"
    coordinator.data = MagicMock()
    coordinator._get_entity_id = MagicMock(return_value="sensor.test")
    coordinator.get_option = MagicMock(return_value=90)
    coordinator.get_switch_state = MagicMock(return_value=True)
    coordinator._entity_monitor = MagicMock()
    coordinator._tick_scheduler = MagicMock()
    coordinator._read_all_external_state = MagicMock()
    coordinator._notify_listeners = MagicMock()
    coordinator._evaluate_state_machine = AsyncMock()
    return coordinator


@pytest.fixture
def lifecycle_manager(mock_coordinator):
    """Create LifecycleManager instance."""
    return LifecycleManager(mock_coordinator)


@pytest.mark.asyncio
async def test_initialize_modules(lifecycle_manager, mock_coordinator):
    """Test module initialization."""
    with patch("custom_components.localshift.coordinator.lifecycle_manager.EntityValidator"), \
         patch("custom_components.localshift.coordinator.lifecycle_manager.StateReader"), \
         patch("custom_components.localshift.coordinator.lifecycle_manager.CostTracker"), \
         patch("custom_components.localshift.coordinator.lifecycle_manager.BatteryController"), \
         patch("custom_components.localshift.coordinator.lifecycle_manager.NotificationService"), \
         patch("custom_components.localshift.coordinator.lifecycle_manager.ComputationEngine"), \
         patch("custom_components.localshift.coordinator.lifecycle_manager.StateMachine") as mock_sm, \
         patch("custom_components.localshift.coordinator.lifecycle_manager.LearningOrchestrator") as mock_learn, \
         patch("custom_components.localshift.coordinator.lifecycle_manager.SolarAccuracyTracker") as mock_solar, \
         patch("custom_components.localshift.coordinator.lifecycle_manager.create_provider"):
        
        # Mock async methods
        mock_learn.return_value.async_initialize = AsyncMock()
        mock_solar.return_value.async_load = AsyncMock()
        
        # Mock learning components
        mock_learn.return_value.decision_tracker = MagicMock()
        mock_learn.return_value.param_optimizer = MagicMock()
        mock_learn.return_value.pattern_analyzer = MagicMock()
        mock_learn.return_value.optimization_controller = MagicMock()
        
        # Call initialize_modules
        await lifecycle_manager.initialize_modules()
        
        # Verify state machine startup grace was set
        mock_sm.return_value.set_startup_grace.assert_called_once_with(30)


def test_setup_subscriptions(lifecycle_manager, mock_coordinator):
    """Test subscription setup."""
    with patch("custom_components.localshift.coordinator.lifecycle_manager.EvaluationDispatcher"), \
         patch("custom_components.localshift.coordinator.lifecycle_manager.SubscriptionManager") as mock_sub:
        
        lifecycle_manager.setup_subscriptions()
        
        # Verify subscription manager was created and started
        mock_sub.return_value.start.assert_called_once()


def test_read_initial_state(lifecycle_manager, mock_coordinator):
    """Test initial state reading."""
    mock_coordinator._state_reader = MagicMock()
    mock_coordinator._evaluation_dispatcher = MagicMock()
    
    lifecycle_manager.read_initial_state()
    
    # Verify read_all_external_state was called
    mock_coordinator._entity_monitor.read_all_external_state.assert_called_once()
    
    # Verify automation ready check
    mock_coordinator._state_reader.check_automation_ready.assert_called_once()


@pytest.mark.asyncio
async def test_async_stop(lifecycle_manager, mock_coordinator):
    """Test async stop."""
    mock_coordinator._subscription_manager = MagicMock()
    mock_coordinator._learning_orchestrator = MagicMock()
    mock_coordinator._learning_orchestrator.async_save_all = AsyncMock()
    
    await lifecycle_manager.async_stop()
    
    # Verify learning data was saved
    mock_coordinator._learning_orchestrator.async_save_all.assert_called_once()
    
    # Verify subscriptions were stopped
    mock_coordinator._subscription_manager.stop.assert_called_once()
```

- [ ] **Step 2: Run LifecycleManager tests**

Run: `pytest tests/coordinator/test_lifecycle_manager.py -v`
Expected: PASS

- [ ] **Step 3: Check coverage**

Run: `pytest tests/coordinator/test_lifecycle_manager.py --cov=custom_components/localshift/coordinator/lifecycle_manager --cov-report=term-missing`
Expected: High coverage (aim for >80%)

- [ ] **Step 4: Commit**

```bash
git add tests/coordinator/test_lifecycle_manager.py
git commit -m "test(coordinator): add unit tests for LifecycleManager

- Test initialize_modules, setup_subscriptions
- Test read_initial_state, async_stop
- Coverage >80% for LifecycleManager"
```

---

## Phase 4 Update: Final Verification

**Update Task 4.2 (ARCHITECTURE.md) to include LifecycleManager:**

Add to the coordinator section:

```markdown
### lifecycle_manager.py (~200 LOC)
- Module initialization and dependency injection
- Subscription and timer setup
- Initial state reading
- Shutdown and cleanup

### Imports
- coordinator.py: ~8 imports (facade only)
- lifecycle_manager.py: ~15 imports (DI container)
- entity_monitor.py: ~6 imports (entity interaction)
- tick_scheduler.py: ~10 imports (periodic tasks)

Total: ~39 imports distributed across 4 files (vs 29 concentrated in 1)
```

---

## Summary

This addendum adds 5 tasks (1.8-1.12) to extract LifecycleManager from coordinator.py:

1. **Task 1.8:** Create LifecycleManager skeleton
2. **Task 1.9:** Extract module initialization (~150 LOC)
3. **Task 1.10:** Extract subscription setup (~50 LOC)
4. **Task 1.11:** Extract initial state read (~20 LOC)
5. **Task 1.12:** Extract async_stop (~10 LOC)

Plus unit tests in Phase 3.

**Result:** Complete 4-file refactoring matching spec exactly.
