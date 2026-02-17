# Regression Analysis: 15-Minute Forecast Implementation

## Overview

Reviewed codebase for potential regressions after implementing 15-minute forecast granularity.

## Regression Found: Dashboard Forecast Table

### Issue

**Location:** `dashboards/localshift_component.yaml`

**Problem:** The 24-Hour SOC Forecast markdown table now displays **96 rows** instead of 24.

**Before (Hourly):**
```yaml
# 24 rows - fits nicely on screen
Hour  | SOC (%) | Solar (kWh) | Load (kWh) | Net (kWh)
------|---------|-------------|------------|-----------
00    | 45.2    | 0.25        | 0.12       | 0.13
01    | 44.8    | 0.00        | 0.10       | -0.10
... (24 rows total)
```

**After (15-minute):**
```yaml
# 96 rows - very long!
Hour  | SOC (%) | Solar (kWh) | Load (kWh) | Net (kWh)
------|---------|-------------|------------|-----------
00:00 | 45.2    | 0.125       | 0.062      | 0.063
00:15 | 45.0    | 0.000       | 0.060      | -0.060
00:30 | 44.8    | 0.000       | 0.058      | -0.058
00:45 | 44.6    | 0.000       | 0.062      | -0.062
01:00 | 44.4    | 0.000       | 0.060      | -0.060
... (96 rows total!)
```

### Impact

- **UX degradation:** Table is 4x longer, difficult to scroll
- **Dashboard layout:** May break on smaller screens
- **Information overload:** Too much detail for quick overview

### Recommended Fix

Update the Jinja2 filter to show only hourly summary (every 4th slot where `minute == 0`):

```yaml
# --- Daily Forecast ---
- type: markdown
  content: |
    **24-Hour SOC Forecast (15-min granularity, hourly summary)**
        
    ```
    Hour  | SOC (%) | Solar (kWh) | Load (kWh) | Net (kWh)
    ------|---------|-------------|------------|-----------
    {% for item in state_attr('sensor.localshift_daily_forecast', 'forecast') | default([]) | selectattr('minute', 'equalto', 0) %}
    {{ "%02d"|format(item.hour) }}    | {{ "%.1f"|format(item.predicted_soc) }}     | {{ "%.2f"|format(item.solar_kwh) }}       | {{ "%.2f"|format(item.consumption_kwh) }}     | {{ "%.2f"|format(item.net_kwh) }}
    {% endfor %}
    ```
    
    *Note: Forecast uses 15-minute granularity (96 slots). Table shows hourly summary (every 4th slot).*
```

**Key change:** Added `| selectattr('minute', 'equalto', 0)` filter to only show slots where minute == 0.

### Alternative Approaches

1. **Show first 24 rows:** `| batch(24) | first` - Quick fix but arbitrary
2. **Create separate 15-min view:** Add another card for detailed view
3. **Use apexcharts only:** Replace table entirely with chart
4. **Paginated view:** Add "show more" button (complex for YAML)

### No Regressions Found

✅ **Core computation logic:** No changes to decision-making algorithms
✅ **Sensor definitions:** No changes to sensor types or IDs
✅ **Coordinator data structure:** Compatible (adds `minute` field, backward compatible)
✅ **Battery controller:** No changes to control logic
✅ **State machine:** No changes to mode transitions
✅ **Apexcharts card:** Already uses `data_generator` that maps all forecast entries (works with 96 slots)

## Summary

**Severity:** Low (UI-only issue, no functional regression)

**Recommended Action:** Update dashboard to show hourly summary for better UX

**Priority:** Optional - can be done in follow-up work