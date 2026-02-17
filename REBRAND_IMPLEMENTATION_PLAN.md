# LocalShift Rebrand & Entity Naming Implementation Plan

**Status:** COMPLETED  
**Started:** 2026-02-17  
**Version Target:** 0.0.2  
**Backlog Item:** backlog-med-010 + Full Rebrand

---

## Overview

This plan combines two major changes:
1. **backlog-med-010**: Category-based entity naming conventions
2. **Rebrand**: Amber Powerwall → LocalShift

### Key Decisions
- ✅ Entity prefix: `localshift_{category}_{specific_name}`
- ✅ Breaking changes accepted - users will need to reconfigure
- ✅ Version bump: 0.0.1 → 0.0.2
- ✅ Domain: `amber_powerwall` → `localshift`
- ✅ Config constants: `CONF_AMBER_*` → `CONF_PRICING_*`
- ✅ Class names: `AmberPowerwall*` → `LocalShift*`

### Breaking Changes
- **Entity IDs will change** (all 37 entities)
- **Domain will change** (users must remove old integration and re-add)
- **Directory path changes** (HACS users will need to reinstall)
- **Config keys change** (no migration - fresh install required)

---

## CRITICAL DISCOVERY

The codebase has **inconsistent entity naming**:

| Platform | Current State | Example |
|----------|---------------|---------|
| Sensors | NO prefix | `effective_cheap_price` |
| Binary Sensors | NO prefix | `forecast_spike_within_window` |
| Buttons | HAS prefix | `amber_powerwall_force_charge` |
| Switches | HAS prefix | `amber_powerwall_automation_enabled` |
| Numbers | HAS prefix | `amber_powerwall_cheap_price_percentile` |

**Resolution:** All entities will be uniformly renamed to `localshift_{category}_{name}` format.

---

## Phase Overview (13 Phases)

| Phase | Description | Est. Time | Detailed Plan |
|-------|-------------|-----------|---------------|
| 1 | Preparation | 10 min | Below |
| 2 | Core Infrastructure | 15 min | `REBRAND_PHASE_2_INFRASTRUCTURE.md` |
| 3 | Entity Naming | 75 min | `REBRAND_PHASE_3_ENTITIES.md` |
| 3.5 | Class Renaming | 20 min | `REBRAND_PHASE_3_5_CLASSES.md` |
| 4 | Python Imports | 30 min | `REBRAND_PHASE_4_IMPORTS.md` |
| 4.5 | Config Constants | 25 min | `REBRAND_PHASE_4_5_CONFIG_CONSTANTS.md` |
| 5 | UI & Translations | 15 min | `REBRAND_PHASE_5_UI_TRANSLATIONS.md` |
| 6 | Documentation | 45 min | `REBRAND_PHASE_6_DOCS.md` |
| 7 | Dashboard | 30 min | `REBRAND_PHASE_7_DASHBOARD.md` |
| 8 | Tests | 15 min | `REBRAND_PHASE_8_TESTS.md` |
| 9 | Other Files | 10 min | Below |
| 10 | Verification | 30 min | Below |
| 11 | Finalization | 15 min | Below |

**Total Estimated Time:** ~5.5 hours

---

## Phase 1: Preparation ✅

### 1.1 Create Implementation Branch
- [x] Review plan document
- [ ] Create git branch: `feature/rebrand-localshift`
- [ ] Commit message: "Start rebrand to LocalShift + backlog-med-010"

### 1.2 Update Backlog
- [ ] Mark `backlog/backlog-med-010.md` status: `IN_PROGRESS`
- [ ] Update `backlog/index.md` status column

---

## Phase 2: Core Infrastructure (Directory & Config)

**Detailed Plan:** `REBRAND_PHASE_2_INFRASTRUCTURE.md`

### Actions
- Directory rename: `custom_components/amber_powerwall` → `custom_components/localshift`
- manifest.json: domain, name, version
- const.py: DOMAIN constant
- pyproject.toml: all path references
- hacs.json: name

### Checklist
- [ ] Directory renamed with `git mv`
- [ ] manifest.json updated
- [ ] const.py DOMAIN updated
- [ ] pyproject.toml updated
- [ ] hacs.json updated

---

## Phase 3: Entity Naming (backlog-med-010)

**Detailed Plan:** `REBRAND_PHASE_3_ENTITIES.md`

### Scope
**37 entities** across 5 platforms:

