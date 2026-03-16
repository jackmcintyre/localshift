# Pricing Provider Abstraction Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralize all Amber/Amber Express provider logic into a `pricing/` module with Protocol pattern and normalized data structures.

**Architecture:** Create `PricingProvider` Protocol with `AmberProvider` and `AmberExpressProvider` implementations. All provider-specific code moves to `pricing/provider.py`. Data is normalized to `ForecastSlot` dataclass at read time.

**Tech Stack:** Python 3.13, dataclasses, typing.Protocol, Home Assistant util.dt

---

## Phase 1: Create Provider Module

### Task 1: Create ForecastSlot dataclass

**Files:**
- Create: `custom_components/localshift/pricing/types.py`
- Test: `tests/pricing/test_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pricing/test_types.py
"""Tests for pricing types."""
from datetime import datetime, timezone

from custom_components.localshift.pricing.types import ForecastSlot


def test_forecast_slot_creation():
    """Test ForecastSlot can be created with required fields."""
    slot = ForecastSlot(
        start_time=datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc),
        duration=30,
        per_kwh=0.15,
        is_spike=False,
        source_type="amber",
    )
    assert slot.duration == 30
    assert slot.per_kwh == 0.15
    assert slot.is_spike is False


def test_forecast_slot_is_frozen():
    """Test ForecastSlot is immutable (frozen dataclass)."""
    slot = ForecastSlot(
        start_time=datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc),
        duration=30,
        per_kwh=0.15,
        is_spike=False,
        source_type="amber",
    )
    try:
        slot.per_kwh = 0.20
        assert False, "Should not be able to modify frozen dataclass"
    except AttributeError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest tests/pricing/test_types.py -v`
Expected: FAIL with "No module named 'custom_components.localshift.pricing'"

- [ ] **Step 3: Create the types module**

```python
# custom_components/localshift/pricing/__init__.py
"""Pricing provider module."""
```

```python
# custom_components/localshift/pricing/types.py
"""Normalized data types for pricing providers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ForecastSlot:
    """Canonical forecast data structure used throughout LocalShift.
    
    All pricing providers normalize their data to this format,
    ensuring consumers have a consistent interface.
    """
    start_time: datetime
    duration: int  # minutes
    per_kwh: float
    is_spike: bool
    source_type: str  # "amber" or "amber_express" for debugging
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest tests/pricing/test_types.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/pricing/ tests/pricing/
git commit -m "feat(#300): add ForecastSlot dataclass for pricing providers"
```

### Task 2: Create PricingProvider Protocol

**Files:**
- Create: `custom_components/localshift/pricing/provider.py`
- Test: `tests/pricing/test_provider.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pricing/test_provider.py
"""Tests for pricing provider protocol."""
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from custom_components.localshift.pricing.provider import PricingProvider
    from custom_components.localshift.pricing.types import ForecastSlot


def test_protocol_has_required_methods():
    """Test PricingProvider protocol defines required interface."""
    from custom_components.localshift.pricing.provider import PricingProvider
    
    # Protocol should have these attributes
    assert hasattr(PricingProvider, 'name')
    assert hasattr(PricingProvider, 'entity_prefix')
    assert hasattr(PricingProvider, 'read_forecasts')
    assert hasattr(PricingProvider, 'is_spike')


def test_amber_provider_implements_protocol():
    """Test AmberProvider correctly implements PricingProvider."""
    from custom_components.localshift.pricing.provider import AmberProvider
    
    provider = AmberProvider()
    assert provider.name == "amber"
    assert provider.entity_prefix == "sensor.100h_"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest tests/pricing/test_provider.py -v`
Expected: FAIL with "cannot import name 'PricingProvider'"

- [ ] **Step 3: Create the provider module with Protocol**

