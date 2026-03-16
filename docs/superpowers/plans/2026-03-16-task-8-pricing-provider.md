# Task 8: Replace Pricing Source Block with Provider Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the if/else pricing_source block (lines 451-501 in reader.py) with a call to `self.pricing_provider.read_forecasts()`, eliminating direct pricing source coupling in StateReader.

**Architecture:** 
StateReader currently has a large if/else block that directly implements Amber Express vs standard provider logic. This task refactors that to delegate to the PricingProvider protocol, making StateReader agnostic to provider implementation. The pricing_provider is already instantiated in Coordinator and passed to StateReader in Task 7.

**Tech Stack:** 
- Python 3.13+ type hints
- Home Assistant testing utilities
- PricingProvider protocol (existing)

---

## Chunk 1: Write and Execute Tests

### Task 1.1: Test Provider Calls for Both Price Entities

**Files:**
- Modify: `tests/state/test_state_reader.py`

- [ ] **Step 1: Write test for general_forecast via provider**

Add this test at the end of `tests/state/test_state_reader.py`:

```python
def test_read_all_external_state_calls_provider_for_general_forecast(
    hass_mock: HomeAssistant,
    entry: ConfigEntry,
    state_reader_with_provider: StateReader,
) -> None:
    """Test StateReader calls pricing_provider.read_forecasts() for general price."""
    from unittest.mock import MagicMock
    from custom_components.localshift.pricing.types import ForecastSlot

    provider = MagicMock()
    provider.read_forecasts.return_value = [
        ForecastSlot(
            start_time=dt_util.now(),
            duration=30,
            per_kwh=0.25,
            is_spike=False,
            source_type="test_provider",
        )
    ]
    
    reader = StateReader(
        hass_mock,
        entry,
        validator=None,
        orchestrator=None,
        pricing_provider=provider,
    )
    
    # Mock entities
    hass_mock.states.async_set(
        "sensor.100h_general_price", "0.25", {"forecasts": []}
    )
    hass_mock.states.async_set(
        "sensor.100h_feed_in_price", "0.12", {"forecasts": []}
    )
    
    state = reader.read_all_external_state()
    
    # Verify provider was called for both price entities
    assert provider.read_forecasts.call_count == 2
    provider.read_forecasts.assert_any_call(hass_mock, "sensor.100h_general_price")
    provider.read_forecasts.assert_any_call(hass_mock, "sensor.100h_feed_in_price")


def test_read_all_external_state_uses_provider_forecast_data(
    hass_mock: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Test StateReader uses forecast data returned by provider."""
    from unittest.mock import MagicMock
    from custom_components.localshift.pricing.types import ForecastSlot

    provider = MagicMock()
    expected_forecast = [
        ForecastSlot(
            start_time=dt_util.now(),
            duration=30,
            per_kwh=0.30,
            is_spike=False,
            source_type="test_provider",
        )
    ]
    provider.read_forecasts.return_value = expected_forecast
    
    reader = StateReader(
        hass_mock,
        entry,
        validator=None,
        orchestrator=None,
        pricing_provider=provider,
    )
    
    # Mock entities
    hass_mock.states.async_set(
        "sensor.100h_general_price", "0.30", {"forecasts": []}
    )
    hass_mock.states.async_set(
        "sensor.100h_feed_in_price", "0.15", {"forecasts": []}
    )
    
    state = reader.read_all_external_state()
    
    # Verify forecasts came from provider
    assert state.general_forecast == expected_forecast
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /config/home/localshift/worktrees/issue-300 && pytest tests/state/test_reader.py::test_read_all_external_state_calls_provider_for_general_forecast -xvs`

Expected: FAIL with assertion about `read_forecasts` not being called or forecasts being empty.

- [ ] **Step 3: Write implementation - Replace the if/else block**

Modify `custom_components/localshift/state/reader.py` lines 451-501:

