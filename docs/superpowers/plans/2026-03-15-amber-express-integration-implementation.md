# Amber Express Integration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add support for Amber Express as alternative pricing source with embedded forecasts, demand window sensor, and shadow optimizer for A/B comparison.

**Architecture:** Add config option for pricing source (amber vs amber_express). Read forecasts from price sensor attributes when using Amber Express. Add shadow price reading for comparison mode. New entities for demand window and comparison results.

**Tech Stack:** Python 3.13+, Home Assistant integration, voluptuous for config schemas

---

## Chunk 1: Config Constants and Defaults

**Files:**
- Modify: `custom_components/localshift/const.py`
- Test: N/A (constants only)

- [ ] **Step 1: Add new config keys to const.py**

Add after line 106 (after `CONF_PRICING_PRICE_SPIKE`):

```python
# Pricing data source (Issue #300)
CONF_PRICING_DATA_SOURCE = "pricing_data_source"
CONF_COMPARISON_MODE = "comparison_mode"

# Pricing source options
PRICING_SOURCE_AMBER = "amber"
PRICING_SOURCE_AMBER_EXPRESS = "amber_express"

# Comparison mode options
COMPARISON_MODE_DISABLED = "disabled"
COMPARISON_MODE_ENABLED = "enabled"
```

- [ ] **Step 2: Add default values**

Add to `DEFAULT_ENTITY_IDS` dict (around line 155):

```python
CONF_PRICING_DATA_SOURCE: DEFAULT_PRICING_DATA_SOURCE,
CONF_COMPARISON_MODE: DEFAULT_COMPARISON_MODE,
```

Add new defaults:

```python
DEFAULT_PRICING_DATA_SOURCE = "amber"
DEFAULT_COMPARISON_MODE = "disabled"
```

- [ ] **Step 3: Run ruff to check**

