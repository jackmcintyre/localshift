# Arbitrary Sleep Delays Between Commands

**ID:** backlog-high-005  
**Priority:** HIGH  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Hardcoded 5-second sleep delays between commands are arbitrary and not optimal.

---

## Description

Uses hardcoded `await asyncio.sleep(5)` between each command. This is arbitrary and:
- May be too long (causing unnecessary delays in mode transitions)
- May be too short (race conditions if Powerwall hasn't processed previous command)
- No verification that command actually succeeded

---

## Affected Files

- `custom_components/amber_powerwall/coordinator.py` (async_set_self_consumption, async_set_hold, etc.)

---

## Proposed Solution

- Make delays configurable via options (default 5s)
- OR implement proper state verification (wait for mode change confirmation via _read_state)
- OR reduce to 2s and add retry logic if mode doesn't change

---

## Notes

This affects user experience during mode transitions.
