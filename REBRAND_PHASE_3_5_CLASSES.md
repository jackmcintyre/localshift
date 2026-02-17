# Phase 3.5: Class Renaming

**Phase:** 3.5 of 13  
**Status:** NOT STARTED  
**Estimated Time:** 20 minutes

---

## Overview

This phase renames all classes that contain "Amber" or "AmberPowerwall" in their names to use "LocalShift" naming convention.

**Total Classes to Rename:** 6 base/primary classes
- 2 in sensor.py
- 2 in binary_sensor.py
- 1 in button.py
- 1 in number.py
- 1 in switch.py
- 1 in coordinator.py

---

## 3.5.1 sensor.py Classes

**File:** `custom_components/localshift/sensor.py`

### Base Class Rename

```python
# SEARCH
class AmberPowerwallSensorBase(SensorEntity):
    """Base class for Amber Powerwall sensors."""

# REPLACE
class LocalShiftSensorBase(SensorEntity):
    """Base class for LocalShift sensors."""
```

### Update All Subclass References

Each sensor class extends the base class. The Phase 3 plan already includes updating these, but verify:

```python
# All sensor classes should extend LocalShiftSensorBase
class EffectiveCheapPriceSensor(LocalShiftSensorBase):
class CheapChargeStopPriceSensor(LocalShiftSensorBase):
class SolarWeightedAvgFITSensor(LocalShiftSensorBase):
class ActiveModeSensor(LocalShiftSensorBase):
class SolarBatteryForecastSensor(LocalShiftSensorBase):
class GridImportPowerSensor(LocalShiftSensorBase):
class GridExportPowerSensor(LocalShiftSensorBase):
class NetElectricityCostSensor(LocalShiftSensorBase):
class DecisionLogSensor(LocalShiftSensorBase):
class ForecastHistorySensor(LocalShiftSensorBase):
class DailyForecastSensor(LocalShiftSensorBase):
class MinimumTargetSOCSensor(LocalShiftSensorBase):
```

### Device Info Update

```python
# SEARCH (in LocalShiftSensorBase.device_info)
return DeviceInfo(
    identifiers={(DOMAIN, self._entry.entry_id)},
    name="Amber Powerwall",
    manufacturer="Custom",
    model="Solar Battery Automation",
    sw_version="0.1.0",
)

# REPLACE
return DeviceInfo(
    identifiers={(DOMAIN, self._entry.entry_id)},
    name="LocalShift",
    manufacturer="Custom",
    model="Solar Battery Automation",
    sw_version="0.0.2",
)
```

### Checklist for sensor.py
- [ ] Base class renamed: `AmberPowerwallSensorBase` → `LocalShiftSensorBase`
- [ ] All subclass references updated
- [ ] Device info name updated to "LocalShift"
- [ ] Device info version updated to "0.0.2"
- [ ] Saved file

---

## 3.5.2 binary_sensor.py Classes

**File:** `custom_components/localshift/binary_sensor.py`

### Base Class Rename

```python
# SEARCH
class AmberPowerwallBinarySensorBase(BinarySensorEntity):
    """Base class for Amber Powerwall binary sensors."""

# REPLACE
class LocalShiftBinarySensorBase(BinarySensorEntity):
    """Base class for LocalShift binary sensors."""
```

### Update All Subclass References

```python
# All binary sensor classes should extend LocalShiftBinarySensorBase
class ForecastSpikeWithinWindowSensor(LocalShiftBinarySensorBase):
class ForceDischargeActiveSensor(LocalShiftBinarySensorBase):
class ForceChargeActiveSensor(LocalShiftBinarySensorBase):
class BoostChargeActiveSensor(LocalShiftBinarySensorBase):
class ForecastExpensivePeriodSensor(LocalShiftBinarySensorBase):
class SolarCanReachTargetSensor(LocalShiftBinarySensorBase):
class BoostChargeNeededSensor(LocalShiftBinarySensorBase):
class DemandWindowActiveSensor(LocalShiftBinarySensorBase):
```

### Device Info Update

