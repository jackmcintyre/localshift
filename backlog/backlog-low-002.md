# Hardcoded Personal Notification Service

**ID:** backlog-low-002  
**Priority:** LOW  
**Status:** COMPLETED  
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
- `custom_components/amber_powerwall/config_flow.py`

---

## Implemented Solution

Dynamic detection of available notify services using dropdown selector:

1. **Added `_get_notify_services()` method** in config_flow.py to dynamically query available notify services from Home Assistant
2. **Changed from TextSelector to SelectSelector** - users now see a dropdown of available notify services
3. **Removed hardcoded default** from const.py DEFAULT_ENTITY_IDS
4. **Smart default** - defaults to first available notify service, or empty if none available

---

## Notes

This affects new user setup. Existing users retain their configured notify service.
