# Phase 7: Dashboard Updates

**Phase:** 7 of 11  
**Status:** NOT STARTED  
**Estimated Time:** 30 minutes

---

## Overview

This phase updates the dashboard YAML file to reflect:
1. New file name: `amber_powerwall_component.yaml` → `localshift.yaml`
2. All entity ID references (35+ entities)
3. Title and header comments
4. Jinja template references

---

## 7.1 File Rename

### Action
```bash
git mv dashboards/amber_powerwall_component.yaml dashboards/localshift.yaml
```

**Checklist:**
- [ ] File renamed
- [ ] Git tracking preserved

---

## 7.2 Header Comments and Title

**File:** `dashboards/localshift.yaml`

### Header Comments
```yaml
# CURRENT
# Amber Powerwall Custom Component Dashboard
# =============================================================================
#
# Dashboard primarily uses entities from the amber_powerwall custom component.
# Also references:
#   - my_home_* entities from Tesla Powerwall integration
#   - 100h_* entities from Amber Electric YAML package
#
# Usage:
#   Add to configuration.yaml:
#     dashboards:
#       amber-powerwall:
#         mode: yaml
#         filename: dashboards/amber_powerwall_component.yaml
#         title: Powerwall
#         icon: mdi:battery-sync

# NEW
# LocalShift Battery Automation Dashboard
# =============================================================================
#
# Dashboard primarily uses entities from the localshift custom component.
# Also references:
#   - my_home_* entities from Tesla Powerwall integration
#   - 100h_* entities from pricing integration (e.g., Amber Electric)
#
# Usage:
#   Add to configuration.yaml:
#     dashboards:
#       localshift:
#         mode: yaml
#         filename: dashboards/localshift.yaml
#         title: Battery Control
#         icon: mdi:battery-sync
```

### Title
```yaml
# CURRENT
title: Powerwall

# NEW
title: Battery Control
```

**Checklist:**
- [ ] Updated header comments
- [ ] Updated title
- [ ] Updated filename references

---

## 7.3 Entity ID Updates

### Complete Entity Mapping

All entity references must be updated. The dashboard contains 35+ entity references across multiple card types.

### Sensor Entities (12)

| Current Entity ID | New Entity ID |
|-------------------|---------------|
| `sensor.amber_powerwall_active_mode` | `sensor.localshift_battery_mode` |
| `sensor.amber_powerwall_effective_cheap_price` | `sensor.localshift_price_cheap_effective` |
| `sensor.amber_powerwall_cheap_charge_stop_price` | `sensor.localshift_price_cheap_charge_stop` |
| `sensor.amber_powerwall_solar_weighted_avg_fit` | `sensor.localshift_solar_weighted_avg_fit` |
| `sensor.amber_powerwall_solar_battery_forecast` | `sensor.localshift_forecast_battery` |
| `sensor.amber_powerwall_grid_import_power` | `sensor.localshift_power_grid_import` |
| `sensor.amber_powerwall_grid_export_power` | `sensor.localshift_power_grid_export` |
| `sensor.amber_powerwall_net_electricity_cost_today` | `sensor.localshift_cost_electricity_net` |
| `sensor.amber_powerwall_decision_log` | `sensor.localshift_decision_log` |
| `sensor.amber_powerwall_forecast_history` | `sensor.localshift_forecast_history` |
| `sensor.amber_powerwall_daily_forecast` | `sensor.localshift_forecast_daily` |
| `sensor.amber_powerwall_minimum_target_soc` | `sensor.localshift_target_soc_minimum` |

### Binary Sensor Entities (8+)

| Current Entity ID | New Entity ID |
|-------------------|---------------|
| `binary_sensor.amber_powerwall_demand_window_active` | `binary_sensor.localshift_binary_demand_window` |
| `binary_sensor.amber_powerwall_forecast_spike_within_window` | `binary_sensor.localshift_binary_price_spike_coming` |
| `binary_sensor.amber_powerwall_forecast_expensive_period_coming` | `binary_sensor.localshift_binary_price_expensive_coming` |
| `binary_sensor.amber_powerwall_force_discharge_active` | `binary_sensor.localshift_binary_discharge_forced` |
| `binary_sensor.amber_powerwall_force_charge_active` | `binary_sensor.localshift_binary_charge_forced` |
| `binary_sensor.amber_powerwall_boost_charge_active` | `binary_sensor.localshift_binary_charge_boost` |
| `binary_sensor.amber_powerwall_solar_can_reach_target` | `binary_sensor.localshift_binary_solar_can_reach_target` |
| `binary_sensor.amber_powerwall_boost_charge_needed` | `binary_sensor.localshift_binary_charge_boost_needed` |

