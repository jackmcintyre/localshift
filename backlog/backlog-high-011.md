# Template Error: None Values in Cost Sensor Attributes

**ID:** backlog-high-011  
**Priority:** HIGH  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Template rendering error occurs when dashboard template tries to access cost sensor attributes that return None values, causing Home Assistant to fail with "round got invalid input 'None'" error.

---

## Description

The dashboard template for `sensor.amber_powerwall_net_electricity_cost_today` fails to render with the following error:

```
Template error: round got invalid input 'None' when rendering template '{% set cost = 'sensor.amber_powerwall_net_electricity_cost_today' %} **Net: ${{ states(cost) }}** Import: ${{ state_attr(cost, 'grid_import_cost') | default(0) | round(2) }} Export: ${{ state_attr(cost, 'grid_export_revenue') | default(0) | round(2) }} Savings: ${{ state_attr(cost, 'battery_savings') | default(0) | round(2) }} Charge: ${{ state_attr(cost, 'battery_charge_cost') | default(0) | round(2) }}' but no default was specified
```

The template uses `default(0)` filters but still receives None values for attributes like `grid_import_cost`, `grid_export_revenue`, `battery_savings`, and `battery_charge_cost`.

---

## Affected Files

- `custom_components/amber_powerwall/sensor.py` (lines 277-285) - NetElectricityCostSensor
- `custom_components/amber_powerwall/coordinator_data.py` (lines 88-91) - Cost accumulator initialization
- `custom_components/amber_powerwall/cost_tracker.py` - Cost accumulation logic
- Dashboard template using the cost sensor

---

## Steps to Reproduce

1. Install the Amber Powerwall integration
2. Access the dashboard that displays cost information
3. Observe the template rendering error in Home Assistant logs
4. The error occurs when the template tries to render cost attributes

---

## Root Cause Analysis

### Investigation Findings

1. **Data Flow**: Cost values are stored in `CoordinatorData` and exposed via `NetElectricityCostSensor.extra_state_attributes`
2. **Initialization**: Cost accumulators are initialized to `0.0` in `CoordinatorData` but may not be set before first sensor render
3. **Timing Issue**: Template may render before coordinator completes first update cycle
4. **Race Condition**: Daily reset at midnight could cause temporary None values during reset
5. **Template Filter Issue**: Home Assistant's `forgiving_round` may not properly handle `default(0)` with `state_attr()`

### Potential Causes

1. **Sensor Initialization Race**: Sensor created before coordinator data is fully initialized
2. **Coordinator Update Timing**: Template renders during coordinator update cycle
3. **Daily Reset Race**: Reset occurs while template is being rendered
4. **Template Syntax**: `default(0)` filter may not work as expected with `state_attr()`

---

## Proposed Solutions

### Option A: Defensive Programming in Sensor (Recommended)

Add null checks in `extra_state_attributes` to ensure 0.0 is always returned:

```python
@property
def extra_state_attributes(self) -> dict[str, Any]:
    d = self.coordinator.data
    return {
        "grid_import_cost": round(d.grid_import_cost or 0.0, 2),
        "grid_export_revenue": round(d.grid_export_revenue or 0.0, 2),
        "battery_savings": round(d.battery_savings or 0.0, 2),
        "battery_charge_cost": round(d.battery_charge_cost or 0.0, 2),
    }
```

**Pros:**
- Simple, robust solution
- Handles all timing issues
- No template changes required
- Follows defensive programming principles

**Cons:**
- Doesn't address root cause of None values

### Option B: Ensure Early Initialization

Modify coordinator to ensure cost values are set before any sensor updates:

```python
def __post_init__(self):
    # Ensure cost values are initialized before any updates
    self.grid_import_cost = 0.0
    self.grid_export_revenue = 0.0
    self.battery_savings = 0.0
    self.battery_charge_cost = 0.0
```

**Pros:**
- Addresses root cause
- Ensures consistent initialization

**Cons:**
- May not handle race conditions during daily reset
- Requires more extensive changes

### Option C: Template Fix

Update dashboard template to use more robust null handling:

```jinja2
{% set cost = 'sensor.amber_powerwall_net_electricity_cost_today' %}
**Net: ${{ states(cost) | default(0) | round(2) }}**
Import: ${{ (state_attr(cost, 'grid_import_cost') | default(0) | float) | round(2) }}
Export: ${{ (state_attr(cost, 'grid_export_revenue') | default(0) | float) | round(2) }}
Savings: ${{ (state_attr(cost, 'battery_savings') | default(0) | float) | round(2) }}
Charge: ${{ (state_attr(cost, 'battery_charge_cost') | default(0) | float) | round(2) }}
```

**Pros:**
- No code changes required
- Addresses template-specific issues

**Cons:**
- Doesn't fix underlying sensor issue
- Template becomes more complex
- May not work in all Home Assistant versions

---

## Investigation Steps

1. **Add Debug Logging**: Add logging to `NetElectricityCostSensor._update_from_coordinator()` to track when attributes are set
2. **Monitor Timing**: Check if error occurs during startup or daily reset
3. **Test Race Conditions**: Simulate rapid coordinator updates during reset
4. **Verify Template**: Test if `default(0)` works correctly with `state_attr()` in isolation
5. **Check Coordinator State**: Verify `CoordinatorData` cost values are never None

---

## Notes

- This is a HIGH priority issue as it affects dashboard display and user experience
- The error appears in logs but may not crash the integration
- Multiple users may be affected by this template rendering issue
- Should be fixed before any production deployment

---

## Related Items

- None currently