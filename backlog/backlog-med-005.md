# Unused Config Option - ALLOW_EXPORT

**ID:** backlog-med-005  
**Priority:** MED  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

CONF_TESLEMETRY_ALLOW_EXPORT is collected but cannot be reconfigured after setup.

---

## Description

`CONF_TESLEMETRY_ALLOW_EXPORT` is collected in config flow but:
- There's NO way to reconfigure it after setup
- It's hardcoded with default in DEFAULT_ENTITY_IDS
- The integration changes it programmatically (so user shouldn't be setting it)

---

## Affected Files

- `custom_components/localshift/config_flow.py` (async_step_user)

---

## Proposed Solution

Either:
- Remove from config flow (integration manages it), OR
- Add to config flow as optional entity that can be reconfigured

---

## Notes

This is a configuration management issue.