```python
# custom_components/localshift/pricing/provider.py
"""Pricing provider protocol and implementations."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .types import ForecastSlot

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


class PricingProvider(Protocol):
    """Protocol for pricing data providers.
    
    Implementations handle provider-specific differences:
    - Entity ID construction
    - Forecast data location and format
    - Spike detection logic
    """
    
    @property
    def name(self) -> str:
        """Provider identifier for logging/debugging."""
        ...
    
    @property
    def entity_prefix(self) -> str:
        """Return entity prefix like 'sensor.100h_' or 'sensor.amber_express_100h_'."""
        ...
    
    def read_forecasts(
        self, 
        hass: HomeAssistant, 
        price_entity_id: str
    ) -> list[ForecastSlot]:
        """Read and normalize forecast data from price entity.
        
        Args:
            hass: Home Assistant instance
            price_entity_id: The price sensor entity ID
            
        Returns:
            List of normalized ForecastSlot objects
        """
        ...
    
    def is_spike(self, forecast_entry: dict[str, Any]) -> bool:
        """Check if a raw forecast entry represents a price spike.
        
        Args:
            forecast_entry: Raw forecast entry from provider
            
        Returns:
            True if this entry represents a spike
        """
        ...


class AmberProvider:
    """Amber pricing provider (original 100H integration)."""
    
    @property
    def name(self) -> str:
        return "amber"
    
    @property
    def entity_prefix(self) -> str:
        return "sensor.100h_"
    
    def read_forecasts(
        self, hass: HomeAssistant, price_entity_id: str
    ) -> list[ForecastSlot]:
        # Implementation in next task
        raise NotImplementedError
    
    def is_spike(self, entry: dict[str, Any]) -> bool:
        # Implementation in next task
        raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest tests/pricing/test_provider.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/pricing/provider.py tests/pricing/test_provider.py
git commit -m "feat(#300): add PricingProvider protocol and AmberProvider skeleton"
```

### Task 3: Implement AmberProvider fully

**Files:**
- Modify: `custom_components/localshift/pricing/provider.py`
- Test: `tests/pricing/test_provider.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/pricing/test_provider.py

def test_amber_provider_read_forecasts():
    """Test AmberProvider reads forecasts from separate entity."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    
    from custom_components.localshift.pricing.provider import AmberProvider
    
    provider = AmberProvider()
    
    # Mock hass with forecast entity
    hass = MagicMock()
    forecast_state = MagicMock()
    forecast_state.attributes = {
        "forecasts": [
            {
                "start_time": "2026-03-16T12:00:00+00:00",
                "duration": 30,
                "per_kwh": 0.15,
                "spike_status": "none",
            },
            {
                "start_time": "2026-03-16T12:30:00+00:00",
                "duration": 30,
                "per_kwh": 0.85,
                "spike_status": "spike",
            },
        ]
    }
    hass.states.get.return_value = forecast_state
    
    slots = provider.read_forecasts(hass, "sensor.100h_general_price")
    
    assert len(slots) == 2
    assert slots[0].per_kwh == 0.15
    assert slots[0].is_spike is False
    assert slots[1].per_kwh == 0.85
    assert slots[1].is_spike is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest tests/pricing/test_provider.py::test_amber_provider_read_forecasts -v`
Expected: FAIL with NotImplementedError

- [ ] **Step 3: Implement AmberProvider methods**

