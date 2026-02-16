# Notification Service Not Validated

**ID:** backlog-high-004  
**Priority:** HIGH  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

The notify service configuration accepts any text input without validating the service exists.

---

## Description

`CONF_NOTIFY_SERVICE` accepts any text input without validating the notify service exists. Invalid service will cause notification failures at runtime with no warning during setup.

---

## Affected Files

- `custom_components/amber_powerwall/config_flow.py` (async_step_solcast)

---

## Proposed Solution

Use entity selector for notify services or validate against available notify services:
```python
from homeassistant.components import notify
available_services = notify.async_get_services(hass)
```

---

## Notes

Related to backlog-high-001 (Entity validation)
