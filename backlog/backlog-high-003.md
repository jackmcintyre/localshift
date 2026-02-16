# No Error Handling for Powerwall API Calls

**ID:** backlog-high-003  
**Priority:** HIGH  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

API calls to Powerwall lack proper error handling, risking crashes and undefined battery states.

---

## Description

These methods make async service calls with no try/except blocks. If Teslemetry API is down or slow, exceptions propagate and may:
- Crash the coordinator
- Leave battery in undefined state
- Cause no notifications of failure

---

## Affected Files

- `custom_components/amber_powerwall/coordinator.py` - `_set_export_mode`, `_set_operation_mode`, `_set_backup_reserve` methods

---

## Proposed Solution

Wrap all service calls in try/except with logging:
```python
try:
    await self.hass.services.async_call(...)
    _LOGGER.debug("Command %s successful", command)
except Exception as e:
    _LOGGER.error("Failed to execute %s: %s", command, e)
    # Optionally retry or notify user
```

---

## Notes

This is a reliability improvement.
