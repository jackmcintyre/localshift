# Pricing Provider Abstraction Layer

**Date:** 2026-03-16
**Issue:** #300 (Amber Express integration)
**Status:** Approved

## Problem

Amber and Amber Express have different data structures:
- **Entity IDs:** `sensor.100h_*` vs `sensor.amber_express_100h_*`
- **Forecast location:** Separate `*_forecast` entities vs embedded in `*_price_detailed`
- **Spike detection:** `spike_status == "spike"` vs `demand_window == True`
- **Timestamp format:** UTC (`:00`) vs local time (`:01` offset)

Currently, provider-specific `if pricing_source ==` checks are scattered across 9 files (66 locations), making the code:
- Hard to maintain
- Prone to bugs (e.g., `ForecastPricesSensor` uses wrong field names)
- Difficult to test in isolation

## Solution

Introduce a `PricingProvider` Protocol that centralizes all provider-specific logic and normalizes data to a canonical format.

## Architecture

```
custom_components/localshift/
├── pricing/                    # NEW: Pricing provider module
│   ├── __init__.py            # Factory function, exports
│   ├── provider.py            # Protocol + AmberProvider + AmberExpressProvider
│   └── types.py               # ForecastSlot dataclass
├── state/
│   └── reader.py              # Uses provider instance
├── engine/
│   └── utils.py               # Receives ForecastSlot (no provider checks)
└── config_flow/
    └── __init__.py            # Creates provider from config
```

## Components

### 1. Normalized Data Type

```python
# pricing/types.py
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class ForecastSlot:
    """Canonical forecast data structure used throughout LocalShift."""
    start_time: datetime
    duration: int  # minutes
    per_kwh: float
    is_spike: bool
    source_type: str  # "amber" or "amber_express" for debugging
```

### 2. Provider Protocol

```python
# pricing/provider.py
from typing import Protocol

class PricingProvider(Protocol):
    """Protocol for pricing data providers."""
    
    @property
    def name(self) -> str:
        """Provider identifier for logging/debugging."""
        ...
    
    @property
    def entity_prefix(self) -> str:
        """Return 'sensor.100h_' or 'sensor.amber_express_100h_'."""
        ...
    
    def read_forecasts(
        self, 
        hass: HomeAssistant, 
        price_entity_id: str
    ) -> list[ForecastSlot]:
        """Read and normalize forecast data from price entity."""
        ...
    
    def is_spike(self, forecast_entry: dict[str, Any]) -> bool:
        """Check if a raw forecast entry represents a price spike."""
        ...
```

### 3. Amber Provider

```python
from homeassistant.util import dt as dt_util

class AmberProvider:
    """Amber pricing provider (original 100H integration)."""
    
    name = "amber"
    
    @property
    def entity_prefix(self) -> str:
        return "sensor.100h_"
    
    def read_forecasts(self, hass: HomeAssistant, price_entity_id: str) -> list[ForecastSlot]:
        # Read from separate forecast entity
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
    
    def is_spike(self, entry: dict) -> bool:
        return entry.get("spike_status") == "spike"
    
    def _normalize_slot(self, raw: dict) -> ForecastSlot:
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

### 4. Amber Express Provider

```python
class AmberExpressProvider:
    """Amber Express pricing provider."""
    
    name = "amber_express"
    
    @property
    def entity_prefix(self) -> str:
        return "sensor.amber_express_100h_"
    
    def read_forecasts(self, hass: HomeAssistant, price_entity_id: str) -> list[ForecastSlot]:
        # Read from _detailed entity with embedded forecasts
        detailed_entity = price_entity_id.replace("_price", "_price_detailed")
        raw_forecasts = self._read_attribute(hass, detailed_entity, "forecasts", [])
        
        # Fallback to simple entity if _detailed unavailable
        if not raw_forecasts:
            _LOGGER.debug("%s has no forecasts, trying simple entity", detailed_entity)
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
    
    def is_spike(self, entry: dict) -> bool:
        return entry.get("demand_window") is True
    
    def _normalize_slot(self, raw: dict) -> ForecastSlot:
        return ForecastSlot(
            start_time=self._parse_timestamp(raw["start_time"]),
            duration=raw.get("duration", 30),
            per_kwh=raw["per_kwh"],
            is_spike=self.is_spike(raw),
            source_type="amber_express",
        )
    
    # _read_attribute and _parse_timestamp inherited from base or duplicated
    # (Both providers use identical implementations)
```

### 5. Error Handling

**Principle:** Fail gracefully, never crash the integration.

| Scenario | Behavior |
|----------|----------|
| Entity not found | Log debug, return empty list |
| Attribute missing | Log debug, use default (`[]`) |
| Malformed slot | Log warning, skip slot, continue |
| Invalid timestamp | Log warning, skip slot, continue |
| Missing required field (`per_kwh`, `start_time`) | Raise in `_normalize_slot`, caught and skipped |

**Logging levels:**
- DEBUG: Expected conditions (entity temporarily unavailable, fallback used)
- WARNING: Unexpected but recoverable (malformed data, parse failures)
- ERROR: Never (all errors are caught and handled)

### 6. Factory Function

```python
# pricing/__init__.py
from .provider import AmberProvider, AmberExpressProvider, PricingProvider
from .types import ForecastSlot

