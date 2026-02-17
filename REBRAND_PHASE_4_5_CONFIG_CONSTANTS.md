# Phase 4.5: Config Constant Renaming

**Phase:** 4.5 of 13  
**Status:** NOT STARTED  
**Estimated Time:** 25 minutes

---

## Overview

This phase renames configuration constants that contain "AMBER" to be provider-agnostic. This makes the integration work with any variable pricing provider (Amber, Octopus, etc.).

**Total Constants to Rename:** 5
- `CONF_AMBER_GENERAL_PRICE`
- `CONF_AMBER_FEED_IN_PRICE`
- `CONF_AMBER_GENERAL_FORECAST`
- `CONF_AMBER_FEED_IN_FORECAST`
- `CONF_AMBER_PRICE_SPIKE`

**Files Affected:**
- `const.py` (definitions)
- `coordinator.py` (imports and usage)
- `config_flow.py` (imports and usage)
- `strings.json` (UI labels)
- `translations/en.json` (UI labels)
- `DEFAULT_ENTITY_IDS` dict

---

## 4.5.1 const.py

**File:** `custom_components/localshift/const.py`

### Constant Definitions

```python
# SEARCH
# Amber Electric entities
CONF_AMBER_GENERAL_PRICE = "amber_general_price"
CONF_AMBER_FEED_IN_PRICE = "amber_feed_in_price"
CONF_AMBER_GENERAL_FORECAST = "amber_general_forecast"
CONF_AMBER_FEED_IN_FORECAST = "amber_feed_in_forecast"
CONF_AMBER_PRICE_SPIKE = "amber_price_spike"

# REPLACE
# Pricing entities (provider-agnostic)
CONF_PRICING_GENERAL_PRICE = "pricing_general_price"
CONF_PRICING_FEED_IN_PRICE = "pricing_feed_in_price"
CONF_PRICING_GENERAL_FORECAST = "pricing_general_forecast"
CONF_PRICING_FEED_IN_FORECAST = "pricing_feed_in_forecast"
CONF_PRICING_PRICE_SPIKE = "pricing_price_spike"
```

### DEFAULT_ENTITY_IDS Update

```python
# SEARCH
DEFAULT_ENTITY_IDS = {
    CONF_TESLEMETRY_OPERATION_MODE: "select.my_home_operation_mode",
    CONF_TESLEMETRY_BACKUP_RESERVE: "number.my_home_backup_reserve",
    CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged",
    CONF_TESLEMETRY_GRID_POWER: "sensor.my_home_grid_power",
    CONF_TESLEMETRY_BATTERY_POWER: "sensor.my_home_battery_power",
    CONF_TESLEMETRY_SOLAR_POWER: "sensor.my_home_solar_power",
    CONF_TESLEMETRY_LOAD_POWER: "sensor.my_home_load_power",
    CONF_TESLEMETRY_ALLOW_EXPORT: "select.my_home_allow_export",
    CONF_AMBER_GENERAL_PRICE: "sensor.100h_general_price",
    CONF_AMBER_FEED_IN_PRICE: "sensor.100h_feed_in_price",
    CONF_AMBER_GENERAL_FORECAST: "sensor.100h_general_forecast",
    CONF_AMBER_FEED_IN_FORECAST: "sensor.100h_feed_in_forecast",
    CONF_AMBER_PRICE_SPIKE: "binary_sensor.100h_price_spike",
    CONF_SOLCAST_FORECAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
    CONF_SOLCAST_FORECAST_TOMORROW: "sensor.solcast_pv_forecast_forecast_tomorrow",
    CONF_SUN_ENTITY: "sun.sun",
}

# REPLACE
DEFAULT_ENTITY_IDS = {
    CONF_TESLEMETRY_OPERATION_MODE: "select.my_home_operation_mode",
    CONF_TESLEMETRY_BACKUP_RESERVE: "number.my_home_backup_reserve",
    CONF_TESLEMETRY_SOC: "sensor.my_home_percentage_charged",
    CONF_TESLEMETRY_GRID_POWER: "sensor.my_home_grid_power",
    CONF_TESLEMETRY_BATTERY_POWER: "sensor.my_home_battery_power",
    CONF_TESLEMETRY_SOLAR_POWER: "sensor.my_home_solar_power",
    CONF_TESLEMETRY_LOAD_POWER: "sensor.my_home_load_power",
    CONF_TESLEMETRY_ALLOW_EXPORT: "select.my_home_allow_export",
    CONF_PRICING_GENERAL_PRICE: "sensor.100h_general_price",
    CONF_PRICING_FEED_IN_PRICE: "sensor.100h_feed_in_price",
    CONF_PRICING_GENERAL_FORECAST: "sensor.100h_general_forecast",
    CONF_PRICING_FEED_IN_FORECAST: "sensor.100h_feed_in_forecast",
    CONF_PRICING_PRICE_SPIKE: "binary_sensor.100h_price_spike",
    CONF_SOLCAST_FORECAST_TODAY: "sensor.solcast_pv_forecast_forecast_today",
    CONF_SOLCAST_FORECAST_TOMORROW: "sensor.solcast_pv_forecast_forecast_tomorrow",
    CONF_SUN_ENTITY: "sun.sun",
}
```

