# Configurable Minimum Target SOC for Proactive Export

**ID:** backlog-high-012  
**Priority:** HIGH  
**Status:** COMPLETED
**Created:** 2026-02-16  
**Updated:** 2026-02-17  

---

## Summary

Add a configurable minimum target SOC threshold to control when proactive export is allowed

---

## Description

The proactive export logic had a hard-coded 20% minimum SOC threshold that would block exports even when conditions were favorable (positive FIT, forecast showing 100% SOC before demand window). This adds a user-configurable threshold via the options flow, making the behavior more flexible.

### Why This Matters

When SOC is exactly at the hard-coded threshold (e.g., 20%), proactive export would be blocked even if:
- FIT price is positive and optimal
- Forecast shows the battery will reach 100% SOC well before the demand window
- The user wants to export more aggressively

### Implementation Approach

Uses a direct options flow configuration (slider) rather than requiring users to create a separate entity. This is simpler and more user-friendly.

### Behavior

The minimum target SOC is used as a floor in the proactive export logic:
- Proactive export is only allowed if `predicted_soc > minimum_target_soc`
- Default value: 20%
- Configurable range: 5% - 30%

---

## Affected Files

- `custom_components/amber_powerwall/const.py` - Add CONF_MINIMUM_TARGET_SOC and DEFAULT_MINIMUM_TARGET_SOC
- `custom_components/amber_powerwall/config_flow.py` - Add NumberSelector to options flow
- `custom_components/amber_powerwall/sensor.py` - Add MinimumTargetSOCSensor to expose value
- `custom_components/amber_powerwall/computation_engine_lib/forecast_computer.py` - Use configured value instead of hard-coded 20%

---

## Implementation Details

### 1. Configuration (const.py)

```python
CONF_MINIMUM_TARGET_SOC = "minimum_target_soc"
DEFAULT_MINIMUM_TARGET_SOC = 20  # % minimum SOC for discharge modes
```

### 2. Options Flow (config_flow.py)

Add NumberSelector with:
- Range: 5% - 30%
- Step: 1%
- Default: 20%

### 3. Sensor (sensor.py)

New `MinimumTargetSOCSensor` entity:
- Unique ID: `minimum_target_soc`
- Name: "Minimum Target SOC"
- Icon: `mdi:battery-charging-20`
- Unit: `%`

### 4. Forecast Computer (forecast_computer.py)

- Remove hard-coded `export_min_soc_pct = 20.0`
- Read value from config options
- Pass to `_should_proactive_export_at_slot()`

---

## User Configuration

1. Go to integration settings → Configure
2. Adjust "Minimum Target SOC" slider (5-30%)
3. Monitor the "Minimum Target SOC" sensor to verify the current value

---

## Related Items

- backlog-high-008: Proactive Export Not Using Peak FIT Prices (related feature)
- Issue investigation: Proactive export not activating at ~20% SOC with positive FIT