Replace:
```python
        # Issue #300: Read forecasts based on pricing source
        pricing_source = self.entry.data.get(
            CONF_PRICING_DATA_SOURCE, DEFAULT_PRICING_DATA_SOURCE
        )

        if pricing_source == PRICING_SOURCE_AMBER_EXPRESS:
            # Amber Express has _detailed entities with full forecast data (including spike_status)
            # Derive _detailed entity IDs from the configured price entities
            general_detailed = general_price_entity.replace("_price", "_price_detailed")
            feed_in_detailed = feed_in_price_entity.replace("_price", "_price_detailed")

            # Read from _detailed entities which have 'forecasts' attribute with full data
            general_forecast = self._read_attribute(general_detailed, "forecasts", [])
            feed_in_forecast = self._read_attribute(feed_in_detailed, "forecasts", [])

            # Fallback to simple entities if _detailed not available
            if not general_forecast:
                _LOGGER.warning(
                    "Amber Express detailed entity '%s' has no forecasts, "
                    "falling back to simple entity. Spike detection may be limited.",
                    general_detailed,
                )
                general_forecast = self._read_attribute(
                    general_price_entity, "forecast", []
                )
            if not feed_in_forecast:
                _LOGGER.warning(
                    "Amber Express detailed entity '%s' has no forecasts, "
                    "falling back to simple entity. Spike detection may be limited.",
                    feed_in_detailed,
                )
                feed_in_forecast = self._read_attribute(
                    feed_in_price_entity, "forecast", []
                )

            data.general_forecast = general_forecast or []
            data.feed_in_forecast = feed_in_forecast or []
        else:
            # Use separate forecast sensors (existing behavior)
            data.general_forecast = (
                self._read_attribute(
                    self._get_entity_id(CONF_PRICING_GENERAL_FORECAST), "forecasts", []
                )
                or []
            )
            data.feed_in_forecast = (
                self._read_attribute(
                    self._get_entity_id(CONF_PRICING_FEED_IN_FORECAST), "forecasts", []
                )
                or []
            )
```

With:
```python
        # Issue #300: Read forecasts using pricing provider
        # If no provider configured, use default behavior (read from separate forecast entities)
        if self.pricing_provider is not None:
            data.general_forecast = self.pricing_provider.read_forecasts(
                self.hass, general_price_entity
            )
            data.feed_in_forecast = self.pricing_provider.read_forecasts(
                self.hass, feed_in_price_entity
            )
            _LOGGER.debug(
                "Using pricing provider %s for forecasts",
                self.pricing_provider.name,
            )
        else:
            # Fallback: use separate forecast sensors (existing behavior)
            data.general_forecast = (
                self._read_attribute(
                    self._get_entity_id(CONF_PRICING_GENERAL_FORECAST), "forecasts", []
                )
                or []
            )
            data.feed_in_forecast = (
                self._read_attribute(
                    self._get_entity_id(CONF_PRICING_FEED_IN_FORECAST), "forecasts", []
                )
                or []
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /config/home/localshift/worktrees/issue-300 && pytest tests/state/test_reader.py::test_read_all_external_state_calls_provider_for_general_forecast tests/state/test_reader.py::test_read_all_external_state_uses_provider_forecast_data -xvs`

Expected: PASS

- [ ] **Step 5: Run full test suite for reader.py**

Run: `cd /config/home/localshift/worktrees/issue-300 && pytest tests/state/test_reader.py -v`

Expected: All tests pass (should be ~94 tests)

- [ ] **Step 6: Check coverage**

Run: `cd /config/home/localshift/worktrees/issue-300 && pytest tests/state/test_reader.py --cov=custom_components.localshift.state.reader --cov-report=term-missing`

Expected: ≥95% coverage (we may need additional tests if coverage drops)

### Task 1.2: Test Provider Called for Shadow Prices

**Files:**
- Modify: `tests/state/test_state_reader.py`

- [ ] **Step 1: Write test for shadow prices via provider**

Add this test at the end of `tests/state/test_state_reader.py`:

```python
def test_read_shadow_prices_calls_provider(
    hass_mock: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Test _read_shadow_prices() calls pricing_provider for shadow entities."""
    from unittest.mock import MagicMock, patch
    from custom_components.localshift.pricing.types import ForecastSlot

    provider = MagicMock()
    expected_shadow = [
        ForecastSlot(
            start_time=dt_util.now(),
            duration=30,
            per_kwh=0.28,
            is_spike=False,
            source_type="test_provider",
        )
    ]
    provider.read_forecasts.return_value = expected_shadow
    
    reader = StateReader(
        hass_mock,
        entry,
        validator=None,
        orchestrator=None,
        pricing_provider=provider,
    )
    
    # Enable comparison mode
    entry.data[CONF_COMPARISON_MODE] = COMPARISON_MODE_ENABLED
    
    # Mock shadow price entities
    hass_mock.states.async_set(
        "sensor.amber_express_100h_general_price", "0.28", {"forecasts": []}
    )
    hass_mock.states.async_set(
        "sensor.amber_express_100h_feed_in_price", "0.14", {"forecasts": []}
    )
    hass_mock.states.async_set(
        "sensor.100h_general_price", "0.30", {"forecasts": []}
    )
    hass_mock.states.async_set(
        "sensor.100h_feed_in_price", "0.15", {"forecasts": []}
    )
    
    shadow = reader._read_shadow_prices()
    
    # Verify provider was called for shadow price entities
    assert shadow["general_forecast_shadow"] == expected_shadow
    assert shadow["feed_in_forecast_shadow"] == expected_shadow
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /config/home/localshift/worktrees/issue-300 && pytest tests/state/test_reader.py::test_read_shadow_prices_calls_provider -xvs`

