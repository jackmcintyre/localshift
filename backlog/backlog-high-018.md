# Backlog Item: Forecast SOC Simulation Does Not Respect Minimum SOC

**ID:** backlog-high-018  
**Priority:** HIGH  
**Status:** COMPLETED  
**Created:** 2026-02-18  
**Updated:** 2026-02-18  

---

## Summary

Forecast SOC simulation does not respect minimum SOC — battery should plateau at the minimum SOC floor and model the shortfall as grid imports.

---

## Description

In `compute_forecast()` and all sub-simulations, the rolling `predicted_soc` is clamped to `[0.0, 100.0]`. When the battery would discharge below `minimum_soc` (`CONF_MINIMUM_TARGET_SOC`, default 20%), the forecast continues to draw energy from the "virtual" battery below that floor.

In reality, the inverter stops discharging at the minimum SOC threshold and the remaining load deficit is served from the grid. The forecast therefore:

- **Under-reports grid imports** during overnight and low-solar periods.
- **Over-estimates remaining battery capacity** heading into the demand window.
- **Distorts grid-charge decisions** — `_simulate_future_soc_with_solar_only()` may incorrectly conclude solar alone is sufficient to reach the target because it lets the virtual battery drain past minimum SOC.
- **Distorts export safety checks** — `_simulate_minimum_soc_without_exports()` and `_simulate_overnight_drain_after_export()` may allow exports that leave insufficient reserve.

---

## Affected Files

- `custom_components/localshift/computation_engine_lib/forecast_computer.py`
  - `compute_forecast()` — main rolling SOC loop
  - `_simulate_future_soc_with_solar_only()` — used for grid-charge decisions
  - `_simulate_minimum_soc_without_exports()` — used for export safety checks
  - `_simulate_overnight_drain_after_export()` — used for overnight drain simulation

---

## Proposed Solution

### 1. `compute_forecast()` — main loop

When applying a negative `battery_delta_kwh` (discharge), clamp `predicted_soc` to `minimum_soc` (not `0.0`). If the SOC would have gone below `minimum_soc`, calculate the energy shortfall and add it to `grid_import_kwh` for that slot.

```python
# After computing battery_delta_kwh...
minimum_soc = float(
    self.entry.options.get(CONF_MINIMUM_TARGET_SOC, DEFAULT_MINIMUM_TARGET_SOC)
)

new_predicted_soc = predicted_soc + (battery_delta_kwh / BATTERY_CAPACITY_KWH * 100)

if new_predicted_soc < minimum_soc and battery_delta_kwh < 0:
    # Battery is hitting the floor — shortfall comes from grid
    actual_soc = minimum_soc
    deficit_pct = minimum_soc - new_predicted_soc
    shortfall_kwh = deficit_pct / 100 * BATTERY_CAPACITY_KWH
    grid_import_kwh += shortfall_kwh  # add to existing grid import for slot
else:
    actual_soc = new_predicted_soc

predicted_soc = max(minimum_soc, min(100.0, actual_soc))
```

### 2. Sub-simulations

In each of the three sub-simulation methods, replace:
```python
soc = max(0.0, min(100.0, soc))
```
with:
```python
soc = max(minimum_soc, min(100.0, soc))
```

The `minimum_soc` value should be passed in as a parameter (from the caller which already reads `CONF_MINIMUM_TARGET_SOC`), or read from `self.entry.options` within the method.

---

## Notes

- `CONF_MINIMUM_TARGET_SOC` / `DEFAULT_MINIMUM_TARGET_SOC` (20%) is already defined in `const.py` and used elsewhere in the codebase (e.g., `export_min_soc_pct` in the export logic). This change makes the forecast consistent with the same constraint.
- Care is needed in the first slot of `compute_forecast()` where `predicted_soc = current_soc` is preserved — if `current_soc < minimum_soc` (e.g., during an unusual discharge event), the forecast should not force-jump the SOC upward; only prevent further simulated discharge below the floor.
- Tests in `tests/test_forecast_computer.py` should be updated or added to assert that the SOC plateau and grid import attribution work correctly.

---

## Related Items

- `backlog-high-017` — Excess Solar Load Shifting Sensors (also forecast-adjacent)
