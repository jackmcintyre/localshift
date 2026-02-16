# Backlog Item: Solar FIT Sensor Shows `unknown` State

**ID:** backlog-high-015  
**Priority:** HIGH  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

`sensor.amber_powerwall_solar_weighted_avg_fit` reports `unknown` instead of `0.0` at nighttime, causing the debug dashboard to show `$unknown/kWh`.

---

## Description

The debug summary shows `Solar Weighted Avg FIT: $unknown/kWh`. The underlying `_compute_solar_weighted_avg_fit()` method in `computation_engine.py` correctly defaults `solar_weighted_avg_fit` to `0.0` when there's no solar production (e.g., nighttime). However, the HA sensor entity itself is reporting its state as `unknown`.

This suggests either:
1. A sensor initialization race condition where the sensor reads `CoordinatorData` before the computation engine has run its first update cycle
2. The sensor's `_update_from_coordinator()` is called before `coordinator.data` is populated
3. An exception in `round(self.coordinator.data.solar_weighted_avg_fit, 4)` preventing the state from being set

The `CoordinatorData` dataclass defaults `solar_weighted_avg_fit: float = 0.0`, so the value should never be `None` or `unknown` under normal operation.

---

## Affected Files

- `custom_components/amber_powerwall/sensor.py` — `SolarWeightedAvgFITSensor._update_from_coordinator()`
- `custom_components/amber_powerwall/coordinator.py` — sensor initialization timing
- `custom_components/amber_powerwall/computation_engine.py` — `_compute_solar_weighted_avg_fit()` (lines ~591-634)

---

## Steps to Reproduce

1. Restart Home Assistant or reload the integration
2. Observe `sensor.amber_powerwall_solar_weighted_avg_fit` state
3. Check if the sensor shows `unknown` instead of `0.0` at nighttime
4. Review debug summary in the dashboard

---

## Proposed Solution

Investigate the sensor initialization flow. Possible fixes:

1. Add defensive handling in the sensor to return `0.0` when coordinator data hasn't been populated yet
2. Ensure the computation engine runs before sensor entities are registered
3. Add a `@property` for `native_value` with a fallback:

```python
def _update_from_coordinator(self) -> None:
    try:
        self._attr_native_value = round(self.coordinator.data.solar_weighted_avg_fit, 4)
    except (TypeError, AttributeError):
        self._attr_native_value = 0.0
```

---

## Notes

- This also causes the cascading issue of `Solar Remaining: None kWh` in the debug dashboard (see backlog-low-005)
- The sensor's `extra_state_attributes` may also fail when the sensor is in `unknown` state

---

## Related Items

- backlog-low-005 (Dashboard Template Shows None for Solar Remaining)
- backlog-high-011 (Template Error: None Values in Cost Sensor Attributes)