| Platform | Count | Current Prefix | New Pattern |
|----------|-------|----------------|-------------|
| Sensors | 12 | None | `localshift_price_*`, `localshift_power_*`, etc. |
| Binary Sensors | 8 | None | `localshift_binary_*` |
| Buttons | 5 | `amber_powerwall_` | `localshift_button_*` |
| Numbers | 7 | `amber_powerwall_` | `localshift_number_*` |
| Switches | 5 | `amber_powerwall_` | `localshift_switch_*` |

### Checklist
- [ ] sensor.py: 12 entities updated
- [ ] binary_sensor.py: 8 entities updated
- [ ] button.py: 5 entities updated
- [ ] number.py: 7 entities updated
- [ ] switch.py: 5 entities updated

---

## Phase 3.5: Class Renaming

**Detailed Plan:** `REBRAND_PHASE_3_5_CLASSES.md`

### Classes to Rename (6 classes)

| Current | New |
|---------|-----|
| `AmberPowerwallSensorBase` | `LocalShiftSensorBase` |
| `AmberPowerwallBinarySensorBase` | `LocalShiftBinarySensorBase` |
| `AmberPowerwallButtonBase` | `LocalShiftButtonBase` |
| `AmberPowerwallNumber` | `LocalShiftNumber` |
| `AmberPowerwallSwitch` | `LocalShiftSwitch` |
| `AmberPowerwallCoordinator` | `LocalShiftCoordinator` |

### Device Info Updates
- name: "Amber Powerwall" → "LocalShift"
- sw_version: "0.1.0" → "0.0.2"

### Checklist
- [ ] All 6 classes renamed
- [ ] Device info updated in all 5 entity files
- [ ] All imports updated

---

## Phase 4: Python Source Files (Import Updates)

**Detailed Plan:** `REBRAND_PHASE_4_IMPORTS.md`

### Scope
- 16 Python files in `custom_components/localshift/`
- 5 Python files in `custom_components/localshift/computation_engine_lib/`

### Change Pattern
```python
# OLD
from custom_components.amber_powerwall.const import DOMAIN

# NEW
from custom_components.localshift.const import DOMAIN
```

### Log Messages to Update
- "Amber Powerwall coordinator started" → "LocalShift coordinator started"
- "Amber Powerwall coordinator stopped" → "LocalShift coordinator stopped"
- "Amber Powerwall automation enabled" → "LocalShift automation enabled"
- "Amber Powerwall automation disabled" → "LocalShift automation disabled"

### Checklist
- [ ] All imports updated
- [ ] Log messages updated
- [ ] Module docstrings updated

---

## Phase 4.5: Config Constant Renaming

**Detailed Plan:** `REBRAND_PHASE_4_5_CONFIG_CONSTANTS.md`

### Constants to Rename (5 constants)

| Current | New |
|---------|-----|
| `CONF_AMBER_GENERAL_PRICE` | `CONF_PRICING_GENERAL_PRICE` |
| `CONF_AMBER_FEED_IN_PRICE` | `CONF_PRICING_FEED_IN_PRICE` |
| `CONF_AMBER_GENERAL_FORECAST` | `CONF_PRICING_GENERAL_FORECAST` |
| `CONF_AMBER_FEED_IN_FORECAST` | `CONF_PRICING_FEED_IN_FORECAST` |
| `CONF_AMBER_PRICE_SPIKE` | `CONF_PRICING_PRICE_SPIKE` |

### Files Affected
- const.py
- coordinator.py
- config_flow.py
- state_reader.py
- computation_engine.py
- strings.json
- translations/en.json

### Checklist
- [ ] const.py definitions updated
- [ ] DEFAULT_ENTITY_IDS updated
- [ ] All usage files updated
- [ ] Config flow step renamed: `async_step_amber` → `async_step_pricing`

---

## Phase 5: UI & Translations

**Detailed Plan:** `REBRAND_PHASE_5_UI_TRANSLATIONS.md`

### Changes
- Config step key: `amber` → `pricing`
- Titles: "Amber Electric Entities" → "Pricing Entities"
- Options title: "Amber Powerwall Settings" → "LocalShift Settings"

### Checklist
- [ ] strings.json updated
- [ ] translations/en.json updated

---

## Phase 6: Documentation

**Detailed Plan:** `REBRAND_PHASE_6_DOCS.md`