### Switch Entities (4+)

| Current Entity ID | New Entity ID |
|-------------------|---------------|
| `switch.amber_powerwall_automation_enabled` | `switch.localshift_switch_automation_enabled` |
| `switch.amber_powerwall_spike_discharge_enabled` | `switch.localshift_switch_spike_discharge_enabled` |
| `switch.amber_powerwall_dry_run` | `switch.localshift_switch_dry_run` |
| `switch.amber_powerwall_demand_window_block` | `switch.localshift_switch_demand_window_block` |
| `switch.amber_powerwall_allow_dw_entry_under_target` | `switch.localshift_switch_allow_dw_entry_under_target` |

### Number Entities (7)

| Current Entity ID | New Entity ID |
|-------------------|---------------|
| `number.amber_powerwall_cheap_price_percentile` | `number.localshift_number_cheap_price_percentile` |
| `number.amber_powerwall_max_pre_charge_price` | `number.localshift_number_max_pre_charge_price` |
| `number.amber_powerwall_price_deadband` | `number.localshift_number_price_deadband` |
| `number.amber_powerwall_forecast_lookahead` | `number.localshift_number_forecast_lookahead` |
| `number.amber_powerwall_pre_charge_battery_threshold` | `number.localshift_number_pre_charge_battery_threshold` |
| `number.amber_powerwall_battery_target` | `number.localshift_number_battery_target` |
| `number.amber_powerwall_load_weight_recent` | `number.localshift_number_load_weight_recent` |

### Button Entities (5)

| Current Entity ID | New Entity ID |
|-------------------|---------------|
| `button.amber_powerwall_return_to_self_consumption` | `button.localshift_button_self_consumption` |
| `button.amber_powerwall_force_charge` | `button.localshift_button_force_charge` |
| `button.amber_powerwall_boost_charge_5kw` | `button.localshift_button_boost_charge` |
| `button.amber_powerwall_force_discharge` | `button.localshift_button_force_discharge` |
| `button.amber_powerwall_update_forecast` | `button.localshift_button_update_forecast` |

---

## 7.4 Jinja Template Updates

### Template Context

The dashboard uses Jinja templates extensively. Each template reference must be updated.

### Example Template Updates

#### Status Card Template
```yaml
# CURRENT
{% set mode = states('sensor.amber_powerwall_active_mode') %}
{% if mode == 'manual' %}🟠 **MANUAL OVERRIDE**

# NEW
{% set mode = states('sensor.localshift_battery_mode') %}
{% if mode == 'manual' %}🟠 **MANUAL OVERRIDE**
```

#### Forecast Card Template
```yaml
# CURRENT
{% set forecast = 'sensor.amber_powerwall_solar_battery_forecast' %}
{% set target = states('number.amber_powerwall_battery_target') | round(0) | int %}
{% set dw_active = is_state('binary_sensor.amber_powerwall_demand_window_active', 'on') %}

# NEW
{% set forecast = 'sensor.localshift_forecast_battery' %}
{% set target = states('number.localshift_number_battery_target') | round(0) | int %}
{% set dw_active = is_state('binary_sensor.localshift_binary_demand_window', 'on') %}
```

#### Price Threshold Template
```yaml
# CURRENT
Threshold: ${{ states('sensor.amber_powerwall_effective_cheap_price') }}/kWh

# NEW
Threshold: ${{ states('sensor.localshift_price_cheap_effective') }}/kWh
```

#### Cost Tracking Template
```yaml
# CURRENT
{% set cost = 'sensor.amber_powerwall_net_electricity_cost_today' %}
**Net: ${{ states(cost) }}**

# NEW
{% set cost = 'sensor.localshift_cost_electricity_net' %}
**Net: ${{ states(cost) }}**
```

#### Debug State Summary Template

This is a large template with MANY entity references. Key sections:

