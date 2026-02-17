# Phase 6: Documentation Updates

**Phase:** 6 of 11  
**Status:** NOT STARTED  
**Estimated Time:** 45 minutes

---

## Overview

This phase updates all documentation files to reflect the rebrand from "Amber Powerwall" to "LocalShift" and the new entity naming conventions from backlog-med-010.

**Files to Update:** 7 documentation files
- README.md
- docs/ENTITY_REFERENCE.md
- docs/ARCHITECTURE.md
- docs/DEVELOPER_GUIDE.md
- docs/CHANGE_DETECTION.md
- docs/FORECAST_DRIVEN_CONTROL.md
- docs/LOAD_SHIFTING_GUIDE.md

---

## 6.1 README.md

**File:** `README.md`

### Global Search/Replace Operations

1. **Title and branding:**
   - `Amber Powerwall` → `LocalShift`
   - `amber_powerwall` → `localshift`
   - `amber-powerwall` → `localshift`

2. **Description updates:**
   - Remove Amber-specific language
   - Make pricing provider agnostic

### Specific Section Updates

#### Header Section
```markdown
# CURRENT
# Amber Powerwall

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![HA](https://img.shields.io/badge/Home%20Assistant-2025.6+-blue.svg)](https://www.home-assistant.io/)

Automated Tesla Powerwall 2 battery control based on Amber Electric spot pricing, Solcast solar forecasts, and configurable thresholds.

# NEW
# LocalShift

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![HA](https://img.shields.io/badge/Home%20Assistant-2025.6+-blue.svg)](https://www.home-assistant.io/)

Automated Tesla Powerwall battery control based on dynamic spot pricing, solar forecasts, and configurable thresholds. Works with Amber Electric, Octopus Energy, and other variable pricing providers.
```

#### Prerequisites Table
Update integration column header to be more generic:
```markdown
# CURRENT
| [Amber Electric](https://www.home-assistant.io/integrations/amber/) | Spot pricing | General price, feed-in price, forecasts, price spike |

# NEW
| [Amber Electric](https://www.home-assistant.io/integrations/amber/) or similar | Variable pricing | General price, feed-in price, forecasts, price spike sensor |
```

#### Installation Section
```markdown
# CURRENT
4. Search for "Amber Powerwall" and install

# NEW
4. Search for "LocalShift" and install
```

```markdown
# CURRENT
1. Copy the `custom_components/amber_powerwall/` folder to your Home Assistant `custom_components/` directory

# NEW
1. Copy the `custom_components/localshift/` folder to your Home Assistant `custom_components/` directory
```

#### Configuration Section
```markdown
# CURRENT
2. Search for "Amber Powerwall"

# NEW
2. Search for "LocalShift"
```

```markdown
# CURRENT
**Step 2 — Amber Electric:** Select your Amber entities (general price, feed-in price, forecasts, price spike sensor)

# NEW
**Step 2 — Pricing Entities:** Select your pricing entities (general price, feed-in price, forecasts, price spike sensor)
```

#### Entity Tables

**Sensors Table - Update ALL entity IDs:**
```markdown
# EXAMPLE TRANSFORMATIONS

| `sensor.amber_powerwall_effective_cheap_price` | Dynamic cheap price threshold |
→
| `sensor.localshift_price_cheap_effective` | Dynamic cheap price threshold |

| `sensor.amber_powerwall_cheap_charge_stop_price` | Effective cheap price + deadband |
→
| `sensor.localshift_price_cheap_charge_stop` | Effective cheap price + deadband |

| `sensor.amber_powerwall_solar_weighted_avg_fit` | Solcast × Amber weighted average feed-in tariff |
→
| `sensor.localshift_solar_weighted_avg_fit` | Solar-weighted average feed-in tariff |

| `sensor.amber_powerwall_active_mode` | Current battery mode from the state machine |
→
| `sensor.localshift_battery_mode` | Current battery mode from the state machine |

| `sensor.amber_powerwall_solar_battery_forecast` | SOC projection with detailed attributes |
→
| `sensor.localshift_forecast_battery` | SOC projection with detailed attributes |

| `sensor.amber_powerwall_grid_import_power` | Current grid import (kW) |
→
| `sensor.localshift_power_grid_import` | Current grid import (kW) |

| `sensor.amber_powerwall_grid_export_power` | Current grid export (kW) |
→
| `sensor.localshift_power_grid_export` | Current grid export (kW) |

| `sensor.amber_powerwall_net_electricity_cost_today` | Net cost with import/export/savings/charge cost attributes |
→
| `sensor.localshift_cost_electricity_net` | Net cost with import/export/savings/charge cost attributes |

| `sensor.amber_powerwall_decision_log` | Mode change history with reasons |
→
| `sensor.localshift_decision_log` | Mode change history with reasons |
```

