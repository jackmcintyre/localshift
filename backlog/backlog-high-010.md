# Pyright Error: Missing _manual_override_set_at Attribute

**ID:** backlog-high-010  
**Priority:** HIGH  
**Status:** COMPLETED
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Button entities attempt to set `_manual_override_set_at` attribute on coordinator that doesn't exist, causing pyright type errors.

---

## Description

In `button.py`, three button classes (ForceChargeButton, ForceDischargeButton, BoostChargeButton) attempt to set:

```python
self.coordinator._manual_override_set_at = dt_util.now()
```

However, the `AmberPowerwallCoordinator` class does not have this attribute defined, causing pyright errors:

```
Cannot assign to attribute "_manual_override_set_at" for class "AmberPowerwallCoordinator"
Attribute "_manual_override_set_at" is unknown
```

### Root Cause

Looking at coordinator.py, there's a method `async_set_manual_override()` that calls:
```python
self._state_machine.set_manual_override_timestamp()
```

This suggests the timestamp is tracked in the state machine, not the coordinator. The button code is using a non-existent attribute.

---

## Affected Files

- `custom_components/amber_powerwall/button.py` (lines 92, 110, 128)
- `custom_components/amber_powerwall/coordinator.py` (may need to add attribute or refactor)

---

## Proposed Solution

Either:
1. Add `_manual_override_set_at` attribute to `AmberPowerwallCoordinator.__init__()` and update logic to use it, OR
2. Refactor buttons to use `async_set_manual_override()` method instead of directly setting the timestamp, OR
3. Add a public method like `coordinator.set_manual_override()` that handles the timestamp internally

---

## Notes

This causes pre-commit failures and prevents the codebase from passing all type checks. The functionality may still work at runtime if the state machine handles the override tracking, but the code is accessing non-existent attributes.