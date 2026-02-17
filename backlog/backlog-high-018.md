# Backlog Item Template

**ID:** backlog-high-018  
**Priority:** HIGH  
**Status:** COMPLETED  
**Created:** 2026-02-17  
**Updated:** 2026-02-17  

---

## Summary

Dashboard references incorrect entity IDs for load weight and solar weighted avg FIT sensors.

---

## Description

The dashboard file `dashboards/amber_powerwall_component.yaml` references entity IDs that don't match the actual entity IDs created by the integration:

1. Dashboard uses: `number.amber_powerwall_load_recent_weight`
   - Actual entity: `number.amber_powerwall_load_weight_recent`

2. Dashboard uses: `sensor.amber_powerwall_solar_weighted_average_fit`
   - Actual entity: `sensor.amber_powerwall_solar_weighted_avg_fit`

This mismatch causes Home Assistant to show "recreate entity ids" option in config, and the dashboard may display unknown values or fail to show data for these entities.

The code itself is consistent:
- `CONF_LOAD_WEIGHT_RECENT = "load_weight_recent"` in `const.py`
- Entity unique_id uses `load_weight_recent` in `number.py`
- Entity unique_id uses `solar_weighted_avg_fit` in `sensor.py`

The documentation (`docs/ENTITY_REFERENCE.md`) already uses the correct entity IDs.

---

## Affected Files

- `dashboards/amber_powerwall_component.yaml` (lines with entity references)

---

## Steps to Reproduce

1. Navigate to Settings → Devices & Services → Amber Powerwall
2. Click "Configure" to open the config flow
3. Observe the "recreate entity ids" option showing 2 entities will be renamed
4. Dashboard may show unknown values for these entities

---

## Proposed Solution

**Two-step process:**

### Step 1: User clicks "recreate entity ids" in Home Assistant
- This will rename the entities to match what the code expects:
  - `number.amber_powerwall_load_recent_weight` → `number.amber_powerwall_load_weight_recent`
  - `sensor.amber_powerwall_solar_weighted_average_fit` → `sensor.amber_powerwall_solar_weighted_avg_fit`

### Step 2: Update dashboard file to use correct entity IDs
- Replace `number.amber_powerwall_load_recent_weight` with `number.amber_powerwall_load_weight_recent`
- Replace `sensor.amber_powerwall_solar_weighted_average_fit` with `sensor.amber_powerwall_solar_weighted_avg_fit` (2 occurrences)

---

## Notes

- **IMPORTANT:** The dashboard currently uses the OLD (incorrect) entity IDs to match the user's current installation
- After user clicks "recreate entity ids", the entities will be renamed to match the code
- Then the dashboard needs to be updated to use the new (correct) entity IDs
- This is a two-part fix: first Home Assistant fixes the entities, then we fix the dashboard

---

## Related Items

- backlog-high-015: Solar FIT Sensor Shows `unknown` State (potentially related symptom)