# Backlog Item: Dashboard Template Shows `None` for Solar Remaining

**ID:** backlog-low-005  
**Priority:** LOW  
**Status:** COMPLETED
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Debug dashboard shows `Solar Remaining: None kWh` because the Jinja `| default(0)` filter doesn't replace explicit `None` values.

---

## Description

The debug summary dashboard template has this line:

```jinja
Solar Remaining: {{ state_attr('sensor.amber_powerwall_solar_weighted_avg_fit', 'total_solar_remaining_kwh') | default(0) }} kWh
```

When the `solar_weighted_avg_fit` sensor is in `unknown` state (see backlog-high-015), `state_attr()` returns `None`. The Jinja `| default(0)` filter only replaces **undefined** values, not explicit `None`. So the output renders as `None kWh` instead of `0 kWh`.

This same pattern may affect other `| default()` usages in the debug template.

---

## Affected Files

- `dashboards/amber_powerwall_component.yaml` — Debug State Summary markdown template

---

## Steps to Reproduce

1. Open the Debug dashboard view
2. Look at `Solar Remaining:` line
3. Observe it shows `None kWh` instead of `0 kWh`

---

## Proposed Solution

Replace `| default(0)` with either:

1. `| default(0, true)` — the second parameter makes `default()` also replace `None`, `False`, and empty strings
2. `| float(0)` — converts to float with 0 as fallback for non-numeric values

Option 2 is preferred as it's more explicit and handles any non-numeric edge case:

```jinja
Solar Remaining: {{ state_attr('sensor.amber_powerwall_solar_weighted_avg_fit', 'total_solar_remaining_kwh') | float(0) }} kWh
```

Should also audit all other `| default()` usages in the debug template for the same issue.

---

## Notes

- This is a cosmetic dashboard issue, not a functional bug
- Fixing backlog-high-015 (sensor showing `unknown`) would also resolve this indirectly
- Similar pattern may exist in the Solar Plan card and Cost card templates

---

## Related Items

- backlog-high-015 (Solar FIT Sensor Shows `unknown` State — root cause)
- backlog-high-011 (Template Error: None Values in Cost Sensor Attributes)