```python
# Update custom_components/localshift/pricing/provider.py

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .types import ForecastSlot

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


class AmberProvider:
    """Amber pricing provider (original 100H integration)."""
    
    @property
    def name(self) -> str:
        return "amber"
    
    @property
    def entity_prefix(self) -> str:
        return "sensor.100h_"
    
    def read_forecasts(
        self, hass: HomeAssistant, price_entity_id: str
    ) -> list[ForecastSlot]:
        """Read forecasts from separate forecast entity."""
        forecast_entity = price_entity_id.replace("_price", "_forecast")
        raw_forecasts = self._read_attribute(hass, forecast_entity, "forecasts", [])
        
        if not raw_forecasts:
            _LOGGER.warning("No forecasts found on %s", forecast_entity)
            return []
        
        slots = []
        for raw in raw_forecasts:
            try:
                slots.append(self._normalize_slot(raw))
            except (KeyError, ValueError, TypeError) as e:
                _LOGGER.warning("Skipping malformed forecast slot: %s", e)
                continue
        return slots
    
    def is_spike(self, entry: dict[str, Any]) -> bool:
        """Check if entry represents a spike (Amber uses spike_status)."""
        return entry.get("spike_status") == "spike"
    
    def _normalize_slot(self, raw: dict[str, Any]) -> ForecastSlot:
        """Convert raw forecast dict to ForecastSlot."""
        return ForecastSlot(
            start_time=self._parse_timestamp(raw["start_time"]),
            duration=raw.get("duration", 30),
            per_kwh=raw["per_kwh"],
            is_spike=self.is_spike(raw),
            source_type="amber",
        )
    
    def _read_attribute(
        self, hass: HomeAssistant, entity_id: str, attr: str, default: Any
    ) -> Any:
        """Read an attribute from a Home Assistant entity."""
        state = hass.states.get(entity_id)
        if state is None:
            _LOGGER.debug("Entity not found: %s", entity_id)
            return default
        return state.attributes.get(attr, default)
    
    def _parse_timestamp(self, ts: str) -> datetime:
        """Parse ISO timestamp to timezone-aware datetime."""
        parsed = dt_util.parse_datetime(ts)
        if parsed is None:
            raise ValueError(f"Invalid timestamp: {ts}")
        return parsed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest tests/pricing/test_provider.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/pricing/provider.py tests/pricing/test_provider.py
git commit -m "feat(#300): implement AmberProvider with forecast reading"
```

### Task 4: Implement AmberExpressProvider

**Files:**
- Modify: `custom_components/localshift/pricing/provider.py`
- Test: `tests/pricing/test_provider.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/pricing/test_provider.py

def test_amber_express_provider_read_forecasts():
    """Test AmberExpressProvider reads from _detailed entity."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    
    from custom_components.localshift.pricing.provider import AmberExpressProvider
    
    provider = AmberExpressProvider()
    
    # Mock hass with _detailed entity
    hass = MagicMock()
    detailed_state = MagicMock()
    detailed_state.attributes = {
        "forecasts": [
            {
                "start_time": "2026-03-16T12:00:00+11:00",
                "duration": 30,
                "per_kwh": 0.20,
                "demand_window": False,
            },
            {
                "start_time": "2026-03-16T12:30:00+11:00",
                "duration": 30,
                "per_kwh": 2.50,
                "demand_window": True,
            },
        ]
    }
    hass.states.get.return_value = detailed_state
    
    slots = provider.read_forecasts(hass, "sensor.amber_express_100h_general_price")
    
    assert len(slots) == 2
    assert slots[0].per_kwh == 0.20
    assert slots[0].is_spike is False
    assert slots[1].per_kwh == 2.50
    assert slots[1].is_spike is True  # demand_window=True


def test_amber_express_entity_prefix():
    """Test AmberExpressProvider uses correct prefix."""
    from custom_components.localshift.pricing.provider import AmberExpressProvider
    
    provider = AmberExpressProvider()
    assert provider.entity_prefix == "sensor.amber_express_100h_"
    assert provider.name == "amber_express"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest tests/pricing/test_provider.py::test_amber_express_provider_read_forecasts -v`
Expected: FAIL

- [ ] **Step 3: Implement AmberExpressProvider**