```yaml
# CURRENT
Mode: {{ states('sensor.amber_powerwall_active_mode') }}
Grid Import: {{ states('sensor.amber_powerwall_grid_import_power') }} kW
Grid Export: {{ states('sensor.amber_powerwall_grid_export_power') }} kW
Demand Window: {{ states('binary_sensor.amber_powerwall_demand_window_active') }}

# NEW
Mode: {{ states('sensor.localshift_battery_mode') }}
Grid Import: {{ states('sensor.localshift_power_grid_import') }} kW
Grid Export: {{ states('sensor.localshift_power_grid_export') }} kW
Demand Window: {{ states('binary_sensor.localshift_binary_demand_window') }}
```

```yaml
# CURRENT
Forecast Spike Within Window: {{ states('binary_sensor.amber_powerwall_forecast_spike_within_window') }}
Max Forecast Price: ${{ state_attr('binary_sensor.amber_powerwall_forecast_spike_within_window', 'max_forecast_price') | default(0) }}/kWh

# NEW
Forecast Spike Within Window: {{ states('binary_sensor.localshift_binary_price_spike_coming') }}
Max Forecast Price: ${{ state_attr('binary_sensor.localshift_binary_price_spike_coming', 'max_forecast_price') | default(0) }}/kWh
```

```yaml
# CURRENT
Effective Cheap Price: ${{ states('sensor.amber_powerwall_effective_cheap_price') }}/kWh
Solar Remaining: {{ state_attr('sensor.amber_powerwall_solar_weighted_avg_fit', 'total_solar_remaining_kwh') | default(0) }} kWh

# NEW
Effective Cheap Price: ${{ states('sensor.localshift_price_cheap_effective') }}/kWh
Solar Remaining: {{ state_attr('sensor.localshift_solar_weighted_avg_fit', 'total_solar_remaining_kwh') | default(0) }} kWh
```

```yaml
# CURRENT
Latest Decision: {{ state_attr('sensor.amber_powerwall_decision_log', 'reason') | default('No decisions yet') }}
Latest SOC: {{ state_attr('sensor.amber_powerwall_decision_log', 'soc') }}%

# NEW
Latest Decision: {{ state_attr('sensor.localshift_decision_log', 'reason') | default('No decisions yet') }}
Latest SOC: {{ state_attr('sensor.localshift_decision_log', 'soc') }}%
```

```yaml
# CURRENT
{% for entry in state_attr('sensor.amber_powerwall_decision_log', 'history') | default([]) | slice(-5) %}

# NEW
{% for entry in state_attr('sensor.localshift_decision_log', 'history') | default([]) | slice(-5) %}
```

```yaml
# CURRENT
{%- for item in state_attr('sensor.amber_powerwall_daily_forecast', 'forecast_hourly') | default([]) %}

# NEW
{%- for item in state_attr('sensor.localshift_forecast_daily', 'forecast_hourly') | default([]) %}
```

---

## 7.5 Implementation Strategy

### Search/Replace Approach

Use systematic search/replace to avoid missing references:

1. **Sensor prefix:**
   - Search: `sensor.amber_powerwall_`
   - Replace: `sensor.localshift_` (then manually fix the suffix)

2. **Binary sensor prefix:**
   - Search: `binary_sensor.amber_powerwall_`
   - Replace: `binary_sensor.localshift_` (then manually fix the suffix)

3. **Switch prefix:**
   - Search: `switch.amber_powerwall_`
   - Replace: `switch.localshift_switch_`

4. **Number prefix:**
   - Search: `number.amber_powerwall_`
   - Replace: `number.localshift_number_`

5. **Button prefix:**
   - Search: `button.amber_powerwall_`
   - Replace: `button.localshift_button_`

### Manual Verification Required

After bulk search/replace, manually verify:
- [ ] All entity IDs match the new convention
- [ ] All Jinja templates compile correctly
- [ ] No orphaned old entity references
- [ ] Button entity IDs (some may have different suffixes)

---

## 7.6 Tile Card Updates

### Status Tile
```yaml
# CURRENT
- type: tile
  entity: sensor.amber_powerwall_active_mode
  name: Mode

# NEW
- type: tile
  entity: sensor.localshift_battery_mode
  name: Mode
```

### Binary Sensor Tiles
```yaml
# CURRENT
- type: tile
  entity: binary_sensor.amber_powerwall_demand_window_active
  name: Demand Window

# NEW
- type: tile
  entity: binary_sensor.localshift_binary_demand_window
  name: Demand Window
```

### Power Monitoring Tiles
```yaml
# CURRENT
- type: tile
  entity: sensor.amber_powerwall_grid_import_power
  name: Grid Import
- type: tile
  entity: sensor.amber_powerwall_grid_export_power
  name: Grid Export

# NEW
- type: tile
  entity: sensor.localshift_power_grid_import
  name: Grid Import
- type: tile
  entity: sensor.localshift_power_grid_export
  name: Grid Export
```

