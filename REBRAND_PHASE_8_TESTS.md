# Phase 8: Test Files

**Phase:** 8 of 13  
**Status:** NOT STARTED  
**Estimated Time:** 15 minutes

---

## Overview

This phase updates all test files to use the new import paths and class names after the directory rename and class renaming.

**Files to Update:**
- `tests/__init__.py`
- `tests/conftest.py`
- `tests/test_computation_engine.py`
- `tests/test_coordinator.py`
- `tests/test_forecast_computer.py`
- `tests/test_integration.py`

---

## 8.1 tests/__init__.py

**File:** `tests/__init__.py`

### Check for Import Paths

```python
# SEARCH for any occurrence of:
custom_components.amber_powerwall

# REPLACE with:
custom_components.localshift
```

### Checklist
- [ ] Checked for old import paths
- [ ] Updated any found
- [ ] Saved file

---

## 8.2 tests/conftest.py

**File:** `tests/conftest.py`

### Import Path Updates

```python
# SEARCH
from custom_components.amber_powerwall.

# REPLACE
from custom_components.localshift.
```

### Class Name Updates

```python
# SEARCH
AmberPowerwallCoordinator

# REPLACE
LocalShiftCoordinator
```

### Checklist
- [ ] Import paths updated
- [ ] Class names updated
- [ ] Saved file

---

## 8.3 tests/test_computation_engine.py

**File:** `tests/test_computation_engine.py`

### Import Path Updates

```python
# SEARCH
from custom_components.amber_powerwall.computation_engine import (
    BatteryMode,
    ...
)

# REPLACE
from custom_components.localshift.computation_engine import (
    BatteryMode,
    ...
)
```

### Any Other Amber References

Search for:
- `amber_powerwall` in import statements
- `AmberPowerwall` in class names
- `Amber Powerwall` in strings

### Checklist
- [ ] Import paths updated
- [ ] Class names updated (if any)
- [ ] String references updated (if any)
- [ ] Saved file

---

## 8.4 tests/test_coordinator.py

**File:** `tests/test_coordinator.py`

### Import Path Updates

```python
# SEARCH
from custom_components.amber_powerwall.coordinator import AmberPowerwallCoordinator

# REPLACE
from custom_components.localshift.coordinator import LocalShiftCoordinator
```

### Class Name Updates in Tests

```python
# SEARCH for usage in test functions
AmberPowerwallCoordinator

# REPLACE
LocalShiftCoordinator
```

### Checklist
- [ ] Import paths updated
- [ ] Class names updated in test code
- [ ] Saved file

---

## 8.5 tests/test_forecast_computer.py

**File:** `tests/test_forecast_computer.py`

### Import Path Updates

```python
# SEARCH
from custom_components.amber_powerwall.computation_engine_lib.forecast_computer import

# REPLACE
from custom_components.localshift.computation_engine_lib.forecast_computer import
```

### Checklist
- [ ] Import paths updated
- [ ] Saved file

---

## 8.6 tests/test_integration.py

**File:** `tests/test_integration.py`

### Import Path Updates

```python
# SEARCH
from custom_components.amber_powerwall.

# REPLACE
from custom_components.localshift.
```

### Module Docstring Update

```python
# SEARCH
"""Integration tests for amber_powerwall component."""

# REPLACE
"""Integration tests for localshift component."""
```

### Checklist
- [ ] Import paths updated
- [ ] Module docstring updated
- [ ] Saved file

---

## 8.7 Verification Commands

### Find All Old Import Paths

```bash
# Check for remaining old imports
grep -rn "from custom_components.amber_powerwall" tests/

# Should return 0 results
```

### Find All Old Class Names

```bash
# Check for remaining old class names
grep -rn "AmberPowerwallCoordinator\|AmberPowerwallSensor\|AmberPowerwallBinary" tests/

# Should return 0 results
```

### Run Tests

```bash
# Run all tests to verify imports work
pytest tests/ -v

# All tests should pass (or at least not fail on imports)
```

---

## Phase 8 Completion Checklist

### Files Updated
- [ ] tests/__init__.py
- [ ] tests/conftest.py
- [ ] tests/test_computation_engine.py
- [ ] tests/test_coordinator.py
- [ ] tests/test_forecast_computer.py
- [ ] tests/test_integration.py

### Verification
- [ ] No old import paths remain (`grep` verified)
- [ ] No old class names remain (`grep` verified)
- [ ] Tests can import modules without errors
- [ ] Pre-commit passes

---

**Phase Status:** ☐ NOT STARTED | ☐ IN PROGRESS | ☐ COMPLETED