```python
# Add to custom_components/localshift/pricing/provider.py after AmberProvider

class AmberExpressProvider:
    """Amber Express pricing provider."""
    
    @property
    def name(self) -> str:
        return "amber_express"
    
    @property
    def entity_prefix(self) -> str:
        return "sensor.amber_express_100h_"
    
    def read_forecasts(
        self, hass: HomeAssistant, price_entity_id: str
    ) -> list[ForecastSlot]:
        """Read forecasts from _detailed entity with fallback."""
        detailed_entity = price_entity_id.replace("_price", "_price_detailed")
        raw_forecasts = self._read_attribute(hass, detailed_entity, "forecasts", [])
        
        if not raw_forecasts:
            _LOGGER.debug(
                "%s has no forecasts, trying simple entity", detailed_entity
            )
            raw_forecasts = self._read_attribute(hass, price_entity_id, "forecast", [])
        
        if not raw_forecasts:
            _LOGGER.warning("No forecasts found for %s", price_entity_id)
            return []
        
        slots = []
        for raw in raw_forecasts:
            try:
                slots.append(self._normalize_slot(raw))
            except (KeyError, ValueError, TypeError) as e:
                _LOGGER.warning("Skipping malformed forecast slot: %s", e)
                continue
        return slots
    
    def is_spike(self, entry: dict[str, Any]) -> bool:
        """Check if entry represents a spike (Express uses demand_window)."""
        return entry.get("demand_window") is True
    
    # Reuse helper methods from AmberProvider (same implementation)
    _read_attribute = AmberProvider._read_attribute
    _normalize_slot = AmberProvider._normalize_slot
    _parse_timestamp = AmberProvider._parse_timestamp
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest tests/pricing/test_provider.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/pricing/provider.py tests/pricing/test_provider.py
git commit -m "feat(#300): implement AmberExpressProvider with demand_window spike detection"
```

### Task 5: Create factory function

**Files:**
- Create: `custom_components/localshift/pricing/__init__.py`
- Test: `tests/pricing/test_init.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pricing/test_init.py
"""Tests for pricing module factory."""
from custom_components.localshift.pricing import create_provider
from custom_components.localshift.pricing.provider import AmberProvider, AmberExpressProvider


def test_create_provider_returns_amber_by_default():
    """Test factory returns AmberProvider for unknown source."""
    provider = create_provider("unknown")
    assert isinstance(provider, AmberProvider)


def test_create_provider_returns_amber():
    """Test factory returns AmberProvider for amber source."""
    provider = create_provider("amber")
    assert isinstance(provider, AmberProvider)


def test_create_provider_returns_amber_express():
    """Test factory returns AmberExpressProvider for amber_express."""
    provider = create_provider("amber_express")
    assert isinstance(provider, AmberExpressProvider)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest tests/pricing/test_init.py -v`
Expected: FAIL

- [ ] **Step 3: Implement factory function**

```python
# custom_components/localshift/pricing/__init__.py
"""Pricing provider module.

Provides a unified interface for different pricing data sources (Amber, Amber Express).
"""
from __future__ import annotations

from .provider import AmberExpressProvider, AmberProvider, PricingProvider
from .types import ForecastSlot

__all__ = ["create_provider", "PricingProvider", "ForecastSlot", "AmberProvider", "AmberExpressProvider"]


def create_provider(source: str) -> PricingProvider:
    """Create the appropriate pricing provider based on config.
    
    Args:
        source: Pricing source identifier ("amber" or "amber_express")
        
    Returns:
        Configured pricing provider instance
    """
    if source == "amber_express":
        return AmberExpressProvider()
    return AmberProvider()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest tests/pricing/test_init.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/pricing/__init__.py tests/pricing/test_init.py
git commit -m "feat(#300): add create_provider factory function"
```

---

## Phase 2: Wire Provider Into System

### Task 6: Update CoordinatorData to store ForecastSlot

**Files:**
- Modify: `custom_components/localshift/coordinator/data.py`
- Test: Update affected tests

- [ ] **Step 1: Check current CoordinatorData structure**

Run: `cd /config/home/localshift/worktrees/issue-300 && rg "general_forecast|feed_in_forecast" custom_components/localshift/coordinator/data.py`

- [ ] **Step 2: Update type annotations in CoordinatorData**

The `general_forecast` and `feed_in_forecast` fields should change from `list[dict[str, Any]]` to `list[ForecastSlot]`.