```python
# SEARCH (in LocalShiftBinarySensorBase.device_info)
return DeviceInfo(
    identifiers={(DOMAIN, self._entry.entry_id)},
    name="Amber Powerwall",
    manufacturer="Custom",
    model="Solar Battery Automation",
    sw_version="0.1.0",
)

# REPLACE
return DeviceInfo(
    identifiers={(DOMAIN, self._entry.entry_id)},
    name="LocalShift",
    manufacturer="Custom",
    model="Solar Battery Automation",
    sw_version="0.0.2",
)
```

### Checklist for binary_sensor.py
- [ ] Base class renamed: `AmberPowerwallBinarySensorBase` → `LocalShiftBinarySensorBase`
- [ ] All subclass references updated
- [ ] Device info name updated to "LocalShift"
- [ ] Device info version updated to "0.0.2"
- [ ] Saved file

---

## 3.5.3 button.py Classes

**File:** `custom_components/localshift/button.py`

### Base Class Rename

```python
# SEARCH
class AmberPowerwallButtonBase(ButtonEntity):
    """Base class for manual mode buttons."""

# REPLACE
class LocalShiftButtonBase(ButtonEntity):
    """Base class for manual mode buttons."""
```

### Update All Subclass References

```python
# All button classes should extend LocalShiftButtonBase
class ForceChargeButton(LocalShiftButtonBase):
class ForceDischargeButton(LocalShiftButtonBase):
class BoostChargeButton(LocalShiftButtonBase):
class SelfConsumptionButton(LocalShiftButtonBase):
class UpdateForecastButton(LocalShiftButtonBase):
```

### Device Info Update

```python
# SEARCH (in LocalShiftButtonBase.device_info)
return DeviceInfo(
    identifiers={(DOMAIN, self._entry.entry_id)},
    name="Amber Powerwall",
    manufacturer="Custom",
    model="Solar Battery Automation",
    sw_version="0.1.0",
)

# REPLACE
return DeviceInfo(
    identifiers={(DOMAIN, self._entry.entry_id)},
    name="LocalShift",
    manufacturer="Custom",
    model="Solar Battery Automation",
    sw_version="0.0.2",
)
```

### Checklist for button.py
- [ ] Base class renamed: `AmberPowerwallButtonBase` → `LocalShiftButtonBase`
- [ ] All subclass references updated
- [ ] Device info name updated to "LocalShift"
- [ ] Device info version updated to "0.0.2"
- [ ] Saved file

---

## 3.5.4 number.py Classes

**File:** `custom_components/localshift/number.py`

### Class Rename

```python
# SEARCH
class AmberPowerwallNumber(NumberEntity):
    """A user-configurable threshold backed by config entry options."""

# REPLACE
class LocalShiftNumber(NumberEntity):
    """A user-configurable threshold backed by config entry options."""
```

### Device Info Update

```python
# SEARCH (in LocalShiftNumber.device_info)
return DeviceInfo(
    identifiers={(DOMAIN, self._entry.entry_id)},
    name="Amber Powerwall",
    manufacturer="Custom",
    model="Solar Battery Automation",
    sw_version="0.1.0",
)

# REPLACE
return DeviceInfo(
    identifiers={(DOMAIN, self._entry.entry_id)},
    name="LocalShift",
    manufacturer="Custom",
    model="Solar Battery Automation",
    sw_version="0.0.2",
)
```

### Checklist for number.py
- [ ] Class renamed: `AmberPowerwallNumber` → `LocalShiftNumber`
- [ ] Device info name updated to "LocalShift"
- [ ] Device info version updated to "0.0.2"
- [ ] Saved file

---

## 3.5.5 switch.py Classes

**File:** `custom_components/localshift/switch.py`

### Class Rename

```python
# SEARCH
class AmberPowerwallSwitch(SwitchEntity):
    """A toggle switch for automation features."""

# REPLACE
class LocalShiftSwitch(SwitchEntity):
    """A toggle switch for automation features."""
```

### Device Info Update