### Checklist for const.py
- [ ] All 5 constant definitions renamed
- [ ] DEFAULT_ENTITY_IDS updated with new constant names
- [ ] Saved file

---

## 4.5.2 coordinator.py

**File:** `custom_components/localshift/coordinator.py`

### Import Updates

```python
# SEARCH
from .const import (
    CONF_AMBER_FEED_IN_FORECAST,
    CONF_AMBER_FEED_IN_PRICE,
    CONF_AMBER_GENERAL_FORECAST,
    CONF_AMBER_GENERAL_PRICE,
    CONF_AMBER_PRICE_SPIKE,
    ...
)

# REPLACE
from .const import (
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
    ...
)
```

### Usage Updates in Monitored Entities List

```python
# SEARCH (in async_start method)
monitored_entities = [
    self._get_entity_id(CONF_TESLEMETRY_OPERATION_MODE),
    self._get_entity_id(CONF_TESLEMETRY_BACKUP_RESERVE),
    self._get_entity_id(CONF_TESLEMETRY_SOC),
    self._get_entity_id(CONF_TESLEMETRY_GRID_POWER),
    self._get_entity_id(CONF_TESLEMETRY_BATTERY_POWER),
    self._get_entity_id(CONF_TESLEMETRY_SOLAR_POWER),
    self._get_entity_id(CONF_TESLEMETRY_LOAD_POWER),
    # NOT monitoring allow_export - changes programmatically
    self._get_entity_id(CONF_AMBER_GENERAL_PRICE),
    self._get_entity_id(CONF_AMBER_FEED_IN_PRICE),
    self._get_entity_id(CONF_AMBER_GENERAL_FORECAST),
    self._get_entity_id(CONF_AMBER_FEED_IN_FORECAST),
    self._get_entity_id(CONF_AMBER_PRICE_SPIKE),
    self._get_entity_id(CONF_SOLCAST_FORECAST_TODAY),
    self._get_entity_id(CONF_SOLCAST_FORECAST_TOMORROW),
]

# REPLACE
monitored_entities = [
    self._get_entity_id(CONF_TESLEMETRY_OPERATION_MODE),
    self._get_entity_id(CONF_TESLEMETRY_BACKUP_RESERVE),
    self._get_entity_id(CONF_TESLEMETRY_SOC),
    self._get_entity_id(CONF_TESLEMETRY_GRID_POWER),
    self._get_entity_id(CONF_TESLEMETRY_BATTERY_POWER),
    self._get_entity_id(CONF_TESLEMETRY_SOLAR_POWER),
    self._get_entity_id(CONF_TESLEMETRY_LOAD_POWER),
    # NOT monitoring allow_export - changes programmatically
    self._get_entity_id(CONF_PRICING_GENERAL_PRICE),
    self._get_entity_id(CONF_PRICING_FEED_IN_PRICE),
    self._get_entity_id(CONF_PRICING_GENERAL_FORECAST),
    self._get_entity_id(CONF_PRICING_FEED_IN_FORECAST),
    self._get_entity_id(CONF_PRICING_PRICE_SPIKE),
    self._get_entity_id(CONF_SOLCAST_FORECAST_TODAY),
    self._get_entity_id(CONF_SOLCAST_FORECAST_TOMORROW),
]
```