**Binary Sensors Table - Update ALL entity IDs:**
```markdown
# EXAMPLE TRANSFORMATIONS

| `binary_sensor.amber_powerwall_demand_window_active` | Whether current time is within the demand window |
→
| `binary_sensor.localshift_binary_demand_window` | Whether current time is within the demand window |

| `binary_sensor.amber_powerwall_forecast_spike_within_window` | Price spike forecast within lookahead |
→
| `binary_sensor.localshift_binary_price_spike_coming` | Price spike forecast within lookahead |

(Continue for all 11 binary sensors...)
```

**Switches Table:**
```markdown
| `switch.amber_powerwall_automation_enabled` | ON | Master toggle for all automation |
→
| `switch.localshift_switch_automation_enabled` | ON | Master toggle for all automation |

(Continue for all 4 switches...)
```

**Numbers Table:**
```markdown
| `number.amber_powerwall_cheap_price_percentile` | Forecast price percentile used for cheap-charge baseline (%) |
→
| `number.localshift_number_cheap_price_percentile` | Forecast price percentile used for cheap-charge baseline (%) |

(Continue for all 6 numbers...)
```

**Buttons Table:**
```markdown
| `button.amber_powerwall_force_charge` | Manually force charge (backup mode, 3.3kW) |
→
| `button.localshift_button_force_charge` | Manually force charge (backup mode, 3.3kW) |

(Continue for all 5 buttons...)
```

#### Dashboard Section
```markdown
# CURRENT
A ready-to-use Lovelace dashboard is included at `dashboards/amber_powerwall.yaml`.

# NEW
A ready-to-use Lovelace dashboard is included at `dashboards/localshift.yaml`.
```

```markdown
# CURRENT
3. Paste the contents of `dashboards/amber_powerwall.yaml`

# NEW
3. Paste the contents of `dashboards/localshift.yaml`
```

```markdown
# CURRENT
sensor:
  - platform: integration
    source: sensor.amber_powerwall_grid_import_power
    name: grid_import_energy

# NEW
sensor:
  - platform: integration
    source: sensor.localshift_power_grid_import
    name: grid_import_energy
```

#### State Machine Section
No entity-specific changes needed, but update any references to component name.

#### Migration Section
```markdown
# CURRENT
If you're migrating from the `amber_powerwall.yaml` package:

# NEW
If you're migrating from a previous version or YAML package:
```

```markdown
# CURRENT
3. Compare the component's `sensor.amber_powerwall_active_mode` against YAML automation actions

# NEW
3. Compare the component's `sensor.localshift_battery_mode` against YAML automation actions
```

### Checklist for README.md
- [ ] Updated title and badges
- [ ] Updated description to be provider-agnostic
- [ ] Updated prerequisites table
- [ ] Updated installation instructions (all references)
- [ ] Updated configuration section
- [ ] Updated ALL sensor entity IDs (12 sensors)
- [ ] Updated ALL binary sensor entity IDs (11 binary sensors)
- [ ] Updated ALL switch entity IDs (4 switches)
- [ ] Updated ALL number entity IDs (6 numbers)
- [ ] Updated ALL button entity IDs (5 buttons)
- [ ] Updated dashboard section
- [ ] Updated required YAML helpers example
- [ ] Updated state machine section
- [ ] Updated migration section
- [ ] Saved file

---

## 6.2 docs/ENTITY_REFERENCE.md

**File:** `docs/ENTITY_REFERENCE.md`

### Global Updates
- Title: `# Amber Powerwall Entity Reference` → `# LocalShift Entity Reference`
- All entity IDs: Same mapping as README.md
- All component references: `Amber Powerwall` → `LocalShift`
- All directory paths: `custom_components/amber_powerwall` → `custom_components/localshift`

### Specific Sections

#### Introduction
```markdown
# CURRENT
Complete reference for all Home Assistant entities provided by the Amber Powerwall integration.

The integration creates **35 entities** grouped under a single "Amber Powerwall" device:

# NEW
Complete reference for all Home Assistant entities provided by the LocalShift integration.

The integration creates **35 entities** grouped under a single "LocalShift" device:
```

