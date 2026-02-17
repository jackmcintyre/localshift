# Phase 4: Python Source Files (Import Updates)

**Phase:** 4 of 11  
**Status:** NOT STARTED  
**Estimated Time:** 30 minutes

---

## Overview

This phase updates all Python import statements and component name references after the directory rename in Phase 2.

**Total Files:** 21 Python files
- 16 files in `custom_components/localshift/`
- 5 files in `custom_components/localshift/computation_engine_lib/`

**Change Pattern:** `custom_components.amber_powerwall` → `custom_components.localshift`

**Note:** This phase assumes Phase 2 (directory rename) is complete.

---

## Import Update Strategy

### Standard Pattern

All imports follow this pattern:

```python
# OLD
from custom_components.amber_powerwall.const import DOMAIN

# NEW
from custom_components.localshift.const import DOMAIN
```

### Verification After Each File

```bash
# Check for remaining old imports
grep -n "amber_powerwall" <filename>

# Should only find references in comments or strings (if any)
```

---

## 4.1 __init__.py

**File:** `custom_components/localshift/__init__.py`

### Expected Imports

```python
from custom_components.localshift.const import (
    DOMAIN,
    PLATFORMS,
)
```

### Changes Required

**SEARCH:**
```python
from custom_components.amber_powerwall.const import (
```

**REPLACE:**
```python
from custom_components.localshift.const import (
```

### Additional Updates

Check for any log messages that reference the component name:
- `"Amber Powerwall"` → `"LocalShift"`
- References to domain in log messages should use `DOMAIN` constant

### Checklist
- [ ] Import statement updated
- [ ] No references to `amber_powerwall` in imports
- [ ] Log messages checked (if any)
- [ ] File saved

---

## 4.2 coordinator.py

**File:** `custom_components/localshift/coordinator.py`

### Expected Imports

```python
from custom_components.localshift.const import (
    DOMAIN,
    CONF_AMBER_GENERAL_PRICE,
    CONF_AMBER_FEED_IN_PRICE,
    # ... other imports
)
from custom_components.localshift.coordinator_data import AmberPowerwallData
from custom_components.localshift.computation_engine import ComputationEngine
from custom_components.localshift.state_machine import StateMachine
from custom_components.localshift.battery_controller import BatteryController
from custom_components.localshift.cost_tracker import CostTracker
from custom_components.localshift.notification_service import NotificationService
```

### Changes Required

Multiple import statements to update:

**SEARCH/REPLACE Block 1:**
```python
------- SEARCH
from custom_components.amber_powerwall.const import (
=======
from custom_components.localshift.const import (
+++++++ REPLACE
```

**SEARCH/REPLACE Block 2:**
```python
------- SEARCH
from custom_components.amber_powerwall.coordinator_data import AmberPowerwallData
=======
from custom_components.localshift.coordinator_data import AmberPowerwallData
+++++++ REPLACE
```

**SEARCH/REPLACE Block 3:**
```python
------- SEARCH
from custom_components.amber_powerwall.computation_engine import ComputationEngine
=======
from custom_components.localshift.computation_engine import ComputationEngine
+++++++ REPLACE
```

**SEARCH/REPLACE Block 4:**
```python
------- SEARCH
from custom_components.amber_powerwall.state_machine import StateMachine
=======
from custom_components.localshift.state_machine import StateMachine
+++++++ REPLACE
```

**SEARCH/REPLACE Block 5:**
```python
------- SEARCH
from custom_components.amber_powerwall.battery_controller import BatteryController
=======
from custom_components.localshift.battery_controller import BatteryController
+++++++ REPLACE
```

**SEARCH/REPLACE Block 6:**
```python
------- SEARCH
from custom_components.amber_powerwall.cost_tracker import CostTracker
=======
from custom_components.localshift.cost_tracker import CostTracker
+++++++ REPLACE
```

**SEARCH/REPLACE Block 7:**
```python
------- SEARCH
from custom_components.amber_powerwall.notification_service import NotificationService
=======
from custom_components.localshift.notification_service import NotificationService
+++++++ REPLACE
```

### Log Message Updates

Check for coordinator startup messages:

**SEARCH:**
```python
"Amber Powerwall coordinator started"
```

