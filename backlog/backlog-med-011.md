# Backlog Item: Remove Redundant Grid Import/Export Sensors

**ID:** backlog-med-011  
**Priority:** MED  
**Status:** PROPOSED  
**Created:** 2026-02-18  
**Updated:** 2026-02-18  

---

## Summary

Consider removing `localshift_power_grid_import` and `localshift_power_grid_export` sensors and referencing Teslemetry entities directly.

---

## Description

The `localshift_power_grid_import` and `localshift_power_grid_export` sensors are thin wrappers that simply split the signed `grid_power_kw` value into positive import/export values:

- `grid_import_power_kw = max(data.grid_power_kw, 0.0)` 
- `grid_export_power_kw = max(-data.grid_power_kw, 0.0)`

The underlying `grid_power_kw` is already read from Teslemetry entities. If Teslemetry provides separate import/export entities, or if the signed grid power value is sufficient for dashboard/automation purposes, these LocalShift sensors add unnecessary indirection and code complexity.

---

## Affected Files

- `custom_components/localshift/sensor.py` - GridImportPowerSensor and GridExportPowerSensor classes
- `custom_components/localshift/coordinator_data.py` - grid_import_power_kw and grid_export_power_kw fields
- `custom_components/localshift/computation_engine.py` - where these values are computed
- `dashboards/localshift.yaml` - may reference these entities

---

## Steps to Evaluate

1. Identify the exact Teslemetry entity IDs for grid power (import/export or signed)
2. Check if Teslemetry provides separate grid import/export power entities
3. Search for references to `sensor.localshift_power_grid_import` and `sensor.localshift_power_grid_export` in dashboards and automations
4. Evaluate if removing these sensors would break any functionality

---

## Proposed Solution

**Option A:** If Teslemetry has separate import/export entities:
- Remove the LocalShift sensors entirely
- Update dashboard to reference Teslemetry entities directly
- Remove the computed fields from coordinator_data

**Option B:** If Teslemetry only has signed grid power:
- Consider keeping these as convenient derived values, OR
- Use template sensors in the dashboard instead

**Option C:** If these provide value for abstraction:
- Keep as-is but document why they exist

---

## Notes

- This is a code quality/maintainability improvement, not critical functionality
- Consider backward compatibility for users who reference these entities
- The notification_service.py uses different sensors (`sensor.grid_import_energy_daily`) which are separate

---

## Related Items

- None