#### Individual Entity Documentation

For each of the 35+ entities, update:
1. Section header with new entity ID
2. Entity ID in examples
3. Any component name references

**Example:**
```markdown
# CURRENT
### 1. sensor.amber_powerwall_effective_cheap_price

# NEW
### 1. sensor.localshift_price_cheap_effective
```

#### Architecture Diagram
Update any entity names in diagrams or flowcharts

#### Troubleshooting Section
```markdown
# CURRENT
1. **Start with `sensor.amber_powerwall_active_mode`**

# NEW
1. **Start with `sensor.localshift_battery_mode`**
```

```markdown
# CURRENT
4. **Use decision log** — `sensor.amber_powerwall_decision_log` shows the last 10 mode changes

# NEW
4. **Use decision log** — `sensor.localshift_decision_log` shows the last 10 mode changes
```

```markdown
# CURRENT
5. **Enable dry run** — Use `switch.amber_powerwall_dry_run` to test without affecting the battery.

# NEW
5. **Enable dry run** — Use `switch.localshift_switch_dry_run` to test without affecting the battery.
```

### Checklist for ENTITY_REFERENCE.md
- [ ] Updated title
- [ ] Updated introduction
- [ ] Updated all 12 sensor entity IDs and documentation
- [ ] Updated all 11 binary sensor entity IDs and documentation
- [ ] Updated all 4 switch entity IDs and documentation
- [ ] Updated all 6 number entity IDs and documentation
- [ ] Updated all 5 button entity IDs and documentation
- [ ] Updated architecture diagram
- [ ] Updated troubleshooting section
- [ ] Updated all component name references
- [ ] Saved file

---

## 6.3 docs/ARCHITECTURE.md

**File:** `docs/ARCHITECTURE.md`

### Updates Needed

#### Title and Headers
```markdown
# CURRENT
# Amber Powerwall Architecture

# NEW
# LocalShift Architecture
```

#### Component References
- All mentions of `Amber Powerwall` → `LocalShift`
- All mentions of `AmberPowerwallCoordinator` → `LocalShiftCoordinator` (if applicable)

#### Directory Paths
```markdown
# CURRENT
`custom_components/amber_powerwall/coordinator.py`

# NEW
`custom_components/localshift/coordinator.py`
```

#### Entity ID Examples
Update any entity IDs used in examples to match new naming convention

#### Diagrams
Update any component names or entity references in flowcharts/diagrams

### Checklist for ARCHITECTURE.md
- [ ] Updated title and all headers
- [ ] Updated component name references throughout
- [ ] Updated directory path references
- [ ] Updated entity ID examples
- [ ] Updated diagrams/flowcharts
- [ ] Saved file

---

## 6.4 docs/DEVELOPER_GUIDE.md

**File:** `docs/DEVELOPER_GUIDE.md`

### Updates Needed

#### Title
```markdown
# CURRENT
# Amber Powerwall Developer Guide

# NEW
# LocalShift Developer Guide
```

#### Directory Paths
All references to `custom_components/amber_powerwall` → `custom_components/localshift`

#### Import Examples
```python
# CURRENT
from custom_components.amber_powerwall.const import DOMAIN

# NEW
from custom_components.localshift.const import DOMAIN
```

#### Entity ID Examples
Update any example entity IDs

#### Testing Instructions
```bash
# CURRENT
pytest tests/ -k amber_powerwall

# NEW
pytest tests/ -k localshift
```

### Checklist for DEVELOPER_GUIDE.md
- [ ] Updated title
- [ ] Updated directory paths
- [ ] Updated import examples
- [ ] Updated entity ID examples
- [ ] Updated testing instructions
- [ ] Updated any component name references
- [ ] Saved file

---

## 6.5 docs/CHANGE_DETECTION.md

**File:** `docs/CHANGE_DETECTION.md`

### Updates Needed

#### Add New Entry
Add entry at the top documenting this rebrand:

