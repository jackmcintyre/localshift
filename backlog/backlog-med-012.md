# Backlog Item Template

**ID:** backlog-med-012  
**Priority:** MED  
**Status:** COMPLETED  
**Created:** 2026-02-18  
**Updated:** 2026-02-19

---

## Summary

Binary sensors incorrectly include "binary" in entity names and unique_ids, violating Home Assistant naming conventions.

---

## Description

All 8 binary sensors in the LocalShift integration have redundant "binary" in their names:

**Current (Incorrect):**
- Unique IDs: `localshift_binary_demand_window`, `localshift_binary_price_spike_coming`, etc.
- Friendly names: `"Binary Demand Window"`, `"Binary Price Spike Coming"`, etc.

**Expected (Per HA Conventions):**
- Unique IDs: `localshift_demand_window`, `localshift_price_spike_coming`, etc.
- Friendly names: `"Demand Window"`, `"Price Spike Coming"`, etc.

The `binary_sensor.` prefix already indicates the entity type, making "binary" in the name redundant. This is a code quality issue that affects user experience with unnecessarily verbose entity names.

---

## Affected Files

- `custom_components/localshift/binary_sensor.py` - All 8 sensor classes need unique_id and name updates
- `docs/ENTITY_REFERENCE.md` - Documentation lists incorrect entity IDs and names

---

## Steps to Reproduce (for bugs)

1. Check Home Assistant entity registry
2. Observe binary sensor entities have redundant "binary" in names
3. Compare to standard HA naming: `binary_sensor.basement_motion` not `binary_sensor.basement_binary_motion`

---

## Proposed Solution

Remove "binary_" from all unique_ids and "Binary " from all names:

```python
# Before
class DemandWindowActiveSensor(LocalShiftBinarySensorBase):
    _attr_unique_id = "localshift_binary_demand_window"
    _attr_name = "Binary Demand Window"

# After
class DemandWindowActiveSensor(LocalShiftBinarySensorBase):
    _attr_unique_id = "localshift_demand_window"
    _attr_name = "Demand Window"
```

Apply to all 8 sensors:
1. `localshift_binary_demand_window` → `localshift_demand_window`
2. `localshift_binary_price_spike_coming` → `localshift_price_spike_coming`
3. `localshift_binary_price_expensive_coming` → `localshift_price_expensive_coming`
4. `localshift_binary_discharge_forced` → `localshift_discharge_forced`
5. `localshift_binary_charge_forced` → `localshift_charge_forced`
6. `localshift_binary_charge_boost` → `localshift_charge_boost`
7. `localshift_binary_solar_can_reach_target` → `localshift_solar_can_reach_target`
8. `localshift_binary_charge_boost_needed` → `localshift_charge_boost_needed`

---

## Notes

**Breaking Change Consideration:** This will create new entity IDs. Users may need to update dashboards/automations. Consider if a migration path is needed.

---

## Related Items

- backlog-med-010 - Category-Based Entity Naming Conventions (completed)