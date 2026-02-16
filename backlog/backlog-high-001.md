# Add Entity Validation on Config Flow

**ID:** backlog-high-001  
**Priority:** HIGH  
**Status:** COMPLETED  
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

## Implementation

Added comprehensive entity validation to config flow:

### 1. Added `_validate_entities()` method
Validates that entities:
- Exist in Home Assistant's state registry
- Are available (not "unavailable" or "unknown")
- Have the correct domain (sensor, number, select, binary_sensor, sun)

### 2. Added `_validate_notify_service()` method
Validates that notify service:
- Is not empty
- Starts with "notify."
- Has valid format (domain.service_name)
- Exists in Home Assistant's service registry

### 3. Applied validation to all three config flow steps:
- **async_step_user**: Validates all 8 Teslemetry entities
- **async_step_amber**: Validates all 5 Amber entities
- **async_step_solcast**: Validates 2 Solcast entities, sun entity, and notify service

### 4. Error handling
- User-friendly error messages displayed inline on form fields
- Form re-displays with user's input when validation fails
- Errors indicate specific issues (entity doesn't exist, wrong domain, unavailable, etc.)

### Files Modified
- `custom_components/amber_powerwall/config_flow.py`: Added validation methods and integrated into flow steps

### Testing
- All pre-commit hooks pass (ruff, ruff-format, vulture)
- Note: Pre-existing pyright errors in button.py are unrelated to this change
