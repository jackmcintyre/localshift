# LocalShift Integration

## Structure

```
custom_components/localshift/
├── __init__.py           # Entry point
├── const.py              # PLATFORMS, constants, entity IDs
├── sensor.py             # 27 sensors
├── binary_sensor.py      # 10 binary sensors
├── switch.py            # 8 switches
├── number.py            # 4 numbers
├── select.py            # 2 selects
├── button.py            # 2 buttons
├── coordinator/         # Data coordinator
├── state/               # State machine
├── engine/              # DP optimizer
├── forecast/            # Forecast computation
└── utils/               # Shared utilities
```

## Conventions

- Python 3.13+ with `from __future__ import annotations`
- Type hints required on all public APIs
- Async/await for HA-interfacing code
- Entities read from coordinator, don't compute

## Entity Pattern

```python
class LocalShiftSensor(SensorEntity):
    def __init__(self, coordinator, key):
        self.coordinator = coordinator
        self._key = key

    @property
    def native_value(self):
        return self.coordinator.data.get(self._key)
```

## Critical Rules

1. Update `docs/ENTITY_REFERENCE.md` when adding/removing entities
2. Update `docs/ARCHITECTURE.md` when changing architecture
3. All platforms async - use `async_forward_entry_setups()`
4. Coordinator owns state - entities read only

## See Also

- `../AGENTS.md` - Root rules (includes doc-first skill)
- `engine/AGENTS.md` - Optimizer rules
- `../../tests/AGENTS.md` - Testing
- `../../docs/INDEX.md` - **Primary documentation index (use this first!)**
- `../../docs/PLANNING_MODEL.md` - Optimizer constraints (legacy, see INDEX.md)
- `../../docs/ENTITY_REFERENCE.md` - Entity catalog
- `../../docs/ARCHITECTURE.md` - System architecture