Expected: FAIL

- [ ] **Step 3: Update `_read_shadow_prices()` to use provider**

Modify `custom_components/localshift/state/reader.py` in the `_read_shadow_prices()` method around line 150-200. Replace the if/else pricing_source block with provider calls:

Find the shadow prices section and update it similarly:
```python
    def _read_shadow_prices(self) -> dict[str, Any]:
        """Read alternate price source for comparison mode."""
        # ... existing code for reading prices ...
        
        # Read shadow forecasts
        if self.pricing_provider is not None:
            general_forecast_shadow = self.pricing_provider.read_forecasts(
                self.hass, general_shadow_entity
            )
            feed_in_forecast_shadow = self.pricing_provider.read_forecasts(
                self.hass, feed_in_shadow_entity
            )
        else:
            # Fallback to reading from entities directly
            general_forecast_shadow = self._read_attribute(
                general_shadow_forecast_entity, "forecasts", []
            ) or []
            feed_in_forecast_shadow = self._read_attribute(
                feed_in_shadow_forecast_entity, "forecasts", []
            ) or []
        
        return {
            # ... existing fields ...
            "general_forecast_shadow": general_forecast_shadow,
            "feed_in_forecast_shadow": feed_in_forecast_shadow,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /config/home/localshift/worktrees/issue-300 && pytest tests/state/test_reader.py::test_read_shadow_prices_calls_provider -xvs`

Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /config/home/localshift/worktrees/issue-300 && pytest tests/state/test_reader.py -v --tb=short`

Expected: All tests pass

- [ ] **Step 6: Check coverage again**

Run: `cd /config/home/localshift/worktrees/issue-300 && pytest tests/state/test_reader.py --cov=custom_components.localshift.state.reader --cov-report=term-missing`

Expected: ≥95% coverage

---

## Chunk 2: Verify and Commit

### Task 2.1: Full Test Run with Coverage

- [ ] **Step 1: Run coordinator tests**

Run: `cd /config/home/localshift/worktrees/issue-300 && pytest tests/coordinator/test_coordinator.py -v --tb=short`

Expected: All 62 tests pass, 97% coverage

- [ ] **Step 2: Run reader tests**

Run: `cd /config/home/localshift/worktrees/issue-300 && pytest tests/state/test_reader.py -v --tb=short`

Expected: All ~96 tests pass, ≥95% coverage

- [ ] **Step 3: Run full test suite with coverage check**

Run: `cd /config/home/localshift/worktrees/issue-300 && pytest tests/state/test_reader.py tests/coordinator/test_coordinator.py --cov=custom_components.localshift.state.reader --cov=custom_components.localshift.coordinator.coordinator --cov-report=term-missing`

Expected: Both files ≥95%, no regressions

- [ ] **Step 4: Stage changes**

Run: `cd /config/home/localshift/worktrees/issue-300 && git add -A`

- [ ] **Step 5: Commit with pre-commit hook**

Run: `cd /config/home/localshift/worktrees/issue-300 && git commit -m "feat(#300): Replace pricing source block with provider abstraction"`

Expected: Pre-commit hook passes ✅

---

## Success Criteria

- ✅ All 152+ tests pass (reader + coordinator)
- ✅ reader.py maintains ≥95% coverage
- ✅ coordinator.py maintains ≥95% coverage
- ✅ No if/else pricing_source logic in read_all_external_state() or _read_shadow_prices()
- ✅ pricing_provider is used to read all forecasts
- ✅ Fallback to direct entity reading when provider is None
- ✅ Pre-commit hook passes

## Notes

- The pricing_source configuration key is no longer used in StateReader; this can be removed in a future cleanup task
- The provider abstraction makes it easy to add new pricing sources without modifying StateReader
- Coordinator still manages provider instantiation; StateReader is decoupled from provider selection logic
