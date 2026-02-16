# Force Charge Detection Logic Bug

**ID:** backlog-crit-001  
**Priority:** CRIT  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

The force charge detection logic in coordinator.py uses incorrect OR operator, making it always true when in "backup" mode.

---

## Description

The force charge detection logic is incorrect:
```python
d.force_charge_active = d.operation_mode == "backup" or (
    d.operation_mode == "autonomous" and d.backup_reserve > 99
)
```

The OR operator makes this always true when in "backup" mode. The logic is meant to detect:
- "backup" mode (3.3kW force charge)
- "autonomous" mode with reserve > 99 (5kW boost charge)

However, the OR structure doesn't properly distinguish between these two states.

---

## Affected Files

- `custom_components/amber_powerwall/coordinator.py` (lines ~440-447)

---

## Proposed Solution

Change to:
```python
d.force_charge_active = (
    d.operation_mode == "backup" or 
    (d.operation_mode == "autonomous" and d.backup_reserve > 99)
)
```

---

## Notes

This is a logic error that affects the core functionality of force charging the battery.