Run: `uv run ruff check custom_components/localshift/const.py`

Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add custom_components/localshift/const.py
git commit -m "feat(#300): add pricing data source config keys"
```

---

## Chunk 2: Config Flow Schemas

**Files:**
- Modify: `custom_components/localshift/config_flow/schemas.py`
- Test: N/A (schema only)

- [ ] **Step 1: Add imports to schemas.py**

Add to imports from const:

```python
from ..const import (
    # ... existing imports ...
    CONF_PRICING_DATA_SOURCE,
    CONF_COMPARISON_MODE,
    DEFAULT_PRICING_DATA_SOURCE,
    DEFAULT_COMPARISON_MODE,
    PRICING_SOURCE_AMBER,
    PRICING_SOURCE_AMBER_EXPRESS,
    COMPARISON_MODE_DISABLED,
    COMPARISON_MODE_ENABLED,
)
```

- [ ] **Step 2: Add pricing source schema builder**

Add at end of schemas.py:

```python
def build_pricing_source_schema(
    defaults: dict[str, str] | None = None,
) -> vol.Schema:
    """Build schema for pricing source selection step."""
    if defaults is None:
        defaults = {}

    return vol.Schema({
        vol.Required(
            CONF_PRICING_DATA_SOURCE,
            default=defaults.get(CONF_PRICING_DATA_SOURCE, DEFAULT_PRICING_DATA_SOURCE),
            description="Pricing data source",
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    PRICING_SOURCE_AMBER,
                    PRICING_SOURCE_AMBER_EXPRESS,
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required(
            CONF_COMPARISON_MODE,
            default=defaults.get(CONF_COMPARISON_MODE, DEFAULT_COMPARISON_MODE),
            description="Enable A/B comparison mode",
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    COMPARISON_MODE_DISABLED,
                    COMPARISON_MODE_ENABLED,
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
    })
```

- [ ] **Step 3: Modify build_pricing_schema to accept source**

Update `build_pricing_schema` to accept source and pre-populate:

```python
def build_pricing_schema(
    defaults: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    user_input: dict[str, Any] | None = None,
    pricing_source: str = PRICING_SOURCE_AMBER,
) -> vol.Schema:
    # ... existing code ...
    
    # Determine entity prefix based on source
    if pricing_source == PRICING_SOURCE_AMBER_EXPRESS:
        prefix = "sensor.amber_express_100h_"
    else:
        prefix = "sensor.100h_"
    
    # Update defaults with prefix
    defaults = {
        CONF_PRICING_GENERAL_PRICE: f"{prefix}general_price",
        CONF_PRICING_FEED_IN_PRICE: f"{prefix}feed_in_price",
        CONF_PRICING_GENERAL_FORECAST: "",  # Embedded for Amber Express
        CONF_PRICING_FEED_IN_FORECAST: "",   # Embedded for Amber Express
        CONF_PRICING_PRICE_SPIICE: f"binary_sensor.amber_express_100h_price_spike" if pricing_source == PRICING_SOURCE_AMBER_EXPRESS else "binary_sensor.100h_price_spike",
    }
```

- [ ] **Step 4: Run ruff**

Run: `uv run ruff check custom_components/localshift/config_flow/schemas.py`

Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/config_flow/schemas.py
git commit -m "feat(#300): add pricing source schema builder"
```

---

## Chunk 3: Config Flow Handler

**Files:**
- Modify: `custom_components/localshift/config_flow/__init__.py`
- Test: `tests/test_config_flow.py`

- [ ] **Step 1: Read config_flow/__init__.py structure**

Run: `symdex_get_file_outline("custom_components/localshift/config_flow/__init__.py")`

- [ ] **Step 2: Add pricing_source step**

Add new step class before the pricing step. Key changes:
- Import new schema builder
- Add `async def async_step_pricing_source`
- Pass pricing source to `build_pricing_schema`

- [ ] **Step 3: Update step order**

Ensure pricing_source step comes before pricing step in the flow.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_config_flow.py -v`

Expected: Existing tests pass

- [ ] **Step 5: Commit**

```bash
git add custom_components/localshift/config_flow/
git commit -m "feat(#300): add pricing source config step"
```

---

## Chunk 4: CoordinatorData Fields

**Files:**
- Modify: `custom_components/localshift/coordinator/data.py`
- Test: N/A (data class only)

- [ ] **Step 1: Add shadow price fields to CoordinatorData**

Add after line 178 (after `feed_in_price`):

```python
# Shadow prices for A/B comparison (Issue #300)
general_price_shadow: float = 0.0
feed_in_price_shadow: float = 0.0
general_forecast_shadow: list = field(default_factory=list)
feed_in_forecast_shadow: list = field(default_factory=list)

# Decision comparison results
primary_decision: str = ""
shadow_decision: str = ""
comparison_match: bool = True
price_delta: float = 0.0  # Difference between sources
```

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check custom_components/localshift/coordinator/data.py`

Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add custom_components/localshift/coordinator/data.py
git commit -m "feat(#300): add shadow price fields to CoordinatorData"
```

---

## Chunk 5: State Reader - Embedded Forecast Reading

**Files:**
- Modify: `custom_components/localshift/state/reader.py`
- Test: `tests/state/test_state_reader.py`

- [ ] **Step 1: Read existing forecast reading code**

Find where forecasts are read (around line 364-375).

- [ ] **Step 2: Write failing test**

Add to `tests/state/test_state_reader.py`:

```python
def test_read_embedded_forecasts_amber_express(self, reader, coordinator_data):
    """Test reading forecasts from price sensor attributes for Amber Express."""
    # Setup mock states with embedded forecasts
    mock_states = {
        "sensor.amber_express_100h_general_price": MockState(
            "sensor.amber_express_100h_general_price",
            "0.15",
            {"forecast": [{"time": "2026-03-15T10:00:00", "value": 0.15}]}
        ),
        "sensor.amber_express_100h_feed_in_price": MockState(
            "sensor.amber_express_100h_feed_in_price",
            "0.08",
            {"forecast": [{"time": "2026-03-15T10:00:00", "value": 0.08}]}
        ),
    }
    
    # Configure reader for amber_express
    reader._config = {"pricing_data_source": "amber_express"}
    
    # Read prices and forecasts
    reader._read_pricing_data(coordinator_data)
    
    # Verify embedded forecasts are read
    assert len(coordinator_data.general_forecast) > 0
    assert coordinator_data.general_forecast[0].get("price") == 0.15
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/state/test_state_reader.py::TestStateReader::test_read_embedded_forecasts_amber_express -v`

Expected: FAIL (functionality not implemented)

- [ ] **Step 4: Implement embedded forecast reading**

Modify `_read_pricing_data` in reader.py to:
1. Check `pricing_data_source` config
2. If amber_express, read forecasts from price sensor attributes
3. If amber, use existing separate forecast sensors

```python
def _read_pricing_data(self, data: CoordinatorData) -> None:
    """Read pricing data from entities."""
    pricing_source = self._config.get(CONF_PRICING_DATA_SOURCE, DEFAULT_PRICING_DATA_SOURCE)
    
    # Read prices
    general_price_entity = self._get_entity_id(CONF_PRICING_GENERAL_PRICE)
    feed_in_price_entity = self._get_entity_id(CONF_PRICING_FEED_IN_PRICE)
    
    # ... existing price reading code ...
    
    # Read forecasts based on source
    if pricing_source == PRICING_SOURCE_AMBER_EXPRESS:
        # Read embedded forecasts from price sensor attributes
        data.general_forecast = self._read_attribute(
            general_price_entity, "forecast", []
        )
        data.feed_in_forecast = self._read_attribute(
            feed_in_price_entity, "forecast", []
        )
    else:
        # Use separate forecast sensors
        data.general_forecast = self._read_attribute(
            self._get_entity_id(CONF_PRICING_GENERAL_FORECAST), "forecasts", []
        )
        data.feed_in_forecast = self._read_attribute(
            self._get_entity_id(CONF_PRICING_FEED_IN_FORECAST), "forecasts", []
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/state/test_state_reader.py::TestStateReader::test_read_embedded_forecasts_amber_express -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add custom_components/localshift/state/reader.py tests/state/test_state_reader.py
git commit -m "feat(#300): add embedded forecast reading for Amber Express"
```

---

## Chunk 6: State Reader - Shadow Prices

**Files:**
- Modify: `custom_components/localshift/state/reader.py`
- Test: `tests/state/test_state_reader.py`

- [ ] **Step 1: Write failing test**

```python
def test_read_shadow_prices(self, reader, coordinator_data):
    """Test reading shadow prices for comparison mode."""
    # Setup both price sources
    reader._config = {
        "pricing_data_source": "amber",
        "comparison_mode": "enabled",
    }
    
    # Read shadow prices
    shadow = reader._read_shadow_prices()
    
    # Verify shadow prices are populated
    assert shadow.get("general_price_shadow") > 0
    assert shadow.get("feed_in_price_shadow") > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/state/test_state_reader.py::TestStateReader::test_read_shadow_prices -v`

Expected: FAIL

- [ ] **Step 3: Implement shadow price reading**

Add method to reader.py:

```python
def _read_shadow_prices(self) -> dict[str, Any]:
    """Read alternate price source for comparison mode."""
    current_source = self._config.get(CONF_PRICING_DATA_SOURCE, DEFAULT_PRICING_DATA_SOURCE)
    
    # Determine shadow source
    if current_source == PRICING_SOURCE_AMBER:
        shadow_source = PRICING_SOURCE_AMBER_EXPRESS
    else:
        shadow_source = PRICING_SOURCE_AMBER
    
    # Build entity IDs for shadow source
    if shadow_source == PRICING_SOURCE_AMBER_EXPRESS:
        prefix = "sensor.amber_express_100h_"
    else:
        prefix = "sensor.100h_"
    
    shadow_general_price = self._read_float_optional(f"{prefix}general_price")
    shadow_feed_in_price = self._read_float_optional(f"{prefix}feed_in_price")
    
    # Read shadow forecasts
    if shadow_source == PRICING_SOURCE_AMBER_EXPRESS:
        shadow_general_forecast = self._read_attribute(
            f"{prefix}general_price", "forecast", []
        )
        shadow_feed_in_forecast = self._read_attribute(
            f"{prefix}feed_in_price", "forecast", []
        )
    else:
        # For amber, need separate forecast entities
        shadow_general_forecast = []
        shadow_feed_in_forecast = []
    
    return {
        "general_price_shadow": shadow_general_price or 0.0,
        "feed_in_price_shadow": shadow_feed_in_price or 0.0,
        "general_forecast_shadow": shadow_general_forecast,
        "feed_in_forecast_shadow": shadow_feed_in_forecast,
    }
```

- [ ] **Step 4: Update main read method to populate shadow data**

In `_read_pricing_data`, add:

```python
def _read_pricing_data(self, data: CoordinatorData) -> None:
    # ... existing code ...
    
    # Check if comparison mode enabled
    comparison_mode = self._config.get(CONF_COMPARISON_MODE, DEFAULT_COMPARISON_MODE)
    if comparison_mode == COMPARISON_MODE_ENABLED:
        shadow = self._read_shadow_prices()
        data.general_price_shadow = shadow["general_price_shadow"]
        data.feed_in_price_shadow = shadow["feed_in_price_shadow"]
        data.general_forecast_shadow = shadow["general_forecast_shadow"]
        data.feed_in_forecast_shadow = shadow["feed_in_forecast_shadow"]
        
        # Calculate price delta
        data.price_delta = abs(data.general_price - data.general_price_shadow)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/state/test_state_reader.py::TestStateReader::test_read_shadow_prices -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add custom_components/localshift/state/reader.py tests/state/test_state_reader.py
git commit -m "feat(#300): add shadow price reading for comparison mode"
```

---

## Chunk 7: New Entities - Demand Window Sensor

**Files:**
- Modify: `custom_components/localshift/binary_sensor.py`
- Test: `tests/test_binary_sensor.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_binary_sensor.py`:

```python
def test_demand_window_sensor(self, coordinator):
    """Test demand window binary sensor reads Amber Express entity."""
    coordinator.data.demand_window_amber = True
    
    # Find the sensor
    sensor = next((s for s in coordinator.entities if s.name == "Demand Window"), None)
    assert sensor is not None
    assert sensor.is_on is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_binary_sensor.py::test_demand_window_sensor -v`

Expected: FAIL

- [ ] **Step 3: Add demand_window field to CoordinatorData**

Already added in Chunk 4.

- [ ] **Step 4: Add demand window reading to reader.py**

```python
# Add to _read_pricing_data
demand_window_entity = "binary_sensor.amber_express_100h_demand_window"
data.demand_window_amber = self._read_bool(demand_window_entity)
```

- [ ] **Step 5: Add binary sensor class**

Add to `binary_sensor.py`:

```python
class LocalShiftDemandWindow(LocalShiftBinarySensor):
    """Binary sensor for demand window status from Amber Express."""

    def __init__(self, coordinator):
        super().__init__(coordinator, "demand_window_amber")

    @property
    def name(self) -> str:
        return "Demand Window"

    @property
    def icon(self) -> str:
        return "mdi:clock-alert"
```

- [ ] **Step 6: Register entity**

Add to platform setup in `binary_sensor.py` or `__init__.py`.

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/test_binary_sensor.py::test_demand_window_sensor -v`

Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add custom_components/localshift/binary_sensor.py tests/test_binary_sensor.py
git commit -m "feat(#300): add demand window binary sensor"
```

---

## Chunk 8: New Entities - Comparison Sensors

**Files:**
- Modify: `custom_components/localshift/sensor.py`
- Test: `tests/test_sensor.py`

- [ ] **Step 1: Write failing tests**

```python
def test_comparison_result_sensor(self, coordinator):
    """Test comparison result sensor."""
    coordinator.data.comparison_match = False
    
    sensor = next((s for s in coordinator.entities if s.name == "Comparison Result"), None)
    assert sensor is not None
    assert sensor.native_value == "mismatch"

def test_price_delta_sensor(self, coordinator):
    """Test price delta sensor."""
    coordinator.data.price_delta = 0.05
    
    sensor = next((s for s in coordinator.entities if s.name == "Price Delta"), None)
    assert sensor is not None
    assert sensor.native_value == 0.05
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sensor.py::test_comparison_result_sensor tests/test_sensor.py::test_price_delta_sensor -v`

Expected: FAIL

- [ ] **Step 3: Add sensor classes**

Add to `sensor.py`:

```python
class LocalShiftComparisonResultSensor(LocalShiftSensor):
    """Sensor showing primary vs shadow decision match status."""

    def __init__(self, coordinator):
        super().__init__(coordinator, "comparison_match")

    @property
    def name(self) -> str:
        return "Comparison Result"

    @property
    def native_value(self) -> str:
        return "match" if self.coordinator.data.comparison_match else "mismatch"


class LocalShiftPriceDeltaSensor(LocalShiftSensor):
    """Sensor showing price difference between sources."""

    def __init__(self, coordinator):
        super().__init__(coordinator, "price_delta")

    @property
    def name(self) -> str:
        return "Price Delta"

    @property
    def native_unit_of_measurement(self) -> str:
        return "$/kWh"
```

- [ ] **Step 4: Register entities**

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_sensor.py::test_comparison_result_sensor tests/test_sensor.py::test_price_delta_sensor -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add custom_components/localshift/sensor.py tests/test_sensor.py
git commit -m "feat(#300): add comparison result sensors"
```

---

## Chunk 9: Documentation Updates

**Files:**
- Modify: `docs/ENTITY_REFERENCE.md`

- [ ] **Step 1: Update entity reference**

Add new entities to the entity table:

```markdown
| binary_sensor.localshift_demand_window | Demand Window | on/off | Amber Express demand window status |
| sensor.localshift_comparison_result | Comparison Result | match/mismatch | Primary vs shadow decision match |
| sensor.localshift_price_delta | Price Delta | $/kWh | Price difference between sources |
```

- [ ] **Step 2: Commit**

```bash
git add docs/ENTITY_REFERENCE.md
git commit -m "docs(#300): document new entities"
```

---

## Chunk 10: Integration Tests and Final Verification

**Files:**
- Test: Full test suite

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest`

Expected: All tests pass

- [ ] **Step 2: Run coverage check**

Run: `uv run pytest --cov=custom_components/localshift --cov-report=term-missing`

Expected: Coverage ≥95%

- [ ] **Step 3: Run ruff**

Run: `uv run ruff check custom_components/localshift`

Expected: No errors

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(#300): complete Amber Express integration"
```

- [ ] **Step 5: Push and create PR**

```bash
git push -u origin issue/300
gh pr create --base test --title "feat(#300): Add Amber Express integration" --body "$(cat <<'EOF'
## Summary
- Add config option to select Amber vs Amber Express pricing source
- Read embedded forecasts from price sensor attributes when using Amber Express
- Add demand window binary sensor from Amber Express
- Add shadow optimizer for A/B comparison between sources
- Add comparison result sensors

## Related Issue
Closes #300
EOF
)"
```

---

## Summary

| Chunk | Description | Files |
|-------|-------------|-------|
| 1 | Config constants | const.py |
| 2 | Config schemas | schemas.py |
| 3 | Config flow handler | config_flow/__init__.py |
| 4 | CoordinatorData fields | coordinator/data.py |
| 5 | Embedded forecasts | state/reader.py |
| 6 | Shadow prices | state/reader.py |
| 7 | Demand window sensor | binary_sensor.py |
| 8 | Comparison sensors | sensor.py |
| 9 | Documentation | ENTITY_REFERENCE.md |
| 10 | Final verification | Full test suite |

Total estimated chunks: 10