```python
# SEARCH (in LocalShiftSwitch.device_info)
return DeviceInfo(
    identifiers={(DOMAIN, self._entry.entry_id)},
    name="Amber Powerwall",
    manufacturer="Custom",
    model="Solar Battery Automation",
    sw_version="0.1.0",
)

# REPLACE
return DeviceInfo(
    identifiers={(DOMAIN, self._entry.entry_id)},
    name="LocalShift",
    manufacturer="Custom",
    model="Solar Battery Automation",
    sw_version="0.0.2",
)
```

### Checklist for switch.py
- [ ] Class renamed: `AmberPowerwallSwitch` → `LocalShiftSwitch`
- [ ] Device info name updated to "LocalShift"
- [ ] Device info version updated to "0.0.2"
- [ ] Saved file

---

## 3.5.6 coordinator.py Class

**File:** `custom_components/localshift/coordinator.py`

### Main Class Rename

```python
# SEARCH
class AmberPowerwallCoordinator:
    """Central coordinator: reads external entities, computes state, drives battery.

    This is NOT a DataUpdateCoordinator (we don't poll an API). Instead we
    subscribe to HA entity state changes and run a periodic 1-minute tick.
    """

# REPLACE
class LocalShiftCoordinator:
    """Central coordinator: reads external entities, computes state, drives battery.

    This is NOT a DataUpdateCoordinator (we don't poll an API). Instead we
    subscribe to HA entity state changes and run a periodic 1-minute tick.
    """
```

### Update All Type Hints and References

Search for all occurrences of `AmberPowerwallCoordinator` and replace with `LocalShiftCoordinator`:

```python
# In __init__.py
coordinator: LocalShiftCoordinator = entry.runtime_data

# In all entity files
from .coordinator import LocalShiftCoordinator

# In type hints
coordinator: LocalShiftCoordinator
```

### Checklist for coordinator.py
- [ ] Class renamed: `AmberPowerwallCoordinator` → `LocalShiftCoordinator`
- [ ] All internal references updated
- [ ] Saved file

---

## 3.5.7 Update All Import Statements

After renaming classes, all files that import them need updates:

### Files to Update

| File | Import Change |
|------|---------------|
| `sensor.py` | `from .coordinator import LocalShiftCoordinator` |
| `binary_sensor.py` | `from .coordinator import LocalShiftCoordinator` |
| `button.py` | `from .coordinator import LocalShiftCoordinator` |
| `number.py` | `from .coordinator import LocalShiftCoordinator` |
| `switch.py` | `from .coordinator import LocalShiftCoordinator` |
| `__init__.py` | `coordinator: LocalShiftCoordinator` |
| `config_flow.py` | If applicable |

### Verification Command

```bash
# Check for remaining old class references
grep -rn "AmberPowerwallCoordinator\|AmberPowerwallSensor\|AmberPowerwallBinary\|AmberPowerwallButton\|AmberPowerwallNumber\|AmberPowerwallSwitch" custom_components/localshift/

# Should return 0 results
```

---

## Phase 3.5 Completion Checklist

### Classes Renamed
- [ ] `AmberPowerwallSensorBase` → `LocalShiftSensorBase` (sensor.py)
- [ ] `AmberPowerwallBinarySensorBase` → `LocalShiftBinarySensorBase` (binary_sensor.py)
- [ ] `AmberPowerwallButtonBase` → `LocalShiftButtonBase` (button.py)
- [ ] `AmberPowerwallNumber` → `LocalShiftNumber` (number.py)
- [ ] `AmberPowerwallSwitch` → `LocalShiftSwitch` (switch.py)
- [ ] `AmberPowerwallCoordinator` → `LocalShiftCoordinator` (coordinator.py)

### Device Info Updated (all 5 entity files)
- [ ] name: "Amber Powerwall" → "LocalShift"
- [ ] sw_version: "0.1.0" → "0.0.2"

### Imports Updated
- [ ] All coordinator imports use new name
- [ ] All type hints updated

### Verification
- [ ] No old class names remain in codebase
- [ ] Pre-commit passes

---

**Phase Status:** ✅ COMPLETED