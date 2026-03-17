---
name: entity-addition
description: Use when adding new entities to the LocalShift integration - sensors, switches, numbers, selects, or buttons
---

# Entity Addition

## Overview

Adding entities to LocalShift follows a consistent pattern. The key steps:

1. Create the entity class in the appropriate `sensors/` module
2. Add to platform file (`sensor.py`, `switch.py`, etc.)
3. Add to coordinator data if it needs computed values
4. Update `docs/ENTITY_REFERENCE.md`
5. Write tests

## Entity Types

| Type | Platform File | Location |
|------|--------------|----------|
| Sensor (30) | `sensor.py` | `sensors/` directory |
| Binary Sensor (10) | `binary_sensor.py` | `sensors/` directory |
| Switch (8) | `switch.py` | Root or sensors/ |
| Number (4) | `number.py` | Root or sensors/ |
| Select (2) | `select.py` | Root or sensors/ |
| Button (2) | `button.py` | Root or sensors/ |

## Step 1: Create Entity Class

### Base Pattern

All entities extend `LocalShiftSensorBase`:

```python
from __future__ import annotations
from homeassistant.components.sensor import SensorEntity
from .base import LocalShiftSensorBase

class MyNewSensor(LocalShiftSensorBase, SensorEntity):
    """Description of what this sensor does."""
    
    def _update_from_coordinator(self) -> None:
        self._attr_native_value = self.coordinator.data.get("my_new_key")
```

### For Binary Sensors

```python
from homeassistant.components.binary_sensor import BinarySensorEntity

class MyNewBinarySensor(LocalShiftSensorBase, BinarySensorEntity):
    """Description."""
    
    def _update_from_coordinator(self) -> None:
        self._attr_is_on = self.coordinator.data.get("my_new_key")
```

### For Switches/Numbers/Selects

```python
from homeassistant.components.switch import SwitchEntity

class MyNewSwitch(LocalShiftSensorBase, SwitchEntity):
    """Description."""
    
    @property
    def is_on(self) -> bool:
        return self.coordinator.data.get("my_new_key", False)
    
    async def async_turn_on(self) -> None:
        # Update coordinator data
        self.coordinator.data["my_new_key"] = True
        # Trigger update
        await self.coordinator.async_request_refresh()
```

## Step 2: Add to Platform File

### For Sensors (`sensor.py`)

```python
# Import at top
from .sensors import (
    # ... existing imports ...
    MyNewSensor,
)

# In async_setup_entry
async def async_setup_entry(hass, entry, async_add_entities):
    # ... existing setup ...
    entities.append(MyNewSensor(coordinator, entry))
    async_add_entities(entities)
```

### For Other Types

Similar pattern in `switch.py`, `number.py`, `select.py`, `button.py`.

## Step 3: Add to Coordinator Data

If the entity needs computed data (not just reading from coordinator):

### Option A: Add to CoordinatorData (`coordinator/data.py`)

```python
@dataclass
class CoordinatorData:
    # ... existing fields ...
    my_new_value: float | None = None
    
    def to_dict(self) -> dict:
        return {
            # ... existing ...
            "my_new_key": self.my_new_value,
        }
```

### Option B: Add computed property

```python
@property
def my_new_computed(self) -> float:
    # Compute from existing data
    return self.solar_power + self.battery_power
```

## Step 4: Update Documentation

Add to `docs/ENTITY_REFERENCE.md`:

```markdown
### N. sensor.localshift_my_new

**Purpose:** Description of what this sensor provides.

**State:** Current value and unit

**Example Data:**
```
State: 1.23
```

**Calculation:** How the value is computed.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `attr_name` | type | Description |
```

## Step 5: Write Tests

### Unit Test Pattern

```python
class TestMyNewSensor:
    def test_native_value(self):
        # Create coordinator with mock data
        coordinator = MockCoordinator()
        coordinator.data["my_new_key"] = 1.23
        
        # Create sensor
        sensor = MyNewSensor(coordinator, MockEntry())
        
        # Assert
        assert sensor.native_value == 1.23
```

### Integration Test Pattern

```python
async def test_my_new_sensor(hass: HomeAssistant, setup_local_shift):
    """Test my new sensor works."""
    # Get entity
    entity = hass.states.get("sensor.localshift_my_new")
    
    # Assert
    assert entity is not None
    assert entity.state == "1.23"
```

## Quick Reference

| Task | File to Edit |
|------|--------------|
| Create sensor | `sensors/*.py` (choose appropriate module) |
| Register sensor | `sensor.py` |
| Add computed data | `coordinator/data.py` |
| Document entity | `docs/ENTITY_REFERENCE.md` |
| Add test | `tests/test_*.py` |

## Common Mistakes

### Mistake 1: Computing in entity instead of coordinator

```python
# BAD: Entity computes its own value
def _update_from_coordinator(self) -> None:
    # Expensive computation in entity!
    self._attr_native_value = expensive_compute()
```

**Fix:** Compute in coordinator, entity just reads:
```python
# GOOD: Entity reads pre-computed value
def _update_from_coordinator(self) -> None:
    self._attr_native_value = self.coordinator.data.get("my_new_key")
```

### Mistake 2: Forgetting to add to coordinator.to_dict()

```python
# BAD: Value available in coordinator but not exposed
def to_dict(self) -> dict:
    return {
        "existing_key": self.existing_value,
        # Forgot my_new_key!
    }
```

**Fix:** Always add to to_dict():
```python
def to_dict(self) -> dict:
    return {
        "existing_key": self.existing_value,
        "my_new_key": self.my_new_value,  # Added
    }
```

### Mistake 3: Not updating ENTITY_REFERENCE.md

```python
# BAD: Entity added but not documented
# Other developers can't find it!
```

**Fix:** Always document new entities.

## Entity Counts (Current)

| Type | Count | Max before review |
|------|-------|-------------------|
| Sensors | 30 | 35 |
| Binary Sensors | 10 | 12 |
| Switches | 8 | 10 |
| Numbers | 4 | 6 |
| Selects | 2 | 4 |
| Buttons | 2 | 4 |

If exceeding limits, consider splitting into separate integrations.

## See Also

- `custom_components/localshift/AGENTS.md` - Integration rules
- `docs/ENTITY_REFERENCE.md` - Entity catalog
- `tests/AGENTS.md` - Testing patterns
