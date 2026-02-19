# Tests Pass with Mocks but Fail in Real HA

**ID:** backlog-med-018
**Priority:** MED
**Status:** PROPOSED
**Created:** 2026-02-19
**Updated:** 2026-02-19

---

## Summary

Test mocks don't simulate real HA entity states, potentially hiding bugs that only appear in production.

---

## Description

Current test fixtures use simplified mocks that don't match real HA behavior:

```python
# conftest.py - Oversimplified
@pytest.fixture
def coordinator_data():
    data = CoordinatorData()
    data.soc = 50.0  # Just a number
    data.operation_mode = "autonomous"  # Just a string
    # No simulation of HA entity state changes, availability, etc.
```

**Problems:**
- Tests pass with perfect data but fail with real HA entity states
- No testing of entity unavailability scenarios
- No testing of state transition edge cases
- Mocks don't simulate async HA behavior

---

## Affected Files

- `tests/conftest.py` - Inadequate fixtures
- `tests/test_*.py` - Tests that may not catch real-world issues

---

## Steps to Reproduce

1. Run tests - they pass
2. Deploy to real HA with entity availability issues
3. Observe different behavior than expected

---

## Proposed Solution

**Option A: Improve fixtures**
```python
@pytest.fixture
def mock_ha_entity_states():
    """Fixture that simulates real HA entity state behavior."""
    return {
        "sensor.powerwall_soc": {
            "state": "50.0",
            "attributes": {"unit_of_measurement": "%"},
            "available": True
        },
        # Include unavailable states, attribute variations, etc.
    }
```

**Option B: Integration tests**
Add tests that run against real HA instances with actual entities.

**Option C: Entity state simulation**
Create fixtures that simulate entity state changes, availability toggles, and error conditions.

---

## Notes

This is a testing quality issue. Better test coverage would catch issues before they reach production.

---

## Related Items

- Phase 6.1.3 in CODE_REVIEW_PLAN.md - Mock fixture issues