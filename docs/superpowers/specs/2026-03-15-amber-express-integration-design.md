# Amber Express Integration Design

**Issue:** #300  
**Date:** 2026-03-15  
**Status:** Draft

## Summary

Add support for Amber Express as an alternative pricing data source, with embedded forecasts, real-time demand window detection, and shadow optimizer for A/B comparison between Amber and Amber Express data sources.

## Background

The Amber integration provides electricity pricing data but has reliability issues:
- Frequent `unavailable` periods causing $0 price fallbacks
- Less frequent updates (~5-30 min)

Amber Express provides:
- More frequent updates (~5 min)
- Zero unavailabilities in testing
- Embedded forecasts in price sensor attributes
- Real-time demand window binary sensor
- Price confidence data (estimate vs confirmed)

## Requirements

### Core Requirements

1. **Pricing Source Selection** - Add config option to choose between Amber and Amber Express
2. **Embedded Forecasts** - Read forecasts from price sensor attributes when using Amber Express
3. **Demand Window Sensor** - Add LocalShift entity for Amber Express demand window binary sensor

### Comparison Features

4. **Shadow Optimizer** - Run optimizer twice (primary + shadow) for A/B comparison
5. **Decision Logging** - Track both decisions for comparison

### Out of Scope

- Renewables sensor integration (removed per user request)
- Price confidence weighting (deferred to future)

## Design

### 1. Config Flow Changes

Add new config options in `const.py`:

```python
# New config keys
CONF_PRICING_DATA_SOURCE = "pricing_data_source"
CONF_COMPARISON_MODE = "comparison_mode"

# Default values
DEFAULT_PRICING_DATA_SOURCE = "amber"
DEFAULT_COMPARISON_MODE = "disabled"

# Options
PRICING_SOURCE_AMBER = "amber"
PRICING_SOURCE_AMBER_EXPRESS = "amber_express"

COMPARISON_MODE_DISABLED = "disabled"
COMPARISON_MODE_ENABLED = "enabled"
```

#### Entity ID Mapping

| Entity Type | Amber (default) | Amber Express |
|------------|------------------|---------------|
| General Price | `sensor.100h_general_price` | `sensor.amber_express_100h_general_price` |
| Feed-in Price | `sensor.100h_feed_in_price` | `sensor.amber_express_100h_feed_in_price` |
| Price Spike | `binary_sensor.100h_price_spike` | `binary_sensor.amber_express_100h_price_spike` |
| Forecasts | Separate sensors | Embedded in price sensor |

### 2. New Config Flow Step

Add `pricing_source` step before pricing entity selection:

1. **User selects pricing data source** (Amber vs Amber Express)
2. **User selects comparison mode** (disabled vs enabled)
3. **Pricing entities step** - Pre-populates entity IDs based on source selection

### 3. Embedded Forecast Reading

In `state/reader.py`, modify forecast reading:

```python
def _read_forecasts(self, source: str, general_price_entity: str, feed_in_price_entity: str) -> tuple[list, list]:
    """Read forecasts based on pricing source."""
    if source == PRICING_SOURCE_AMBER_EXPRESS:
        # Read embedded forecasts from price sensor attributes
        general_forecast = self._read_attribute(general_price_entity, "forecast", [])
        feed_in_forecast = self._read_attribute(feed_in_price_entity, "forecast", [])
    else:
        # Use separate forecast sensors (existing behavior)
        general_forecast = self._read_attribute(
            self._get_entity_id(CONF_PRICING_GENERAL_FORECAST), "forecasts", []
        )
        feed_in_forecast = self._read_attribute(
            self._get_entity_id(CONF_PRICING_FEED_IN_FORECAST), "forecasts", []
        )
    return general_forecast, feed_in_forecast
```

### 4. Shadow Optimizer

Add shadow price reading in `state/reader.py`:

```python
def _read_shadow_prices(self) -> dict:
    """Read alternate price source for comparison."""
    # Determine alternate source
    current = self._config.get(CONF_PRICING_DATA_SOURCE)
    shadow_source = "amber_express" if current == "amber" else "amber"
    
    # Read shadow prices and forecasts
    return {
        "general_price_shadow": ...,
        "feed_in_price_shadow": ...,
        "general_forecast_shadow": ...,
        "feed_in_forecast_shadow": ...,
    }
```

### 5. CoordinatorData Fields

Add to `coordinator/data.py`:

```python
@dataclass
class CoordinatorData:
    # ... existing fields ...
    
    # Shadow prices (for A/B comparison)
    general_price_shadow: float = 0.0
    feed_in_price_shadow: float = 0.0
    general_forecast_shadow: list = field(default_factory=list)
    feed_in_forecast_shadow: list = field(default_factory=list)
    
    # Decision comparison
    primary_decision: str = ""
    shadow_decision: str = ""
    comparison_match: bool = True
```

### 6. New Entities

#### Binary Sensor: Demand Window

| Property | Value |
|----------|-------|
| Entity ID | `binary_sensor.localshift_demand_window` |
| Source | `binary_sensor.amber_express_100h_demand_window` |
| State | on/off |

#### Sensors: Comparison Results

| Entity ID | Description |
|-----------|-------------|
| `sensor.localshift_comparison_result` | "match" or "mismatch" |
| `sensor.localshift_price_delta` | Price difference between sources ($/kWh) |

### 7. Files to Modify

| File | Changes |
|------|---------|
| `const.py` | Add `CONF_PRICING_DATA_SOURCE`, `CONF_COMPARISON_MODE`, defaults, options |
| `config_flow/schemas.py` | Add pricing source schema, comparison mode schema |
| `config_flow/__init__.py` | Add `pricing_source` step handler |
| `state/reader.py` | Add dual price reading, embedded forecast reading, shadow prices |
| `coordinator/data.py` | Add shadow price fields, comparison result fields |
| `sensor.py` | Add comparison sensors |
| `binary_sensor.py` | Add demand_window sensor |
| `dashboard.yaml` | Update default entity references |
| `docs/ENTITY_REFERENCE.md` | Document new entities |

### 8. Backward Compatibility

- Existing Amber users: No changes required, works as before
- New users: Can select pricing source and enable comparison at setup
- Existing users wanting to switch: Can change via Options flow

## Testing Considerations

1. **Unit tests** for embedded forecast reading with mock attributes
2. **Integration tests** for config flow with both pricing sources
3. **Shadow mode tests** verify dual optimizer execution
4. **Historical comparison** via comparison sensors after deployment

## Future Enhancements

- Price confidence weighting using `estimate` field
- Auto-switch to backup source when primary unavailable
- Demand window sensor as primary (with time config fallback)
