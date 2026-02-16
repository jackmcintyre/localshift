# Time Precision Inconsistency

**ID:** backlog-med-002  
**Priority:** MED  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Time comparison uses different precision between now and demand window times.

---

## Description

Uses `now_t = now_dt.time()` to compare against demand window times, which are parsed from strings like "15:00:00". The comparison `now_t >= dw_start_time` compares time objects with different precision (now_t includes microseconds, dw_start_time may or may not).

At the exact boundary (e.g., 14:59:59.999), the comparison may behave unexpectedly.

---

## Affected Files

- `custom_components/amber_powerwall/coordinator.py` (line ~415)

---

## Proposed Solution

Strip microseconds from now for comparison:
```python
now_t = now_dt.replace(microsecond=0).time()
```

---

## Notes

Edge case issue at exact boundary times.