**REPLACE:**
```python
"LocalShift coordinator started"
```

### Checklist
- [ ] All 7 import statements updated
- [ ] Log messages updated
- [ ] No `amber_powerwall` references in imports
- [ ] File saved

---

## 4.3 sensor.py

**File:** `custom_components/localshift/sensor.py`

### Expected Imports

```python
from custom_components.localshift.const import DOMAIN
from custom_components.localshift.coordinator import AmberPowerwallCoordinator
```

### Changes Required

**SEARCH/REPLACE Block 1:**
```python
------- SEARCH
from custom_components.amber_powerwall.const import DOMAIN
=======
from custom_components.localshift.const import DOMAIN
+++++++ REPLACE
```

**SEARCH/REPLACE Block 2:**
```python
------- SEARCH
from custom_components.amber_powerwall.coordinator import AmberPowerwallCoordinator
=======
from custom_components.localshift.coordinator import AmberPowerwallCoordinator
+++++++ REPLACE
```

### Checklist
- [ ] Import statements updated (2)
- [ ] File saved

---

## 4.4 binary_sensor.py

**File:** `custom_components/localshift/binary_sensor.py`

### Expected Imports

```python
from custom_components.localshift.const import DOMAIN
from custom_components.localshift.coordinator import AmberPowerwallCoordinator
```

### Changes Required

Same pattern as sensor.py:

**SEARCH/REPLACE Block 1:**
```python
------- SEARCH
from custom_components.amber_powerwall.const import DOMAIN
=======
from custom_components.localshift.const import DOMAIN
+++++++ REPLACE
```

**SEARCH/REPLACE Block 2:**
```python
------- SEARCH
from custom_components.amber_powerwall.coordinator import AmberPowerwallCoordinator
=======
from custom_components.localshift.coordinator import AmberPowerwallCoordinator
+++++++ REPLACE
```

### Checklist
- [ ] Import statements updated (2)
- [ ] File saved

---

## 4.5 switch.py

**File:** `custom_components/localshift/switch.py`

### Expected Imports

```python
from custom_components.localshift.const import (
    DOMAIN,
    SWITCH_AUTOMATION_ENABLED,
    # ... other switch constants
)
from custom_components.localshift.coordinator import AmberPowerwallCoordinator
```

### Changes Required

**SEARCH/REPLACE Block 1:**
```python
------- SEARCH
from custom_components.amber_powerwall.const import (
=======
from custom_components.localshift.const import (
+++++++ REPLACE
```

**SEARCH/REPLACE Block 2:**
```python
------- SEARCH
from custom_components.amber_powerwall.coordinator import AmberPowerwallCoordinator
=======
from custom_components.localshift.coordinator import AmberPowerwallCoordinator
+++++++ REPLACE
```

### Checklist
- [ ] Import statements updated (2)
- [ ] File saved

---

## 4.6 number.py

**File:** `custom_components/localshift/number.py`

### Expected Imports

```python
from custom_components.localshift.const import (
    DOMAIN,
    CONF_CHEAP_PRICE_PERCENTILE,
    # ... other number constants
)
from custom_components.localshift.coordinator import AmberPowerwallCoordinator
```

### Changes Required

**SEARCH/REPLACE Block 1:**
```python
------- SEARCH
from custom_components.amber_powerwall.const import (
=======
from custom_components.localshift.const import (
+++++++ REPLACE
```

**SEARCH/REPLACE Block 2:**
```python
------- SEARCH
from custom_components.amber_powerwall.coordinator import AmberPowerwallCoordinator
=======
from custom_components.localshift.coordinator import AmberPowerwallCoordinator
+++++++ REPLACE
```

### Checklist
- [ ] Import statements updated (2)
- [ ] File saved

---

## 4.7 button.py

**File:** `custom_components/localshift/button.py`

### Expected Imports

```python
from custom_components.localshift.const import (
    DOMAIN,
    BUTTON_FORCE_CHARGE,
    # ... other button constants
)
from custom_components.localshift.coordinator import AmberPowerwallCoordinator
```

### Changes Required

**SEARCH/REPLACE Block 1:**
```python
------- SEARCH
from custom_components.amber_powerwall.const import (
=======
from custom_components.localshift.const import (
+++++++ REPLACE
```