### Files to Update (7 files)
- README.md
- docs/ARCHITECTURE.md
- docs/CHANGE_DETECTION.md
- docs/DEVELOPER_GUIDE.md
- docs/ENTITY_REFERENCE.md
- docs/FORECAST_DRIVEN_CONTROL.md
- docs/LOAD_SHIFTING_GUIDE.md

### Checklist
- [ ] README.md updated
- [ ] All doc files updated

---

## Phase 7: Dashboard

**Detailed Plan:** `REBRAND_PHASE_7_DASHBOARD.md`

### Actions
1. Rename file: `amber_powerwall_component.yaml` → `localshift.yaml`
2. Update all 37+ entity references
3. Update Jinja templates

### Checklist
- [ ] File renamed
- [ ] All entity references updated
- [ ] Jinja templates updated

---

## Phase 8: Tests

**Detailed Plan:** `REBRAND_PHASE_8_TESTS.md`

### Files to Update (6 files)
- tests/__init__.py
- tests/conftest.py
- tests/test_computation_engine.py
- tests/test_coordinator.py
- tests/test_forecast_computer.py
- tests/test_integration.py

### Checklist
- [ ] All import paths updated
- [ ] All class names updated

---

## Phase 9: Other Files

### Files to Update

#### .pre-commit-config.yaml
```yaml
# Update path references
args: ["custom_components/localshift", "--min-confidence", "80"]
```

#### .clinerules
```
# Update path references
- When editing any Python file in `custom_components/localshift/`...
```

### Files to Delete
- `amber_powerwall.egg-info/` directory

### Checklist
- [ ] .pre-commit-config.yaml updated
- [ ] .clinerules updated
- [ ] Build artifacts removed

---

## Phase 10: Verification

### Pre-commit Checks
```bash
pre-commit run --all-files
```

### Test Suite
```bash
pytest tests/ -v
```

### Search for Remaining References
```bash
# Find any remaining old references
grep -r "amber_powerwall" --exclude-dir=".git" --exclude-dir="__pycache__" .
grep -r "Amber Powerwall" --exclude-dir=".git" --exclude-dir="__pycache__" .

# Should only find references in:
# - This implementation plan
# - Backlog files (historical)
# - Git history
```

### Manual Verification
- [ ] All Python files have new imports
- [ ] All entity IDs follow new convention
- [ ] All class names updated
- [ ] All config constants updated
- [ ] manifest.json correct
- [ ] Version is 0.0.2

### Checklist
- [ ] Pre-commit passes
- [ ] Tests pass
- [ ] No old references found

---

## Phase 11: Finalization

### Update Backlog
- [ ] Mark `backlog/backlog-med-010.md` status: `COMPLETED`
- [ ] Update `backlog/index.md` status column

### Commit Changes
```bash
git add -A
git commit -m "Rebrand to LocalShift + implement backlog-med-010 entity naming

- Renamed domain: amber_powerwall → localshift
- Renamed 6 classes: AmberPowerwall* → LocalShift*
- Renamed 5 config constants: CONF_AMBER_* → CONF_PRICING_*
- Updated all 37 entity IDs to follow localshift_{category}_{name} pattern
- Updated all documentation
- Updated dashboard
- Version bump: 0.0.2

BREAKING CHANGE: All entity IDs have changed. Users must remove and re-add integration.
Config keys have changed. Fresh install required.

Closes: backlog-med-010"
```

---

## File Inventory

### Files to Rename (2)
1. `custom_components/amber_powerwall/` → `custom_components/localshift/`
2. `dashboards/amber_powerwall_component.yaml` → `dashboards/localshift.yaml`

### Files to Modify (Python - 21 files)
- 16 files in `custom_components/localshift/`
- 5 files in `custom_components/localshift/computation_engine_lib/`
- 6 files in `tests/`

### Files to Modify (Config - 6 files)
- manifest.json
- strings.json
- translations/en.json
- pyproject.toml
- hacs.json
- .pre-commit-config.yaml