### Checklist for coordinator.py
- [ ] Import statement updated
- [ ] All usage references updated
- [ ] Saved file

---

## 4.5.3 config_flow.py

**File:** `custom_components/localshift/config_flow.py`

### Import Updates

```python
# SEARCH
from .const import (
    CONF_AMBER_FEED_IN_FORECAST,
    CONF_AMBER_FEED_IN_PRICE,
    CONF_AMBER_GENERAL_FORECAST,
    CONF_AMBER_GENERAL_PRICE,
    CONF_AMBER_PRICE_SPIKE,
    ...
)

# REPLACE
from .const import (
    CONF_PRICING_FEED_IN_FORECAST,
    CONF_PRICING_FEED_IN_PRICE,
    CONF_PRICING_GENERAL_FORECAST,
    CONF_PRICING_GENERAL_PRICE,
    CONF_PRICING_PRICE_SPIKE,
    ...
)
```

### Usage Updates

Search for all occurrences of the old constant names and replace:

```python
# Search pattern:
CONF_AMBER_GENERAL_PRICE → CONF_PRICING_GENERAL_PRICE
CONF_AMBER_FEED_IN_PRICE → CONF_PRICING_FEED_IN_PRICE
CONF_AMBER_GENERAL_FORECAST → CONF_PRICING_GENERAL_FORECAST
CONF_AMBER_FEED_IN_FORECAST → CONF_PRICING_FEED_IN_FORECAST
CONF_AMBER_PRICE_SPIKE → CONF_PRICING_PRICE_SPIKE
```

Typical usages in config_flow.py:
- Schema definitions
- Default value retrieval
- Entity validation

### Checklist for config_flow.py
- [ ] Import statement updated
- [ ] All schema definitions updated
- [ ] All validation code updated
- [ ] Saved file

---

## 4.5.4 state_reader.py

**File:** `custom_components/localshift/state_reader.py`

### Check for Usage

If state_reader.py uses these constants (for reading entity values):

```python
# SEARCH for any usage like:
CONF_AMBER_GENERAL_PRICE
CONF_AMBER_FEED_IN_PRICE
CONF_AMBER_GENERAL_FORECAST
CONF_AMBER_FEED_IN_FORECAST
CONF_AMBER_PRICE_SPIKE

# REPLACE with:
CONF_PRICING_GENERAL_PRICE
CONF_PRICING_FEED_IN_PRICE
CONF_PRICING_GENERAL_FORECAST
CONF_PRICING_FEED_IN_FORECAST
CONF_PRICING_PRICE_SPIKE
```

### Checklist for state_reader.py
- [ ] Checked for usage
- [ ] Updated any references found
- [ ] Saved file

---

## 4.5.5 computation_engine.py

**File:** `custom_components/localshift/computation_engine.py`

### Check for Usage

```python
# SEARCH for any usage like:
CONF_AMBER_GENERAL_PRICE
CONF_AMBER_FEED_IN_PRICE
CONF_AMBER_GENERAL_FORECAST
CONF_AMBER_FEED_IN_FORECAST
CONF_AMBER_PRICE_SPIKE
```

### Checklist for computation_engine.py
- [ ] Checked for usage
- [ ] Updated any references found
- [ ] Saved file

---

## 4.5.6 strings.json

**File:** `custom_components/localshift/strings.json`

### Update Config Step "amber" to "pricing"

```json
// SEARCH
{
  "config": {
    "step": {
      "amber": {
        "title": "Amber Electric Entities",
        "description": "Select the Amber Electric entities for pricing data.",
        "data": {
          "amber_general_price": "General Price ($/kWh)",
          "amber_feed_in_price": "Feed-in Price ($/kWh)",
          "amber_general_forecast": "General Price Forecast",
          "amber_feed_in_forecast": "Feed-in Price Forecast",
          "amber_price_spike": "Price Spike Sensor"
        }
      }
    }
  }
}

// REPLACE
{
  "config": {
    "step": {
      "pricing": {
        "title": "Pricing Entities",
        "description": "Select the pricing entities (e.g., from Amber Electric or Octopus Energy).",
        "data": {
          "pricing_general_price": "General Price ($/kWh)",
          "pricing_feed_in_price": "Feed-in Price ($/kWh)",
          "pricing_general_forecast": "General Price Forecast",
          "pricing_feed_in_forecast": "Feed-in Price Forecast",
          "pricing_price_spike": "Price Spike Sensor"
        }
      }
    }
  }
}
```