**SEARCH/REPLACE Block 2:**
```python
------- SEARCH
from custom_components.amber_powerwall.coordinator import AmberPowerwallCoordinator
=======
from custom_components.localshift.coordinator import AmberPowerwallCoordinator
+++++++ REPLACE
```

### Checklist
- [ ] Import statements updated (2)
- [ ] File saved

---

## 4.8 config_flow.py

**File:** `custom_components/localshift/config_flow.py`

### Expected Imports

```python
from custom_components.localshift.const import (
    DOMAIN,
    CONF_TESLEMETRY_OPERATION_MODE,
    # ... all config constants
)
```

### Changes Required

**SEARCH:**
```python
from custom_components.amber_powerwall.const import (
```

**REPLACE:**
```python
from custom_components.localshift.const import (
```

### Checklist
- [ ] Import statement updated
- [ ] File saved

---

## 4.9 computation_engine.py

**File:** `custom_components/localshift/computation_engine.py`

### Expected Imports

```python
from custom_components.localshift.const import (
    BATTERY_CAPACITY_KWH,
    # ... other constants
)
from custom_components.localshift.computation_engine_lib.forecast_computer import ForecastComputer
from custom_components.localshift.computation_engine_lib.history_fetcher import HistoryFetcher
from custom_components.localshift.computation_engine_lib.solar_utils import SolarUtils
```

### Changes Required

**SEARCH/REPLACE Block 1:**
```python
------- SEARCH
from custom_components.amber_powerwall.const import (
=======
from custom_components.localshift.const import (
+++++++ REPLACE
```

**SEARCH/REPLACE Block 2:**
```python
------- SEARCH
from custom_components.amber_powerwall.computation_engine_lib.forecast_computer import ForecastComputer
=======
from custom_components.localshift.computation_engine_lib.forecast_computer import ForecastComputer
+++++++ REPLACE
```

**SEARCH/REPLACE Block 3:**
```python
------- SEARCH
from custom_components.amber_powerwall.computation_engine_lib.history_fetcher import HistoryFetcher
=======
from custom_components.localshift.computation_engine_lib.history_fetcher import HistoryFetcher
+++++++ REPLACE
```

**SEARCH/REPLACE Block 4:**
```python
------- SEARCH
from custom_components.amber_powerwall.computation_engine_lib.solar_utils import SolarUtils
=======
from custom_components.localshift.computation_engine_lib.solar_utils import SolarUtils
+++++++ REPLACE
```

### Checklist
- [ ] All 4 import statements updated
- [ ] File saved

---

## 4.10 battery_controller.py

**File:** `custom_components/localshift/battery_controller.py`

### Expected Imports

```python
from custom_components.localshift.const import (
    TESLEMETRY_EXPORT_PV_ONLY,
    TESLEMETRY_EXPORT_BATTERY_OK,
    # ... other constants
)
```

### Changes Required

**SEARCH:**
```python
from custom_components.amber_powerwall.const import (
```

**REPLACE:**
```python
from custom_components.localshift.const import (
```

### Log Messages

Check for any log messages mentioning component name.

### Checklist
- [ ] Import statement updated
- [ ] Log messages checked
- [ ] File saved

---

## 4.11 state_machine.py

**File:** `custom_components/localshift/state_machine.py`

### Expected Imports

```python
from custom_components.localshift.const import BatteryMode
```

### Changes Required

**SEARCH:**
```python
from custom_components.amber_powerwall.const import BatteryMode
```

**REPLACE:**
```python
from custom_components.localshift.const import BatteryMode
```

### Log Messages

Update any state machine log messages:
- Check for "Amber Powerwall" references
- Replace with "LocalShift" or use generic terms

### Checklist
- [ ] Import statement updated
- [ ] Log messages checked
- [ ] File saved

---

## 4.12 Other Core Files

For the remaining core files, apply the same pattern:

### Files
- `coordinator_data.py`
- `cost_tracker.py`
- `notification_service.py`
- `state_reader.py`

### Standard Update

For each file:

**SEARCH:**
```python
from custom_components.amber_powerwall.
```

**REPLACE:**
```python
from custom_components.localshift.
```

