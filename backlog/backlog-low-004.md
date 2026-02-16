# Missing Type Hints for Internal Methods

**ID:** backlog-low-004  
**Priority:** LOW  
**Status:** PROPOSED  
**Created:** 2026-02-16  
**Updated:** 2026-02-16  

---

## Summary

Many internal helper methods lack complete type hints.

---

## Description

Many internal helper methods lack complete type hints, making code harder to:
- Maintain
- Understand
- Use IDE auto-completion
- Catch type errors early

Examples:
- `_get_expected_load_kw` - no return type
- `_get_historical_hourly_averages` - no parameter types
- `_sum_solar_before_target` - incomplete types

---

## Affected Files

- `custom_components/amber_powerwall/coordinator.py`

---

## Proposed Solution

Add proper type annotations for all method parameters and return values:
```python
def _get_expected_load_kw(self, hours_to_target: float) -> float:
```

---

## Notes

Code quality improvement.
