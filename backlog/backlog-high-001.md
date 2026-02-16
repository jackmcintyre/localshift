# Add Entity Validation on Config Flow

**ID:** backlog-high-001  
**Priority:** HIGH  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Add validation that selected entities exist and are from correct integrations in the config flow.

---

## Description

No validation that selected entities exist or are from correct integrations. Could fail silently with runtime errors.

---

## Affected Files

- `custom_components/amber_powerwall/config_flow.py`

---

## Proposed Solution

Add validation step that checks entity domains and availability before proceeding. Use async_validate_step or custom validators.

---

## Notes

Related to backlog-high-004 (Notification Service validation)
