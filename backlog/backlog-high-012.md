# Configurable Grid Export Reserve via Minimum Target SOC Entity

**ID:** backlog-high-012  
**Priority:** HIGH  
**Status:** COMPLETED
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Use a dedicated "Minimum Target SOC" entity to control reserve % during export modes

---

## Description

Instead of trying to detect/cache the default backup reserve value (which is unreliable), use a dedicated entity that the user creates and controls. The user creates a Number entity in Home Assistant, and the system reads its value to determine the reserve percentage during export modes.

This is more reliable because:
- User has full control over the value
- No startup detection needed
- Clear separation between "minimum target" and actual backup reserve

### Behavior

| Mode | Reserve Formula | Example (min_target=10%) |
|------|-----------------|------------------------|
| SPIKE_DISCHARGE | min_target | 10% |
| PROACTIVE_EXPORT | min_target | 10% |
| All others | Read from entity | User-defined |

---

## Affected Files

- `custom_components/amber_powerwall/const.py` - Add config key for entity
- `custom_components/amber_powerwall/config_flow.py` - Add entity selector
- `custom_components/amber_powerwall/strings.json` - Add entity description
- `custom_components/amber_powerwall/battery_controller.py` - Read entity value for reserve

---

## Implementation

### 1. Add Config Entity (const.py)

```python
# New entity config key
CONF_MINIMUM_TARGET_SOC = "minimum_target_soc"

# Default entity ID
DEFAULT_ENTITY_IDS = {
    ...
    CONF_MINIMUM_TARGET_SOC: "number.my_home_minimum_target_soc",
}
```

### 2. Add to Config Flow (config_flow.py)

Add EntitySelector for the new number entity (required entity).

### 3. Battery Controller (battery_controller.py)

```python
# Read the minimum target SOC entity value
def _get_minimum_target_soc(self) -> float:
    """Read the minimum target SOC from entity."""
    entity_id = self._get_entity_id("minimum_target_soc")
    return self._read_float(entity_id, default=10.0)

# In set_force_discharge() and set_proactive_export():
minimum_target = self._get_minimum_target_soc()
await self._set_backup_reserve(minimum_target)

# In set_self_consumption():
# Restore to minimum target (or could read backup_reserve entity)
await self._set_backup_reserve(self._get_minimum_target_soc())
```

---

## User Setup

1. User creates a generic "Number" helper in Home Assistant called "Minimum Target SOC"
2. User sets their desired minimum (e.g., 10%, 15%, 20%)
3. User selects this entity during integration setup
4. System reads this value during export modes to set reserve

---

## Related Items

- backlog-high-008: Proactive Export Not Using Peak FIT Prices (related feature)