---

## 7.7 Button Action Updates

### Manual Override Buttons

```yaml
# CURRENT
tap_action:
  action: call-service
  service: button.press
  target:
    entity_id: button.amber_powerwall_return_to_self_consumption

# NEW
tap_action:
  action: call-service
  service: button.press
  target:
    entity_id: button.localshift_button_self_consumption
```

```yaml
# CURRENT
target:
  entity_id: button.amber_powerwall_force_charge

# NEW
target:
  entity_id: button.localshift_button_force_charge
```

```yaml
# CURRENT
target:
  entity_id: button.amber_powerwall_boost_charge_5kw

# NEW
target:
  entity_id: button.localshift_button_boost_charge
```

```yaml
# CURRENT
target:
  entity_id: button.amber_powerwall_force_discharge

# NEW
target:
  entity_id: button.localshift_button_force_discharge
```

```yaml
# CURRENT
target:
  entity_id: button.amber_powerwall_update_forecast

# NEW
target:
  entity_id: button.localshift_button_update_forecast
```

---

## 7.8 Graph/Chart Entity Updates

### Forecast Graph

```yaml
# CURRENT
- entity: sensor.amber_powerwall_daily_forecast
  name: Planned SOC

# NEW
- entity: sensor.localshift_forecast_daily
  name: Planned SOC
```

---

## 7.9 Entity-ID Tracking Updates

Update any `entity_id:` lists:

```yaml
# CURRENT
entity_id:
  - sensor.amber_powerwall_active_mode

# NEW
entity_id:
  - sensor.localshift_battery_mode
```

---

## 7.10 Verification Steps

### Manual Verification Checklist

After completing all updates:

1. **Search for old references:**
   ```bash
   grep -n "amber_powerwall" dashboards/localshift.yaml
   ```
   
   Expected result: **0 matches** (or only in comments)

2. **Validate YAML syntax:**
   ```bash
   python3 -c "import yaml; yaml.safe_load(open('dashboards/localshift.yaml'))"
   ```

3. **Count entity references:**
   - Sensors: Should find ~12+ references
   - Binary sensors: Should find ~8+ references
   - Switches: Should find ~5+ references
   - Numbers: Should find ~7+ references
   - Buttons: Should find ~5+ references

4. **Verify Jinja templates:**
   - No syntax errors in `{{ }}` or `{% %}`
   - All `state_attr()` calls use correct entity IDs
   - All `states()` calls use correct entity IDs

---

## 7.11 Implementation Checklist

- [ ] File renamed to `dashboards/localshift.yaml`
- [ ] Updated header comments
- [ ] Updated title
- [ ] Updated all sensor entity IDs (12+)
- [ ] Updated all binary sensor entity IDs (8+)
- [ ] Updated all switch entity IDs (5+)
- [ ] Updated all number entity IDs (7+)
- [ ] Updated all button entity IDs (5+)
- [ ] Updated button action targets
- [ ] Updated all Jinja templates
- [ ] Updated graph/chart entities
- [ ] Verified no old entity references remain
- [ ] Validated YAML syntax
- [ ] Saved file

---

## Phase 7 Completion Checklist

- [ ] Dashboard file renamed
- [ ] All entity references updated (35+)
- [ ] All Jinja templates updated
- [ ] All button actions updated
- [ ] YAML syntax validated
- [ ] No old references remain
- [ ] Phase marked complete in master plan

---

## Quick Reference: Most Common Entities

These entities appear most frequently in the dashboard:

| Old | New | Frequency |
|-----|-----|-----------|
| `sensor.amber_powerwall_active_mode` | `sensor.localshift_battery_mode` | High |
| `sensor.amber_powerwall_daily_forecast` | `sensor.localshift_forecast_daily` | High |
| `binary_sensor.amber_powerwall_demand_window_active` | `binary_sensor.localshift_binary_demand_window` | High |
| `sensor.amber_powerwall_effective_cheap_price` | `sensor.localshift_price_cheap_effective` | Medium |
| `sensor.amber_powerwall_decision_log` | `sensor.localshift_decision_log` | Medium |

---

**Phase Status:** ☐ NOT STARTED | ☐ IN PROGRESS | ☐ COMPLETED