# Backlog Item: `can_reach_target` Inconsistency Between Legacy and Detailed Forecast

**ID:** backlog-med-008  
**Priority:** MED  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

`solar_can_reach_target` binary sensor (`on`) disagrees with `solar_battery_forecast["can_reach_target"]` (`False`) in the debug output, causing confusion when debugging.

---

## Description

The debug summary shows two contradictory values:

```
Solar Can Reach Target: on          ← binary sensor (detailed 15-min forecast)
Can Reach Target: False             ← legacy forecast dict (solar-only calculation)
```

These are computed by two completely different methods with different logic:

### Binary Sensor: `solar_can_reach_target` (shows `on`)
- Set in `computation_engine.py` Step 5 (lines ~237-238)
- Derived from the **detailed 15-min forecast** which includes grid charging effects
- When `should_grid_charge` is True, it adds `grid_charge_amount` to `battery_delta_kwh`
- So this predicted SOC reflects solar **+ any planned grid top-ups**

### Legacy Forecast Dict: `can_reach_target` (shows `False`)
- Set in `computation_engine.py` `_compute_solar_battery_forecast()` (line ~388)
- Uses a simpler calculation: `can_reach = data.soc >= target_pct or net_solar >= deficit_kwh`
- `net_solar = solar_kwh - consumption_kwh` — pure solar minus load, **no grid charging**
- At 63.9% SOC vs 90% target with 0 solar at night, this is naturally `False`

The binary sensor is the authoritative value used by the state machine for decisions. The legacy dict is only for backward-compatible dashboard display. However, both appear in the debug summary without context, which is misleading.

---

## Affected Files

- `custom_components/amber_powerwall/computation_engine.py` — both `_compute_solar_battery_forecast()` and Step 5 (solar_can_reach_target)
- `dashboards/amber_powerwall_component.yaml` — debug summary template displays both values

---

## Steps to Reproduce

1. Open debug summary when SOC is below battery target
2. Observe `Solar Can Reach Target: on` (because grid charging is planned)
3. Observe `Can Reach Target: False` (because solar alone can't reach target)
4. Note the contradiction

---

## Proposed Solution

Options (in order of preference):

1. **Align the legacy calculation** — Make `_compute_solar_battery_forecast()` derive `can_reach_target` from the detailed 15-min forecast (same as the binary sensor), eliminating the inconsistency entirely

2. **Remove `can_reach_target` from the legacy dict** — Since the binary sensor is the authoritative source, remove the duplicate field to avoid confusion

3. **Add clarifying labels in debug** — Rename in the dashboard template to `Can Reach Target (solar only): False` and `Can Reach Target (with grid): on`

---

## Notes

- This is a cosmetic/debug-clarity issue — the state machine uses the correct binary sensor value
- The legacy forecast dict is used by the "Solar Plan" dashboard card, so option 1 or 2 could affect dashboard display
- Option 1 is cleanest since it makes both values consistent without breaking backwards compatibility

---

## Related Items

- None