```markdown
## 2026-02-17: LocalShift Rebrand + Entity Naming (backlog-med-010)

**Type:** BREAKING CHANGE  
**Version:** 0.0.2

### Changes
- Rebranded from "Amber Powerwall" to "LocalShift"
- Changed domain from `amber_powerwall` to `localshift`
- Implemented category-based entity naming convention
- All 35+ entity IDs changed to `localshift_{category}_{name}` format

### Impact
- **Users must remove and re-add integration**
- **All entity IDs will change**
- **Automations and dashboards will break**
- **Historical data will not migrate automatically**

### Migration
1. Export automations and scripts that reference entities
2. Remove old integration from Settings → Integrations
3. Install LocalShift 0.0.2
4. Re-add integration and configure
5. Update all automations/scripts with new entity IDs
6. Update dashboards with new entity IDs

### Entity Mapping
See `REBRAND_IMPLEMENTATION_PLAN.md` for complete mapping table.
```

#### Update Existing References
Update any entity ID examples in existing entries to reflect new naming

### Checklist for CHANGE_DETECTION.md
- [ ] Added new entry for rebrand
- [ ] Updated any entity ID references in old entries
- [ ] Updated component name references
- [ ] Saved file

---

## 6.6 docs/FORECAST_DRIVEN_CONTROL.md

**File:** `docs/FORECAST_DRIVEN_CONTROL.md`

### Updates Needed

#### Title
```markdown
# CURRENT
# Amber Powerwall: Forecast-Driven Control

# NEW
# LocalShift: Forecast-Driven Control
```

#### Entity References
Update all entity ID examples to new naming convention

#### Component References
Update all `Amber Powerwall` → `LocalShift`

### Checklist for FORECAST_DRIVEN_CONTROL.md
- [ ] Updated title
- [ ] Updated entity ID examples
- [ ] Updated component references
- [ ] Saved file

---

## 6.7 docs/LOAD_SHIFTING_GUIDE.md

**File:** `docs/LOAD_SHIFTING_GUIDE.md`

### Updates Needed

#### Title
```markdown
# CURRENT
# Amber Powerwall: Load Shifting Guide

# NEW
# LocalShift: Load Shifting Guide
```

#### Entity References
Update all entity ID examples

#### Component References
Update all `Amber Powerwall` → `LocalShift`

### Checklist for LOAD_SHIFTING_GUIDE.md
- [ ] Updated title
- [ ] Updated entity ID examples
- [ ] Updated component references
- [ ] Saved file

---

## Phase 6 Completion Checklist

- [ ] README.md: All updates completed
- [ ] ENTITY_REFERENCE.md: All updates completed
- [ ] ARCHITECTURE.md: All updates completed
- [ ] DEVELOPER_GUIDE.md: All updates completed
- [ ] CHANGE_DETECTION.md: All updates completed
- [ ] FORECAST_DRIVEN_CONTROL.md: All updates completed
- [ ] LOAD_SHIFTING_GUIDE.md: All updates completed
- [ ] All files verified for remaining old references
- [ ] Phase marked complete in master plan

---

## Entity ID Quick Reference

Use this as a quick lookup when updating docs:

### Sensors (12)
| Old | New |
|-----|-----|
| `effective_cheap_price` | `localshift_price_cheap_effective` |
| `cheap_charge_stop_price` | `localshift_price_cheap_charge_stop` |
| `solar_weighted_avg_fit` | `localshift_solar_weighted_avg_fit` |
| `battery_automation_active_mode` | `localshift_battery_mode` |
| `solar_battery_forecast` | `localshift_forecast_battery` |
| `grid_import_power` | `localshift_power_grid_import` |
| `grid_export_power` | `localshift_power_grid_export` |
| `net_electricity_cost_today` | `localshift_cost_electricity_net` |
| `battery_automation_decision_log` | `localshift_decision_log` |
| `forecast_history` | `localshift_forecast_history` |
| `daily_forecast` | `localshift_forecast_daily` |
| `minimum_target_soc` | `localshift_target_soc_minimum` |

### Binary Sensors (8)
| Old | New |
|-----|-----|
| `forecast_spike_within_window` | `localshift_binary_price_spike_coming` |
| `battery_force_discharge_active` | `localshift_binary_discharge_forced` |
| `battery_force_charge_active` | `localshift_binary_charge_forced` |
| `battery_boost_charge_active` | `localshift_binary_charge_boost` |
| `forecast_expensive_period_coming` | `localshift_binary_price_expensive_coming` |
| `solar_can_reach_target` | `localshift_binary_solar_can_reach_target` |
| `boost_charge_needed` | `localshift_binary_charge_boost_needed` |
| `demand_window_active` | `localshift_binary_demand_window` |

---

**Phase Status:** ☐ NOT STARTED | ☐ IN PROGRESS | ☐ COMPLETED