- [ ] **Step 3: Run tests to identify breakage**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest --tb=short 2>&1 | head -100`

- [ ] **Step 4: Fix type errors**

Update any code that accesses forecast dicts to use ForecastSlot attributes.

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/coordinator/data.py
git commit -m "refactor(#300): update CoordinatorData to use ForecastSlot type"
```

### Task 7: Inject provider into StateReader

**Files:**
- Modify: `custom_components/localshift/state/reader.py`
- Modify: `custom_components/localshift/computation_engine.py`
- Test: `tests/state/test_reader.py`

- [ ] **Step 1: Write failing test for provider injection**

```python
# Add to tests/state/test_reader.py

def test_state_reader_uses_provider_for_forecasts():
    """Test StateReader delegates forecast reading to provider."""
    from unittest.mock import MagicMock, patch
    
    from custom_components.localshift.pricing.types import ForecastSlot
    from custom_components.localshift.state.reader import StateReader
    
    mock_provider = MagicMock()
    mock_provider.read_forecasts.return_value = [
        ForecastSlot(
            start_time=datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc),
            duration=30,
            per_kwh=0.15,
            is_spike=False,
            source_type="amber",
        )
    ]
    
    hass = MagicMock()
    entry = MagicMock()
    entry.data = {"pricing_data_source": "amber"}
    
    reader = StateReader(hass, entry, MagicMock(), mock_provider)
    # ... verify provider is called
```

- [ ] **Step 2: Update StateReader constructor**

```python
# In custom_components/localshift/state/reader.py

# Add import at top
from ..pricing import PricingProvider

class StateReader:
    def __init__(
        self, 
        hass: HomeAssistant, 
        entry: ConfigEntry, 
        entity_validator: EntityValidator,
        pricing_provider: PricingProvider,  # NEW
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.entity_validator = entity_validator
        self.pricing_provider = pricing_provider  # NEW
```

- [ ] **Step 3: Update ComputationEngine to create provider**

```python
# In custom_components/localshift/computation_engine.py

# Add import
from .pricing import create_provider

# In __init__ or setup:
pricing_source = entry.data.get(CONF_PRICING_DATA_SOURCE, DEFAULT_PRICING_DATA_SOURCE)
self.pricing_provider = create_provider(pricing_source)

# Pass to StateReader
self.state_reader = StateReader(hass, entry, entity_validator, self.pricing_provider)
```

- [ ] **Step 4: Run tests**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest tests/state/test_reader.py -v`

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/state/reader.py custom_components/localshift/computation_engine.py
git commit -m "refactor(#300): inject PricingProvider into StateReader"
```

### Task 8: Update StateReader to use provider for forecasts

**Files:**
- Modify: `custom_components/localshift/state/reader.py`
- Test: `tests/state/test_reader.py`

- [ ] **Step 1: Locate forecast reading code**

The forecast reading is in `read_all_external_state()` around lines 445-495.

- [ ] **Step 2: Replace provider-specific code with provider call**

```python
# In read_all_external_state(), replace the if/else block with:

# Issue #300: Use provider for forecast reading
data.general_forecast = self.pricing_provider.read_forecasts(
    self.hass, general_price_entity
)
data.feed_in_forecast = self.pricing_provider.read_forecasts(
    self.hass, feed_in_price_entity
)
```