### Files to Modify (Documentation - 7 files)
- README.md
- docs/*.md (6 files)

### Files to Modify (Dashboard - 1 file)
- dashboards/localshift.yaml

### Files to Delete
- `amber_powerwall.egg-info/`

**Total Files Modified:** ~42 files

---

## Risk Assessment

### High Risk
- **Directory rename**: May cause import errors if not done carefully
- **Entity ID changes**: Users will lose history and automations
- **Domain change**: Requires full reinstall
- **Config constant changes**: Existing configs invalid

### Medium Risk
- **Test imports**: May miss some import paths
- **Dashboard**: May have entity references in Jinja templates
- **Config flow**: Step method rename may break flow

### Low Risk
- **Documentation**: Can be fixed post-merge
- **Pre-commit**: Will catch syntax errors

### Mitigation
- Work in feature branch
- Run pre-commit after each phase
- Test import of each modified file
- Perform full test suite run before merge

---

## Entity ID Quick Reference

### Sensors (12)
| New unique_id | Description |
|---------------|-------------|
| `localshift_price_cheap_effective` | Dynamic cheap price threshold |
| `localshift_price_cheap_charge_stop` | Cheap price + deadband |
| `localshift_solar_weighted_avg_fit` | Solar-weighted avg FIT |
| `localshift_battery_mode` | Current battery mode |
| `localshift_forecast_battery` | SOC forecast |
| `localshift_power_grid_import` | Grid import power |
| `localshift_power_grid_export` | Grid export power |
| `localshift_cost_electricity_net` | Net electricity cost |
| `localshift_decision_log` | Mode change history |
| `localshift_forecast_history` | Historical forecasts |
| `localshift_forecast_daily` | Daily forecast |
| `localshift_target_soc_minimum` | Minimum target SOC |

### Binary Sensors (8)
| New unique_id | Description |
|---------------|-------------|
| `localshift_binary_price_spike_coming` | Price spike forecast |
| `localshift_binary_discharge_forced` | Force discharge active |
| `localshift_binary_charge_forced` | Force charge active |
| `localshift_binary_charge_boost` | Boost charge active |
| `localshift_binary_price_expensive_coming` | Expensive period forecast |
| `localshift_binary_solar_can_reach_target` | Solar can reach target |
| `localshift_binary_charge_boost_needed` | Boost charge needed |
| `localshift_binary_demand_window` | Demand window active |

### Buttons (5)
| New unique_id | Description |
|---------------|-------------|
| `localshift_button_force_charge` | Force charge button |
| `localshift_button_force_discharge` | Force discharge button |
| `localshift_button_boost_charge` | Boost charge button |
| `localshift_button_self_consumption` | Self consumption button |
| `localshift_button_update_forecast` | Update forecast button |

### Numbers (7)
| New unique_id | Description |
|---------------|-------------|
| `localshift_number_cheap_price_percentile` | Cheap price percentile |
| `localshift_number_max_pre_charge_price` | Max pre-charge price |
| `localshift_number_price_deadband` | Price deadband |
| `localshift_number_forecast_lookahead` | Forecast lookahead |
| `localshift_number_pre_charge_battery_threshold` | Pre-charge threshold |
| `localshift_number_battery_target` | Battery target |
| `localshift_number_load_weight_recent` | Load weight |

### Switches (5)
| New unique_id | Description |
|---------------|-------------|
| `localshift_switch_automation_enabled` | Master automation toggle |
| `localshift_switch_spike_discharge_enabled` | Spike discharge toggle |
| `localshift_switch_dry_run` | Dry run toggle |
| `localshift_switch_demand_window_block` | Demand window block |
| `localshift_switch_allow_dw_entry_under_target` | Allow DW entry under target |

---

## Notes

- Keep this plan document updated as work progresses
- Mark checkboxes as items are completed
- Add notes for any unexpected issues
- Update time estimates if significantly different

---

**Plan Version:** 2.0  
**Last Updated:** 2026-02-17

## Detailed Phase Plans

The following detailed plan files exist:

1. `REBRAND_PHASE_2_INFRASTRUCTURE.md` - Directory and config
2. `REBRAND_PHASE_3_ENTITIES.md` - Entity naming (37 entities)
3. `REBRAND_PHASE_3_5_CLASSES.md` - Class renaming (6 classes)
4. `REBRAND_PHASE_4_IMPORTS.md` - Import path updates
5. `REBRAND_PHASE_4_5_CONFIG_CONSTANTS.md` - Config constant renaming
6. `REBRAND_PHASE_5_UI_TRANSLATIONS.md` - UI strings
7. `REBRAND_PHASE_6_DOCS.md` - Documentation
8. `REBRAND_PHASE_7_DASHBOARD.md` - Dashboard
9. `REBRAND_PHASE_8_TESTS.md` - Test files