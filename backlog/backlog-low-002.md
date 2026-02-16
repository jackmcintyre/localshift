# Hardcoded Personal Notification Service

**ID:** backlog-low-002  
**Priority:** LOW  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Default notification service uses hardcoded personal device ID.

---

## Description

`notify.mobile_app_jacks_iphone` is hardcoded with personal user's device ID. Every user who installs this will see this default, which won't work for them.

---

## Affected Files

- `custom_components/amber_powerwall/const.py` (DEFAULT_ENTITY_IDS)

---

## Proposed Solution

- Use more generic default like "notify.mobile_app" or make it required without default, OR
- Detect available notify services and use first one as default

---

## Notes

This affects new user setup.