- [ ] **Step 3: Run tests**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest tests/state/ -v`

- [ ] **Step 4: Commit**

```bash
git add custom_components/localshift/state/reader.py
git commit -m "refactor(#300): use PricingProvider for forecast reading in StateReader"
```

---

## Phase 3: Clean Up

### Task 9: Remove pricing_source parameter from utils

**Files:**
- Modify: `custom_components/localshift/engine/utils.py`
- Modify: `custom_components/localshift/engine/price_signal_engine.py`
- Modify: `custom_components/localshift/engine/spike_analyzer.py`

- [ ] **Step 1: Find all pricing_source usages in utils**

Run: `cd /config/home/localshift/worktrees/issue-300 && rg "pricing_source" custom_components/localshift/engine/`

- [ ] **Step 2: Remove _is_spike_slot function**

Since spike detection is now in ForecastSlot.is_spike, remove the `_is_spike_slot()` function from utils.py.

- [ ] **Step 3: Update callers to use ForecastSlot.is_spike**

Any code calling `_is_spike_slot(entry, pricing_source)` should now use `slot.is_spike` directly.

- [ ] **Step 4: Run tests**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest tests/engine/ -v`

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/engine/
git commit -m "refactor(#300): remove pricing_source param, use ForecastSlot.is_spike"
```

### Task 10: Fix ForecastPricesSensor field names

**Files:**
- Modify: `custom_components/localshift/sensors/forecast.py`

- [ ] **Step 1: Locate incorrect field access**

The `ForecastPricesSensor` uses `slot.get("timestamp")` and `slot.get("price")` which are wrong field names.

- [ ] **Step 2: Update to use ForecastSlot attributes**

```python
# In ForecastPricesSensor.extra_state_attributes

# Before (wrong):
for slot in d.general_forecast:
    ts = slot.get("timestamp", "")
    price = slot.get("price", "")

# After (correct):
for slot in d.general_forecast:
    ts = slot.start_time.isoformat()
    price = slot.per_kwh
```

- [ ] **Step 3: Run tests**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest tests/sensors/ -v`

- [ ] **Step 4: Commit**

```bash
git add custom_components/localshift/sensors/forecast.py
git commit -m "fix(#300): use ForecastSlot fields in ForecastPricesSensor"
```

### Task 11: Move PRICING_SOURCE constants to pricing module

**Files:**
- Modify: `custom_components/localshift/const.py`
- Modify: `custom_components/localshift/pricing/__init__.py`
- Update all imports

- [ ] **Step 1: Add constants to pricing module**

```python
# In custom_components/localshift/pricing/__init__.py

PRICING_SOURCE_AMBER = "amber"
PRICING_SOURCE_AMBER_EXPRESS = "amber_express"
```

- [ ] **Step 2: Update imports across codebase**

Run: `cd /config/home/localshift/worktrees/issue-300 && rg "from ..const import.*PRICING_SOURCE" -l`

Update each file to import from `..pricing` instead.

- [ ] **Step 3: Remove from const.py**

Remove `PRICING_SOURCE_AMBER` and `PRICING_SOURCE_AMBER_EXPRESS` from const.py.

- [ ] **Step 4: Run full test suite**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest`

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/
git commit -m "refactor(#300): move PRICING_SOURCE constants to pricing module"
```

---

## Verification

### Final Verification

- [ ] **Run full test suite**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run pytest --cov=custom_components/localshift --cov-report=term-missing`

Expected: All tests pass, coverage ≥95%

- [ ] **Run linting**

Run: `cd /config/home/localshift/worktrees/issue-300 && uv run ruff check custom_components/localshift`

Expected: No errors

- [ ] **Manual testing with Amber Express**

Deploy to HA instance and verify forecast reading works with Amber Express entities.

---

## Summary

| Phase | Tasks | Files Changed |
|-------|-------|---------------|
| 1 | 1-5 | pricing/ module (new) |
| 2 | 6-8 | StateReader, ComputationEngine, CoordinatorData |
| 3 | 9-11 | utils, sensors, const (cleanup) |

**Total commits:** ~11
**Estimated time:** 2-3 hours

## File Structure

```
custom_components/localshift/
├── pricing/                    # NEW MODULE
│   ├── __init__.py            # Factory + exports
│   ├── types.py               # ForecastSlot dataclass
│   └── provider.py            # Protocol + implementations
├── state/reader.py            # MODIFY: inject provider
├── engine/utils.py            # MODIFY: remove pricing_source param
├── config_flow/__init__.py    # MODIFY: create provider
└── sensors/forecast.py        # MODIFY: use ForecastSlot fields
```

---
