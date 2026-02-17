# Backlog Item Template

**ID:** backlog-med-010  
**Priority:** MED  
**Status:** IN_PROGRESS
**Created:** 2026-02-17  
**Updated:** 2026-02-17  

---

## Summary

Add consistent category-based naming conventions to all entities and refactor existing code to match.

---

## Description

The current entity naming is inconsistent across platforms:
- Sensors and binary sensors lack the `amber_powerwall_` prefix
- Buttons, numbers, and switches already use the prefix but inconsistently
- Display names vary between natural language and abbreviated forms

**Proposed Convention:**
- Unique IDs: `{category}_{descriptor}` format
  - Sensors: `amber_powerwall_sensor_{specific_name}`
  - Binary Sensors: `amber_powerwall_binary_{specific_name}`
  - Buttons: `amber_powerwall_button_{specific_name}`
  - Numbers: `amber_powerwall_number_{specific_name}`
  - Switches: `amber_powerwall_switch_{specific_name}`
- Display Names: Include category word for clarity (e.g., "Sensor Price Cheap Effective")

---

## Affected Files

- `custom_components/amber_powerwall/sensor.py` - 12 sensors
- `custom_components/amber_powerwall/binary_sensor.py` - 8 binary sensors
- `custom_components/amber_powerwall/button.py` - 5 buttons
- `custom_components/amber_powerwall/number.py` - 7 number entities
- `custom_components/amber_powerwall/switch.py` - 4 switches
- `docs/ENTITY_REFERENCE.md` - documentation

---

## Sensor Mapping

| Current unique_id | New unique_id | New Display Name |
|-------------------|---------------|------------------|
| `effective_cheap_price` | `amber_powerwall_price_cheap_effective` | Price Cheap Effective |
| `cheap_charge_stop_price` | `amber_powerwall_price_cheap_charge_stop` | Price Cheap Charge Stop |
| `solar_weighted_avg_fit` | `amber_powerwall_solar_weighted_avg_fit` | Solar Weighted Avg FIT |
| `battery_automation_active_mode` | `amber_powerwall_battery_mode` | Battery Mode |
| `solar_battery_forecast` | `amber_powerwall_forecast_battery` | Forecast Battery |
| `grid_import_power` | `amber_powerwall_power_grid_import` | Power Grid Import |
| `grid_export_power` | `amber_powerwall_power_grid_export` | Power Grid Export |
| `net_electricity_cost_today` | `amber_powerwall_cost_electricity_net` | Cost Electricity Net |
| `battery_automation_decision_log` | `amber_powerwall_decision_log` | Decision Log |
| `forecast_history` | `amber_powerwall_forecast_history` | Forecast History |
| `daily_forecast` | `amber_powerwall_forecast_daily` | Forecast Daily |
| `minimum_target_soc` | `amber_powerwall_target_soc_minimum` | Target SOC Minimum |

---

## Binary Sensor Mapping

| Current unique_id | New unique_id | New Display Name |
|-------------------|---------------|------------------|
| `forecast_spike_within_window` | `amber_powerwall_binary_price_spike_coming` | Binary Price Spike Coming |
| `battery_force_discharge_active` | `amber_powerwall_binary_discharge_forced` | Binary Discharge Forced |
| `battery_force_charge_active` | `amber_powerwall_binary_charge_forced` | Binary Charge Forced |
| `battery_boost_charge_active` | `amber_powerwall_binary_charge_boost` | Binary Charge Boost |
| `forecast_expensive_period_coming` | `amber_powerwall_binary_price_expensive_coming` | Binary Price Expensive Coming |
| `solar_can_reach_target` | `amber_powerwall_binary_solar_can_reach_target` | Binary Solar Can Reach Target |
| `boost_charge_needed` | `amber_powerwall_binary_charge_boost_needed` | Binary Charge Boost Needed |
| `demand_window_active` | `amber_powerwall_binary_demand_window` | Binary Demand Window |

---

## Steps to Implement

1. Refactor `sensor.py` - update all sensor unique_id and _attr_name values
2. Refactor `binary_sensor.py` - update all binary sensor unique_id and _attr_name values
3. Optionally refactor buttons, numbers, switches for consistency
4. Run pre-commit hooks to verify code quality
5. Update `docs/ENTITY_REFERENCE.md` documentation
6. Note: This is a breaking change - entity IDs will change in Home Assistant

---

## Notes

- This is a MEDIUM priority item (code quality/maintainability)
- It is a BREAKING CHANGE - users will see new entities after upgrade
- Consider adding migration notes or aliases for backward compatibility
- Related to existing entity naming patterns in buttons/numbers/switches

---

## Related Items

- N/A
