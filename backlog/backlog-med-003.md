# Decision Log Limited to 50 Entries

**ID:** backlog-med-003  
**Priority:** MED  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Decision log caps at 50 entries, discarding older history needed for debugging.

---

## Description

Decision log is capped at 50 entries, discarding older history:
```python
if len(d.decision_log) > 50:
    d.decision_log = d.decision_log[-50:]
```
For debugging issues that occurred hours ago, this may be insufficient. Users can't see what happened earlier in the day.

---

## Affected Files

- `custom_components/amber_powerwall/coordinator.py` (line ~975)

---

## Proposed Solution

- Increase limit to 100-200 entries, OR
- Implement time-based retention (keep last 24 hours), OR
- Make it configurable via options

---

## Notes

This affects debugging capabilities.