### Also Update Options Section Title

```json
// SEARCH
"title": "Amber Powerwall Settings"

// REPLACE
"title": "LocalShift Settings"
```

### Checklist for strings.json
- [ ] Step key "amber" → "pricing"
- [ ] Title updated
- [ ] Description updated
- [ ] All data keys updated
- [ ] Options title updated
- [ ] Saved file

---

## 4.5.7 translations/en.json

**File:** `custom_components/localshift/translations/en.json`

### Same Changes as strings.json

```json
// SEARCH
{
  "config": {
    "step": {
      "amber": {
        "title": "Amber Electric Entities",
        "description": "Select the Amber Electric entities for pricing data.",
        "data": {
          "amber_general_price": "General Price ($/kWh)",
          "amber_feed_in_price": "Feed-in Price ($/kWh)",
          "amber_general_forecast": "General Price Forecast",
          "amber_feed_in_forecast": "Feed-in Price Forecast",
          "amber_price_spike": "Price Spike Sensor"
        }
      }
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "Amber Powerwall Settings",
        ...
      }
    }
  }
}

// REPLACE
{
  "config": {
    "step": {
      "pricing": {
        "title": "Pricing Entities",
        "description": "Select the pricing entities (e.g., from Amber Electric or Octopus Energy).",
        "data": {
          "pricing_general_price": "General Price ($/kWh)",
          "pricing_feed_in_price": "Feed-in Price ($/kWh)",
          "pricing_general_forecast": "General Price Forecast",
          "pricing_feed_in_forecast": "Feed-in Price Forecast",
          "pricing_price_spike": "Price Spike Sensor"
        }
      }
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "LocalShift Settings",
        ...
      }
    }
  }
}
```

### Checklist for translations/en.json
- [ ] Step key "amber" → "pricing"
- [ ] Title updated
- [ ] Description updated
- [ ] All data keys updated
- [ ] Options title updated
- [ ] Saved file

---

## 4.5.8 config_flow.py - Step Method Rename

**File:** `custom_components/localshift/config_flow.py`

The config flow step method must be renamed:

```python
# SEARCH
async def async_step_amber(self, user_input: dict[str, Any] | None = None):
    """Handle Amber Electric entities configuration."""

# REPLACE
async def async_step_pricing(self, user_input: dict[str, Any] | None = None):
    """Handle pricing entities configuration."""
```

### Checklist for config_flow.py step method
- [ ] Method renamed: `async_step_amber` → `async_step_pricing`
- [ ] Docstring updated
- [ ] Any internal references updated

---

## Phase 4.5 Completion Checklist

### Constants Renamed (const.py)
- [ ] `CONF_AMBER_GENERAL_PRICE` → `CONF_PRICING_GENERAL_PRICE`
- [ ] `CONF_AMBER_FEED_IN_PRICE` → `CONF_PRICING_FEED_IN_PRICE`
- [ ] `CONF_AMBER_GENERAL_FORECAST` → `CONF_PRICING_GENERAL_FORECAST`
- [ ] `CONF_AMBER_FEED_IN_FORECAST` → `CONF_PRICING_FEED_IN_FORECAST`
- [ ] `CONF_AMBER_PRICE_SPIKE` → `CONF_PRICING_PRICE_SPIKE`

### Files Updated
- [ ] const.py: definitions + DEFAULT_ENTITY_IDS
- [ ] coordinator.py: imports + usage
- [ ] config_flow.py: imports + usage + step method name
- [ ] state_reader.py: checked for usage
- [ ] computation_engine.py: checked for usage
- [ ] strings.json: step key + labels
- [ ] translations/en.json: step key + labels

### Verification
- [ ] No old constant names remain
- [ ] JSON files valid
- [ ] Pre-commit passes

---

**Phase Status:** ✅ COMPLETED