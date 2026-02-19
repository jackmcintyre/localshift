# Forecast SOC Stays Flat at Minimum Despite Solar Excess

**ID:** backlog-high-022
**Priority:** HIGH
**Status:** COMPLETED
**Created:** 2026-02-19
**Updated:** 2026-02-19

---

## Summary

Forecast SOC stays flat at minimum level even when solar production exceeds consumption, preventing proper battery charging from solar excess.

---

## Description

When the battery SOC drops to the minimum threshold (20%), the forecast correctly clamps SOC to minimum and models grid import for any shortfall. However, if solar production exceeds consumption (creating excess solar), the forecast fails to apply this excess to charge the battery above minimum SOC.

This causes the SOC to stay flat at minimum level throughout the day, even when solar production is strong enough to charge the battery. The excess solar energy is "lost" in the forecast simulation.

**Example from user data:**
- At 10:55, SOC = 10.0%, Solar = 0.377 kWh, Load = 0.375 kW (≈0.094 kWh)
- Net solar = 0.377 - 0.094 = 0.283 kWh excess
- SOC should increase, but stays flat at 10.0%

---

## Affected Files

- `custom_components/localshift/computation_engine_lib/forecast_computer.py` (lines 1763-1770)

---

## Steps to Reproduce

1. Set battery to minimum SOC (20%)
2. Have solar production exceed consumption
3. Observe that forecast SOC stays flat at minimum instead of increasing

---

## Proposed Solution

After clamping SOC to minimum and adding grid import for shortfall, check if there's excess solar energy and apply it to charge the battery (unless export is financially optimal):

```python
# After setting new_predicted_soc = export_min_soc_pct
# Check if solar excess can charge battery above minimum
if net_kwh > 0 and not in_demand_window:
    excess_kwh = net_kwh
    charge_delta = min(excess_kwh, max_solar_charge_kwh) * 0.92
    new_predicted_soc += charge_delta / BATTERY_CAPACITY_KWH * 100
    # Reduce grid import since solar is covering load
    grid_import_kwh = max(0, grid_import_kwh - consumption_kwh / 0.95)
```

---

## Notes

This bug was discovered during analysis of overnight grid charging behavior. The fix should prioritize charging the battery with solar excess at minimum SOC, unless the financial forecast indicates that exporting at current prices is optimal.

---

## Related Items

- Related to overnight grid charging analysis
- Affects the same forecast simulation logic as backlog-high-019