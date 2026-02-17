# Phase 5: UI & Translations

**Phase:** 5 of 13  
**Status:** NOT STARTED  
**Estimated Time:** 15 minutes

---

## Overview

This phase updates the UI configuration files (strings.json and translations) to reflect the rebrand and config constant changes from Phase 4.5.

**Note:** Phase 4.5 already covers most of strings.json and translations/en.json. This phase documents any remaining UI updates not covered there.

**Files to Update:**
- `strings.json` (primary UI strings)
- `translations/en.json` (English translations)

---

## 5.1 Complete strings.json Update

**File:** `custom_components/localshift/strings.json`

### Full Updated Content

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Teslemetry Entities",
        "description": "Select the Teslemetry entities for your Tesla Powerwall.",
        "data": {
          "teslemetry_operation_mode": "Operation Mode (select entity)",
          "teslemetry_backup_reserve": "Backup Reserve (number entity)",
          "teslemetry_soc": "Battery SOC %",
          "minimum_target_soc": "Minimum Target SOC (number entity)",
          "teslemetry_grid_power": "Grid Power (kW)",
          "teslemetry_battery_power": "Battery Power (kW)",
          "teslemetry_solar_power": "Solar Power (kW)",
          "teslemetry_load_power": "Load Power (kW)",
          "teslemetry_allow_export": "Allow Export (select entity)"
        }
      },
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
      },
      "solcast": {
        "title": "Solcast & Notifications",
        "description": "Select the Solcast forecast entities and notification service.",
        "data": {
          "solcast_forecast_today": "Solar Forecast Today",
          "solcast_forecast_tomorrow": "Solar Forecast Tomorrow",
          "notify_service": "Notification Service (e.g. notify.mobile_app_name)"
        }
      }
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "LocalShift Settings",
        "description": "Adjust automation thresholds and demand window timing.",
        "data": {
          "cheap_price_percentile": "Cheap Price Percentile",
          "max_precharge_price": "Max Pre-charge Price",
          "cheap_price_deadband": "Price Deadband",
          "forecast_lookahead_hours": "Forecast Lookahead",
          "precharge_battery_threshold": "Pre-charge Battery Threshold",
          "battery_target": "Battery Target",
          "demand_window_start": "Demand Window Start",
          "demand_window_end": "Demand Window End"
        }
      }
    }
  }
}
```

### Changes Summary

| Section | Old | New |
|---------|-----|-----|
| Config step key | `amber` | `pricing` |
| Config step title | "Amber Electric Entities" | "Pricing Entities" |
| Config step description | "Select the Amber Electric entities for pricing data." | "Select the pricing entities (e.g., from Amber Electric or Octopus Energy)." |
| Data key prefix | `amber_*` | `pricing_*` |
| Options title | "Amber Powerwall Settings" | "LocalShift Settings" |

### Checklist for strings.json
- [ ] Config step key renamed: `amber` → `pricing`
- [ ] Config step title updated
- [ ] Config step description updated
- [ ] All data keys updated with `pricing_` prefix
- [ ] Options title updated to "LocalShift Settings"
- [ ] JSON validated
- [ ] Saved file

---

## 5.2 Complete translations/en.json Update

**File:** `custom_components/localshift/translations/en.json`

### Full Updated Content (same as strings.json)

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Teslemetry Entities",
        "description": "Select the Teslemetry entities for your Tesla Powerwall.",
        "data": {
          "teslemetry_operation_mode": "Operation Mode (select entity)",
          "teslemetry_backup_reserve": "Backup Reserve (number entity)",
          "teslemetry_soc": "Battery SOC %",
          "minimum_target_soc": "Minimum Target SOC (number entity)",
          "teslemetry_grid_power": "Grid Power (kW)",
          "teslemetry_battery_power": "Battery Power (kW)",
          "teslemetry_solar_power": "Solar Power (kW)",
          "teslemetry_load_power": "Load Power (kW)",
          "teslemetry_allow_export": "Allow Export (select entity)"
        }
      },
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
      },
      "solcast": {
        "title": "Solcast & Notifications",
        "description": "Select the Solcast forecast entities and notification service.",
        "data": {
          "solcast_forecast_today": "Solar Forecast Today",
          "solcast_forecast_tomorrow": "Solar Forecast Tomorrow",
          "notify_service": "Notification Service (e.g. notify.mobile_app_name)"
        }
      }
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "LocalShift Settings",
        "description": "Adjust automation thresholds and demand window timing.",
        "data": {
          "cheap_price_percentile": "Cheap Price Percentile",
          "max_precharge_price": "Max Pre-charge Price",
          "cheap_price_deadband": "Price Deadband",
          "forecast_lookahead_hours": "Forecast Lookahead",
          "precharge_battery_threshold": "Pre-charge Battery Threshold",
          "battery_target": "Battery Target",
          "demand_window_start": "Demand Window Start",
          "demand_window_end": "Demand Window End"
        }
      }
    }
  }
}
```

### Checklist for translations/en.json
- [ ] Config step key renamed: `amber` → `pricing`
- [ ] Config step title updated
- [ ] Config step description updated
- [ ] All data keys updated with `pricing_` prefix
- [ ] Options title updated to "LocalShift Settings"
- [ ] JSON validated
- [ ] Saved file

---

## 5.3 Validation

### JSON Syntax Validation

```bash
# Validate JSON syntax
python3 -c "import json; json.load(open('custom_components/localshift/strings.json'))"
python3 -c "import json; json.load(open('custom_components/localshift/translations/en.json'))"
```

### Home Assistant Config Flow Test

After implementation, verify in Home Assistant:
1. Go to Settings → Devices & Services
2. Add Integration → "LocalShift"
3. Verify step titles appear correctly
4. Verify field labels appear correctly

---

## Phase 5 Completion Checklist

- [ ] strings.json fully updated
- [ ] translations/en.json fully updated
- [ ] Both JSON files validated
- [ ] No "Amber" references remain in UI strings
- [ ] Step key `amber` → `pricing` in both files
- [ ] All data keys use `pricing_` prefix

---

**Phase Status:** ☐ NOT STARTED | ☐ IN PROGRESS | ☐ COMPLETED