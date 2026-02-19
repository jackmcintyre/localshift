# Missing Cleanup for Historical Load Cache

**ID:** backlog-med-004  
**Priority:** MED  
**Status:** COMPLETED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Historical load cache isn't explicitly cleaned up on coordinator shutdown.

---

## Description

The historical load cache is cleared at midnight but never explicitly cleaned up on coordinator shutdown. While not critical (memory will be freed on process exit), it's good practice for clean shutdown.

---

## Affected Files

- `custom_components/amber_powerwall/coordinator.py` (async_stop method)

---

## Proposed Solution

Add cache cleanup in `async_stop`:
```python
self._historical_load_cache.clear()
self._historical_load_cache_date = ""
```

---

## Notes

Good practice for clean shutdown.
