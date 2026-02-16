# Solar Curtailment for Negative FIT

**ID:** backlog-high-009  
**Priority:** HIGH  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Implement solar curtailment to prevent exporting when FIT (feed-in tariff) is negative or zero.

---

## Description

When Amber Electric's feed-in tariff goes negative or zero, the system currently continues to export power (either through proactive export or normal self-consumption). This can result in:
- Paying to export solar energy (negative FIT)
- Exporting battery at negative prices

Teslemetry's `select.my_home_allow_export` entity supports a "Never" option that completely prevents all exports (both solar and battery). This should be used when FIT ≤ 0 to protect against negative export revenue.

---

## Affected Files

- `custom_components/amber_powerwall/const.py` - Add TESLEMETRY_EXPORT_NEVER constant
- `custom_components/amber_powerwall/computation_engine.py` - Add curtailment check in _compute_active_mode()
- `custom_components/amber_powerwall/battery_controller.py` - Add set_curtail() method
- `custom_components/amber_powerwall/state_machine.py` - Add CURTAIL mode handling
- `custom_components/amber_powerwall/config_flow.py` - Add curtail switch option
- `custom_components/amber_powerwall/coordinator_data.py` - Add curtailment_active flag

---

## Proposed Solution

### 1. Add new constant in const.py
```python
# Teslemetry export modes (select.allow_export options)
TESLEMETRY_EXPORT_PV_ONLY = "pv_only"
TESLEMETRY_EXPORT_BATTERY_OK = "battery_ok"
TESLEMETRY_EXPORT_NEVER = "never"  # NEW: Completely prevent exports
```

### 2. Add configuration option
Add a switch entity `switch.curtail_on_negative_fit` that users can enable/disable (default: ON).

### 3. Update computation_engine.py
In `_compute_active_mode()`, add curtailment check:
```python
# Check if curtailment should be active
curtail_enabled = self._get_switch_state("curtail_on_negative_fit")
if curtail_enabled and data.feed_in_price <= 0:
    data.active_mode = BatteryMode.CURTAIL
    data.curtailment_active = True
    return
```

### 4. Add set_curtail() method in battery_controller.py
```python
async def set_curtail(self, data: CoordinatorData, dry_run: bool = False) -> bool:
    """Set battery to curtail mode - prevent all exports when FIT is negative."""
    if dry_run:
        _LOGGER.info("DRY RUN: set_curtail")
        return True
    
    # Set allow_export to "never" to prevent all exports
    if not await self._set_export_mode(TESLEMETRY_EXPORT_NEVER):
        _LOGGER.error("Aborting curtail mode: Failed to set export mode")
        return False
    
    # Set to self_consumption mode
    if not await self._set_operation_mode("self_consumption"):
        _LOGGER.error("Aborting curtail mode: Failed to set operation mode")
        return False
    
    # Set backup reserve to 10%
    if not await self._set_backup_reserve(10):
        _LOGGER.error("Aborting curtail mode: Failed to set backup reserve")
        return False
    
    return True
```

### 5. Add CURTAIL mode to state_machine.py
Add handling in _execute_mode_transition() for BatteryMode.CURTAIL.

### 6. Add binary sensor
`binary_sensor.amber_powerwall_curtailment_active` - Shows ON when curtailment is active.

---

## Notes

- This is separate from the existing "proactive export" feature which exports battery BEFORE negative FIT periods to maximize revenue
- Curtailment is a safety measure to prevent ANY export when FIT is already negative/zero
- The proactive export feature should still run during positive FIT periods to maximize revenue before negative windows arrive
- Consider adding hysteresis (e.g., enable curtailment at FIT ≤ 0, disable at FIT > 0.02) to prevent rapid toggling

---

## Related Items

- backlog-high-008: Proactive Export Not Using Peak FIT Prices (related - both deal with FIT optimization)
