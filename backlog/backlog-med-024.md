# Backlog Item

**ID:** backlog-med-024  
**Priority:** MED  
**Status:** PROPOSED  
**Created:** 2026-02-19  
**Updated:** 2026-02-19  

---

## Summary

Improve usability of component settings with friendly names and help text

---

## Description

The settings/configurable options for the LocalShift component within Home Assistant lack usability improvements:

1. **Missing friendly names** - Some settings use technical jargon (e.g., "Deadband", "DW Entry", "Percentile")
2. **No descriptions/help text** - Users have no guidance on what each setting does
3. **Incomplete translations** - 4 settings are missing from strings.json:
   - `manual_override_timeout`
   - `load_weight_recent`
   - `minimum_target_soc`
   - `allow_dw_entry_under_target`
4. **Documentation drift** - docs/DEVELOPER_GUIDE.md is missing some options and uses inconsistent naming

### Settings Requiring Attention

**Options Flow (Configure > LocalShift > Configure):**

| Config Key | Current Label | Issues |
|------------|---------------|--------|
| `cheap_price_percentile` | "Cheap Price Percentile" | Technical term, needs help text |
| `max_precharge_price` | "Max Pre-charge Price" | Needs help text |
| `cheap_price_deadband` | "Price Deadband" | Technical jargon, needs explanation |
| `forecast_lookahead_hours` | "Forecast Lookahead" | Vague, needs clarification |
| `battery_target` | "Battery Target" | Needs help text |
| `demand_window_start` | "Demand Window Start" | Needs explanation |
| `demand_window_end` | "Demand Window End" | Needs explanation |
| `manual_override_timeout` | **MISSING** | Label missing entirely |
| `load_weight_recent` | **MISSING** | Label missing, technical term |
| `minimum_target_soc` | **MISSING** | Label missing, needs help text |
| `allow_dw_entry_under_target` | **MISSING** | Label missing, confusing name |

---

## Affected Files

- `custom_components/localshift/strings.json` - Missing labels and data_description
- `custom_components/localshift/translations/en.json` - Missing labels and data_description
- `docs/DEVELOPER_GUIDE.md` - Missing options, inconsistent naming

---

## Proposed Solution

### 1. Add friendly names and descriptions to strings.json

Add `data_description` entries for all settings in Home Assistant's recommended format.

### 2. Apply same changes to translations/en.json

Mirror strings.json changes for consistency.

### 3. Update docs/DEVELOPER_GUIDE.md

- Add missing options to the Options Flow table
- Use consistent naming matching the UI

### Proposed Friendly Names

| Config Key | Proposed Friendly Name |
|------------|------------------------|
| `cheap_price_percentile` | Cheap Price Threshold |
| `cheap_price_deadband` | Price Hysteresis Band |
| `manual_override_timeout` | Manual Override Timeout |
| `load_weight_recent` | Recent Load Weight |
| `minimum_target_soc` | Minimum Discharge SOC |
| `allow_dw_entry_under_target` | Allow Early Demand Window Entry |

---

## Notes

- Home Assistant supports `data_description` in config flows to show help text below each field
- The changes are purely cosmetic - no logic changes required
- Should follow Home Assistant best practices for translatable strings

---

## Related Items

- Related to backlog-med-005 (Unused Config Option - ALLOW_EXPORT)
