# Backlog Item: Grid Import/Export Totals Always Zero in Debug Forecast

**ID:** backlog-high-014
**Priority:** HIGH
**Status:** ✅ COMPLETED
**Created:** 2026-02-16
**Updated:** 2026-02-16

---

## Summary

Debug forecast dashboard shows "Total Grid Import: 0 kWh" and "Total Grid Export: 0 kWh" due to a key name mismatch in `sensor.py`.

---

## Description

When building the `debug_15min` slot list, the grid import/export values are renamed from `"grid_import_kwh"` / `"grid_export_kwh"` to shortened keys `"grid_in"` / `"grid_out"` (lines 314-315). However, the total calculation on lines 322-323 still references the original key names (`"grid_import_kwh"` / `"grid_export_kwh"`), which no longer exist in the `debug_15min` dicts.

This means `slot.get("grid_import_kwh", 0)` and `slot.get("grid_export_kwh", 0)` always return the default `0`, producing totals of 0 even when individual slots have non-zero grid values.

The dashboard template reads these totals from sensor attributes:
```yaml
Total Grid Import: {{ state_attr('sensor.amber_powerwall_daily_forecast', 'debug_total_grid_import_kwh') }}
Total Grid Export: {{ state_attr('sensor.amber_powerwall_daily_forecast', 'debug_total_grid_export_kwh') }}
```

---

## Affected Files

- `custom_components/amber_powerwall/sensor.py` (lines 314-315, 322-323)

---

## Steps to Reproduce

1. Open the debug forecast dashboard in Home Assistant
2. Observe individual slots have non-zero `grid_out` values
3. Observe "Total Grid Import: 0 kWh" and "Total Grid Export: 0 kWh" at the top

---

## Proposed Solution

Change lines 322-323 to use the renamed keys that match the `debug_15min` dict structure:

```python
total_grid_import = sum(slot.get("grid_in", 0) or 0 for slot in debug_15min)
total_grid_export = sum(slot.get("grid_out", 0) or 0 for slot in debug_15min)
```

The `or 0` handles the case where the value is `None` (since `slot.get("grid_in")` has no default on line 314).

---

## Notes

- This is a display-only bug — it does not affect the actual forecast computation or automation decisions
- The individual slot data is correct; only the summary totals are wrong
- The `or 0` guard is needed because lines 314-315 use `slot.get()` without a default value, so `None` values could appear if the source data is missing

---

## Related Items

- None