### Checklist
- [ ] coordinator_data.py - imports updated
- [ ] cost_tracker.py - imports updated
- [ ] notification_service.py - imports updated
- [ ] state_reader.py - imports updated

---

## 4.13 computation_engine_lib Files

**Directory:** `custom_components/localshift/computation_engine_lib/`

### Files to Update

1. `__init__.py` (likely no imports to update)
2. `forecast_computer.py`
3. `history_fetcher.py`
4. `solar_utils.py`
5. `utils.py`

### Pattern for Each File

**SEARCH:**
```python
from custom_components.amber_powerwall.
```

**REPLACE:**
```python
from custom_components.localshift.
```

**Note:** These files may have fewer imports from parent module.

### Specific Files

#### forecast_computer.py

May import from parent:
```python
from custom_components.localshift.const import BATTERY_CAPACITY_KWH
```

#### history_fetcher.py

Check for imports from parent module or sibling modules.

#### solar_utils.py

Check for imports from parent module.

#### utils.py

Check for imports from parent module.

### Checklist
- [ ] __init__.py - checked (likely no changes)
- [ ] forecast_computer.py - imports updated
- [ ] history_fetcher.py - imports updated
- [ ] solar_utils.py - imports updated
- [ ] utils.py - imports updated

---

## 4.14 Bulk Search Strategy

### Find All Import References

```bash
# Find all files with old import path
grep -r "from custom_components.amber_powerwall" custom_components/localshift/

# This will show all files that need updating
```

### Update Pattern

For each file shown:
1. Open file
2. Replace `from custom_components.amber_powerwall` with `from custom_components.localshift`
3. Save file
4. Verify with grep

### Verification After Bulk Update

```bash
# Should return NO results
grep -r "from custom_components.amber_powerwall" custom_components/localshift/

# Also check for 'import' statements
grep -r "import custom_components.amber_powerwall" custom_components/localshift/
```

---

## Phase 4 Completion Checklist

### Core Module Files (16 files)
- [ ] __init__.py
- [ ] coordinator.py
- [ ] sensor.py
- [ ] binary_sensor.py
- [ ] switch.py
- [ ] number.py
- [ ] button.py
- [ ] config_flow.py
- [ ] const.py (no imports to update, but verify)
- [ ] computation_engine.py
- [ ] battery_controller.py
- [ ] state_machine.py
- [ ] coordinator_data.py
- [ ] cost_tracker.py
- [ ] notification_service.py
- [ ] state_reader.py

### Computation Engine Lib Files (5 files)
- [ ] computation_engine_lib/__init__.py
- [ ] computation_engine_lib/forecast_computer.py
- [ ] computation_engine_lib/history_fetcher.py
- [ ] computation_engine_lib/solar_utils.py
- [ ] computation_engine_lib/utils.py

### Verification
- [ ] No files contain `from custom_components.amber_powerwall`
- [ ] No files contain `import custom_components.amber_powerwall`
- [ ] All log messages updated (component name references)
- [ ] Pre-commit passes on all modified files

---

## Pre-commit Verification

```bash
# Run on all modified Python files
pre-commit run --files custom_components/localshift/*.py \
                       custom_components/localshift/computation_engine_lib/*.py
```

Expected result: All checks pass (or only minor formatting fixes)

---

## Common Issues

### Issue 1: Circular Import After Rename

**Symptom:** ImportError about circular imports

**Solution:** This shouldn't happen if only import paths are changed. If it occurs, check for new circular dependencies introduced during refactoring.

### Issue 2: Missing Imports

**Symptom:** `ImportError: cannot import name 'X'`

**Solution:** Verify the file structure is correct and the directory rename was successful.

### Issue 3: IDE Still Shows Old Paths

**Symptom:** IDE shows import errors even though imports are correct

**Solution:** Restart IDE or rebuild project index.

---

## Next Steps

After Phase 4:
1. Verify all checklists complete
2. Run pre-commit on all files
3. Attempt to import the module in Python:
   ```bash
   python3 -c "from custom_components.localshift.const import DOMAIN; print(DOMAIN)"
   # Should print: localshift
   ```
4. Proceed to Phase 5: UI & Translations

---

**Phase Status:** ✅ COMPLETED