# LocalShift Integration - Agent Guidelines

## Overview

Home Assistant integration for automated Tesla Powerwall battery control. 53 entities (27 sensors, 10 binary sensors, 8 switches, 4 numbers, 2 selects, 2 buttons).

## Entry Points

| File | Purpose |
|------|---------|
| `__init__.py` | Integration setup/teardown, forwards to platforms |
| `const.py` | PLATFORMS list, constants, default entity IDs |
| `manifest.json` | HA integration metadata (domain: localshift) |
| `config_flow/` | Configuration flow UI |

## Platform Structure

```
custom_components/localshift/
├── __init__.py              # Integration entry
├── const.py                 # Constants, PLATFORMS
├── manifest.json            # Integration metadata
├── sensor.py                # 27 sensors
├── binary_sensor.py         # 10 binary sensors
├── switch.py                # 8 switches
├── number.py                # 4 numbers
├── select.py                # 2 selects
├── button.py                # 2 buttons
├── coordinator/             # Data coordinator
├── state/                   # State machine
├── services/                # Service handlers
├── engine/                  # DP optimizer (see engine/AGENTS.md)
├── forecast/                # Forecast computation
├── learning/                # Learning system
└── utils/                   # Shared utilities
```

## Where to Look

| Task | Location |
|------|----------|
| Add new entity | Create platform file (sensor.py, etc.) |
| Modify state machine | `state/` directory |
| Change optimization | `engine/` directory |
| Update config flow | `config_flow/` directory |
| Add service | `services/` directory |
| Entity constants | `const.py` |

## Conventions

- **Python 3.13+** with `from __future__ import annotations`
- **Type hints** required for all public APIs
- **Async/await** for all HA-interfacing code
- **Entity IDs**: Use constants from `const.py` (DEFAULT_ENTITY_IDS)
- **Logging**: Use `_LOGGER = logging.getLogger(__name__)`
- **Error handling**: Graceful degradation, entity health tracking

## Entity Pattern

```python
class LocalShiftSensor(SensorEntity):
    """Representation of a LocalShift sensor."""

    def __init__(self, coordinator, key):
        self.coordinator = coordinator
        self._key = key

    @property
    def native_value(self):
        return self.coordinator.data.get(self._key)
```

## Critical Rules

1. **MUST update `docs/ENTITY_REFERENCE.md`** when adding/removing entities
2. **MUST update `docs/ARCHITECTURE.md`** when changing system architecture
3. **All platforms async** - use `async_forward_entry_setups()`
4. **Coordinator owns state** - entities read from coordinator, don't compute
5. **Entity health tracking** - mark entities as unavailable when source entities fail

## Testing

- Test file: `tests/test_<platform>.py`
- Use fixtures from `tests/conftest.py`
- Mock HA with `mock_hass_with_states` fixture

## See Also

- `../docs/ARCHITECTURE.md` - System architecture
- `../docs/ENTITY_REFERENCE.md` - Entity definitions
- `../docs/PLANNING_MODEL.md` - Optimizer guide (CRITICAL for engine changes)
- `../tests/AGENTS.md` - Testing patterns