def create_provider(source: str) -> PricingProvider:
    """Create the appropriate pricing provider based on config."""
    if source == "amber_express":
        return AmberExpressProvider()
    return AmberProvider()

__all__ = ["create_provider", "PricingProvider", "ForecastSlot"]
```

## Integration Points

### StateReader

```python
# state/reader.py
class StateReader:
    def __init__(
        self, 
        hass: HomeAssistant, 
        entry: ConfigEntry, 
        entity_validator: EntityValidator,
        pricing_provider: PricingProvider  # NEW
    ) -> None:
        self.pricing_provider = pricing_provider
    
    def read_all_external_state(self, data: CoordinatorData) -> None:
        # Before: 30+ lines of if/else
        # After:
        data.general_forecast = self.pricing_provider.read_forecasts(
            self.hass, general_price_entity
        )
        data.feed_in_forecast = self.pricing_provider.read_forecasts(
            self.hass, feed_in_price_entity
        )
```

### Config Flow

```python
# config_flow/__init__.py
from ..pricing import create_provider

# In async_setup_entry or coordinator creation:
pricing_source = entry.data.get(CONF_PRICING_DATA_SOURCE, "amber")
pricing_provider = create_provider(pricing_source)

# Pass to StateReader
state_reader = StateReader(hass, entry, entity_validator, pricing_provider)
```

### Utils (Simplified)

```python
# engine/utils.py

# Before:
def _is_spike_slot(forecast_entry: dict, pricing_source: str) -> bool:
    if pricing_source == PRICING_SOURCE_AMBER_EXPRESS:
        return forecast_entry.get("demand_window") is True
    return forecast_entry.get("spike_status") == "spike"

# After: Spike detection is pre-computed in ForecastSlot.is_spike
# No provider checks needed in utils
```

### ForecastPricesSensor (Bug Fix)

```python
# sensors/forecast.py

# Before (WRONG field names):
for slot in d.general_forecast:
    ts = slot.get("timestamp", "")  # Wrong!
    price = slot.get("price", "")   # Wrong!

# After (using ForecastSlot):
for slot in d.general_forecast:  # slot is ForecastSlot
    ts = slot.start_time.isoformat()
    price = slot.per_kwh
```

## Data Flow

```
┌─────────────────┐
│   Config Flow   │
│  (user selects  │
│   amber/express)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│ create_provider │────▶│ AmberProvider   │
│                 │     │ AmberExpress    │
└────────┬────────┘     └────────┬────────┘
         │                       │
         ▼                       ▼
┌─────────────────────────────────────────┐
│            StateReader                   │
│  provider.read_forecasts(hass, entity)  │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│         list[ForecastSlot]              │
│  (normalized: start_time, per_kwh,      │
│   duration, is_spike)                   │
└────────────────┬────────────────────────┘
                 │
         ┌───────┴───────┐
         ▼               ▼
┌─────────────┐  ┌─────────────┐
│   Utils     │  │  Sensors    │
│ (no checks) │  │ (no checks) │
└─────────────┘  └─────────────┘
```

## Migration Plan

### Phase 1: Add Provider Module (No Behavior Change)
1. Create `pricing/types.py` with `ForecastSlot`
2. Create `pricing/provider.py` with Protocol and both implementations
3. Create `pricing/__init__.py` with factory
4. Add unit tests for each provider

### Phase 2: Wire Provider Into System
1. Config flow creates provider, passes to coordinator
2. StateReader accepts provider, uses for forecast reading
3. CoordinatorData stores `list[ForecastSlot]` instead of raw dicts

### Phase 3: Clean Up
1. Remove `pricing_source` parameter from utils functions
2. Remove scattered `if pricing_source ==` checks
3. Fix `ForecastPricesSensor` to use `ForecastSlot` fields
4. Move `PRICING_SOURCE_*` constants to `pricing/__init__.py`

## Files Changed

| File | Change |
|------|--------|
| `pricing/__init__.py` | NEW - factory, exports |
| `pricing/provider.py` | NEW - Protocol + implementations |
| `pricing/types.py` | NEW - ForecastSlot |
| `state/reader.py` | Inject provider, simplify forecast reading |
| `engine/utils.py` | Remove pricing_source param |
| `engine/price_signal_engine.py` | Remove provider checks |
| `engine/spike_analyzer.py` | Remove provider checks |
| `config_flow/__init__.py` | Create provider from config |
| `coordinator/data.py` | Store `list[ForecastSlot]` |
| `sensors/forecast.py` | Use ForecastSlot fields |
| `const.py` | Move PRICING_SOURCE_* to pricing/ |

## Test Strategy

1. **Unit tests:** Each provider's `read_forecasts()` and `is_spike()` methods
2. **Normalization tests:** Verify both providers produce identical `ForecastSlot` for equivalent input data
3. **Integration tests:** StateReader with each provider
4. **Regression:** All existing tests pass (behavior preserved)

## Benefits

1. **Single source of truth:** All provider logic in `pricing/provider.py` (~200 lines)
2. **Easier testing:** Mock the Protocol, test providers in isolation
3. **Bug prevention:** `ForecastSlot` guarantees correct field names
4. **Maintainability:** Adding/modifying providers touches one file
5. **Clean consumers:** Utils, sensors, state machine have no provider-specific code
