# Phase 3: Entity Naming Implementation (backlog-med-010)

**Phase:** 3 of 13  
**Status:** NOT STARTED  
**Estimated Time:** 75 minutes (expanded scope)

---

## Overview

This phase implements backlog-med-010 by updating all entity unique_ids and display names to follow the new `localshift_{category}_{specific_name}` convention.

**CRITICAL DISCOVERY:** The codebase has inconsistent entity naming:
- Sensors/Binary Sensors: NO prefix (e.g., `effective_cheap_price`)
- Buttons/Switches/Numbers: ALREADY have `amber_powerwall_` prefix

**New Convention Applied Uniformly:**
- All entities get `localshift_{category}_{specific_name}` format

**Total Entities to Update:** 37
- 12 Sensors (currently NO prefix)
- 8 Binary Sensors (currently NO prefix)
- 5 Buttons (currently HAS prefix - must change)
- 7 Number Entities (currently HAS prefix - must change)
- 5 Switches (currently HAS prefix - must change)

---

## 3.1 sensor.py (12 sensors)

**File:** `custom_components/localshift/sensor.py`

### CRITICAL: Current State

Sensors currently have NO prefix:
```python
_attr_unique_id = "effective_cheap_price"  # NO prefix
_attr_name = "Effective Cheap Price"
```

### Entity Mapping (12 sensors)

| Current unique_id | New unique_id | Current Name | New Name |
|-------------------|---------------|--------------|----------|
| `effective_cheap_price` | `localshift_price_cheap_effective` | `Effective Cheap Price` | `Price Cheap Effective` |
| `cheap_charge_stop_price` | `localshift_price_cheap_charge_stop` | `Cheap Charge Stop Price` | `Price Cheap Charge Stop` |
| `solar_weighted_avg_fit` | `localshift_solar_weighted_avg_fit` | `Solar Weighted Avg FIT` | `Solar Weighted Avg FIT` |
| `battery_automation_active_mode` | `localshift_battery_mode` | `Active Mode` | `Battery Mode` |
| `solar_battery_forecast` | `localshift_forecast_battery` | `Solar Battery Forecast` | `Forecast Battery` |
| `grid_import_power` | `localshift_power_grid_import` | `Grid Import Power` | `Power Grid Import` |
| `grid_export_power` | `localshift_power_grid_export` | `Grid Export Power` | `Power Grid Export` |
| `net_electricity_cost_today` | `localshift_cost_electricity_net` | `Net Electricity Cost Today` | `Cost Electricity Net` |
| `battery_automation_decision_log` | `localshift_decision_log` | `Decision Log` | `Decision Log` |
| `forecast_history` | `localshift_forecast_history` | `Forecast History` | `Forecast History` |
| `daily_forecast` | `localshift_forecast_daily` | `Daily Forecast` | `Forecast Daily` |
| `minimum_target_soc` | `localshift_target_soc_minimum` | `Minimum Target SOC` | `Target SOC Minimum` |

### Implementation - Each Sensor

#### Sensor 1: EffectiveCheapPriceSensor
```python
# SEARCH
class EffectiveCheapPriceSensor(AmberPowerwallSensorBase):
    """Dynamic cheap price threshold (urgency-adjusted)."""

    _attr_unique_id = "effective_cheap_price"
    _attr_name = "Effective Cheap Price"

# REPLACE
class EffectiveCheapPriceSensor(LocalShiftSensorBase):
    """Dynamic cheap price threshold (urgency-adjusted)."""

    _attr_unique_id = "localshift_price_cheap_effective"
    _attr_name = "Price Cheap Effective"
```

#### Sensor 2: CheapChargeStopPriceSensor
```python
# SEARCH
class CheapChargeStopPriceSensor(AmberPowerwallSensorBase):
    """Effective threshold + deadband."""

    _attr_unique_id = "cheap_charge_stop_price"
    _attr_name = "Cheap Charge Stop Price"

# REPLACE
class CheapChargeStopPriceSensor(LocalShiftSensorBase):
    """Effective threshold + deadband."""

    _attr_unique_id = "localshift_price_cheap_charge_stop"
    _attr_name = "Price Cheap Charge Stop"
```

#### Sensor 3: SolarWeightedAvgFITSensor
```python
# SEARCH
class SolarWeightedAvgFITSensor(AmberPowerwallSensorBase):
    """Solar-production-weighted average feed-in tariff."""

    _attr_unique_id = "solar_weighted_avg_fit"
    _attr_name = "Solar Weighted Avg FIT"

# REPLACE
class SolarWeightedAvgFITSensor(LocalShiftSensorBase):
    """Solar-production-weighted average feed-in tariff."""

    _attr_unique_id = "localshift_solar_weighted_avg_fit"
    _attr_name = "Solar Weighted Avg FIT"
```

#### Sensor 4: ActiveModeSensor
```python
# SEARCH
class ActiveModeSensor(AmberPowerwallSensorBase):
    """Current battery automation mode."""

    _attr_unique_id = "battery_automation_active_mode"
    _attr_name = "Active Mode"

# REPLACE
class ActiveModeSensor(LocalShiftSensorBase):
    """Current battery automation mode."""

    _attr_unique_id = "localshift_battery_mode"
    _attr_name = "Battery Mode"
```

#### Sensor 5: SolarBatteryForecastSensor
```python
# SEARCH
class SolarBatteryForecastSensor(AmberPowerwallSensorBase):
    """Solar battery SOC forecast with detailed attributes."""

    _attr_unique_id = "solar_battery_forecast"
    _attr_name = "Solar Battery Forecast"

# REPLACE
class SolarBatteryForecastSensor(LocalShiftSensorBase):
    """Solar battery SOC forecast with detailed attributes."""

    _attr_unique_id = "localshift_forecast_battery"
    _attr_name = "Forecast Battery"
```

#### Sensor 6: GridImportPowerSensor
```python
# SEARCH
class GridImportPowerSensor(AmberPowerwallSensorBase):
    """Grid import power (always >= 0)."""

    _attr_unique_id = "grid_import_power"
    _attr_name = "Grid Import Power"

# REPLACE
class GridImportPowerSensor(LocalShiftSensorBase):
    """Grid import power (always >= 0)."""

    _attr_unique_id = "localshift_power_grid_import"
    _attr_name = "Power Grid Import"
```

#### Sensor 7: GridExportPowerSensor
```python
# SEARCH
class GridExportPowerSensor(AmberPowerwallSensorBase):
    """Grid export power (always >= 0)."""

    _attr_unique_id = "grid_export_power"
    _attr_name = "Grid Export Power"

# REPLACE
class GridExportPowerSensor(LocalShiftSensorBase):
    """Grid export power (always >= 0)."""

    _attr_unique_id = "localshift_power_grid_export"
    _attr_name = "Power Grid Export"
```

#### Sensor 8: NetElectricityCostSensor
```python
# SEARCH
class NetElectricityCostSensor(AmberPowerwallSensorBase):
    """Net electricity cost today (import cost - export revenue)."""

    _attr_unique_id = "net_electricity_cost_today"
    _attr_name = "Net Electricity Cost Today"

# REPLACE
class NetElectricityCostSensor(LocalShiftSensorBase):
    """Net electricity cost today (import cost - export revenue)."""

    _attr_unique_id = "localshift_cost_electricity_net"
    _attr_name = "Cost Electricity Net"
```

#### Sensor 9: DecisionLogSensor
```python
# SEARCH
class DecisionLogSensor(AmberPowerwallSensorBase):
    """Battery mode change decision log."""

    _attr_unique_id = "battery_automation_decision_log"
    _attr_name = "Decision Log"

# REPLACE
class DecisionLogSensor(LocalShiftSensorBase):
    """Battery mode change decision log."""

    _attr_unique_id = "localshift_decision_log"
    _attr_name = "Decision Log"
```

#### Sensor 10: ForecastHistorySensor
```python
# SEARCH
class ForecastHistorySensor(AmberPowerwallSensorBase):
    """Historical forecast predictions for planned vs actual comparison."""

    _attr_unique_id = "forecast_history"
    _attr_name = "Forecast History"

# REPLACE
class ForecastHistorySensor(LocalShiftSensorBase):
    """Historical forecast predictions for planned vs actual comparison."""

    _attr_unique_id = "localshift_forecast_history"
    _attr_name = "Forecast History"
```

#### Sensor 11: DailyForecastSensor
```python
# SEARCH
class DailyForecastSensor(AmberPowerwallSensorBase):
    """Full 24-hour forecast with hourly breakdown."""

    _attr_unique_id = "daily_forecast"
    _attr_name = "Daily Forecast"

# REPLACE
class DailyForecastSensor(LocalShiftSensorBase):
    """Full 24-hour forecast with hourly breakdown."""

    _attr_unique_id = "localshift_forecast_daily"
    _attr_name = "Forecast Daily"
```

#### Sensor 12: MinimumTargetSOCSensor
```python
# SEARCH
class MinimumTargetSOCSensor(AmberPowerwallSensorBase):
    """Minimum target SOC for discharge modes (base reserve)."""

    _attr_unique_id = "minimum_target_soc"
    _attr_name = "Minimum Target SOC"

# REPLACE
class MinimumTargetSOCSensor(LocalShiftSensorBase):
    """Minimum target SOC for discharge modes (base reserve)."""

    _attr_unique_id = "localshift_target_soc_minimum"
    _attr_name = "Target SOC Minimum"
```

### Checklist for sensor.py
- [ ] Updated all 12 sensor unique_id values
- [ ] Updated all 12 sensor _attr_name values
- [ ] Updated base class reference: `AmberPowerwallSensorBase` → `LocalShiftSensorBase`
- [ ] Updated module docstring (line 1)
- [ ] Saved file

---

## 3.2 binary_sensor.py (8 binary sensors)

**File:** `custom_components/localshift/binary_sensor.py`

### CRITICAL: Current State

Binary sensors currently have NO prefix:
```python
_attr_unique_id = "forecast_spike_within_window"  # NO prefix
_attr_name = "Forecast Spike Within Window"
```

### Entity Mapping (8 binary sensors)

| Current unique_id | New unique_id | Current Name | New Name |
|-------------------|---------------|--------------|----------|
| `forecast_spike_within_window` | `localshift_binary_price_spike_coming` | `Forecast Spike Within Window` | `Binary Price Spike Coming` |
| `battery_force_discharge_active` | `localshift_binary_discharge_forced` | `Force Discharge Active` | `Binary Discharge Forced` |
| `battery_force_charge_active` | `localshift_binary_charge_forced` | `Force Charge Active` | `Binary Charge Forced` |
| `battery_boost_charge_active` | `localshift_binary_charge_boost` | `Boost Charge Active` | `Binary Charge Boost` |
| `forecast_expensive_period_coming` | `localshift_binary_price_expensive_coming` | `Expensive Period Coming` | `Binary Price Expensive Coming` |
| `solar_can_reach_target` | `localshift_binary_solar_can_reach_target` | `Solar Can Reach Target` | `Binary Solar Can Reach Target` |
| `boost_charge_needed` | `localshift_binary_charge_boost_needed` | `Boost Charge Needed` | `Binary Charge Boost Needed` |
| `demand_window_active` | `localshift_binary_demand_window` | `Demand Window Active` | `Binary Demand Window` |

### Implementation - Each Binary Sensor

#### Binary Sensor 1: ForecastSpikeWithinWindowSensor
```python
# SEARCH
class ForecastSpikeWithinWindowSensor(AmberPowerwallBinarySensorBase):
    """Whether a price spike is forecast within the lookahead window."""

    _attr_unique_id = "forecast_spike_within_window"
    _attr_name = "Forecast Spike Within Window"

# REPLACE
class ForecastSpikeWithinWindowSensor(LocalShiftBinarySensorBase):
    """Whether a price spike is forecast within the lookahead window."""

    _attr_unique_id = "localshift_binary_price_spike_coming"
    _attr_name = "Binary Price Spike Coming"
```

#### Binary Sensor 2: ForceDischargeActiveSensor
```python
# SEARCH
class ForceDischargeActiveSensor(AmberPowerwallBinarySensorBase):
    """Whether battery is currently force discharging."""

    _attr_unique_id = "battery_force_discharge_active"
    _attr_name = "Force Discharge Active"

# REPLACE
class ForceDischargeActiveSensor(LocalShiftBinarySensorBase):
    """Whether battery is currently force discharging."""

    _attr_unique_id = "localshift_binary_discharge_forced"
    _attr_name = "Binary Discharge Forced"
```

#### Binary Sensor 3: ForceChargeActiveSensor
```python
# SEARCH
class ForceChargeActiveSensor(AmberPowerwallBinarySensorBase):
    """Whether battery is currently force charging (backup mode)."""

    _attr_unique_id = "battery_force_charge_active"
    _attr_name = "Force Charge Active"

# REPLACE
class ForceChargeActiveSensor(LocalShiftBinarySensorBase):
    """Whether battery is currently force charging (backup mode)."""

    _attr_unique_id = "localshift_binary_charge_forced"
    _attr_name = "Binary Charge Forced"
```

#### Binary Sensor 4: BoostChargeActiveSensor
```python
# SEARCH
class BoostChargeActiveSensor(AmberPowerwallBinarySensorBase):
    """Whether battery is currently boost charging (5kW)."""

    _attr_unique_id = "battery_boost_charge_active"
    _attr_name = "Boost Charge Active"

# REPLACE
class BoostChargeActiveSensor(LocalShiftBinarySensorBase):
    """Whether battery is currently boost charging (5kW)."""

    _attr_unique_id = "localshift_binary_charge_boost"
    _attr_name = "Binary Charge Boost"
```

#### Binary Sensor 5: ForecastExpensivePeriodSensor
```python
# SEARCH
class ForecastExpensivePeriodSensor(AmberPowerwallBinarySensorBase):
    """Whether an expensive period is forecast within lookahead."""

    _attr_unique_id = "forecast_expensive_period_coming"
    _attr_name = "Expensive Period Coming"

# REPLACE
class ForecastExpensivePeriodSensor(LocalShiftBinarySensorBase):
    """Whether an expensive period is forecast within lookahead."""

    _attr_unique_id = "localshift_binary_price_expensive_coming"
    _attr_name = "Binary Price Expensive Coming"
```

#### Binary Sensor 6: SolarCanReachTargetSensor
```python
# SEARCH
class SolarCanReachTargetSensor(AmberPowerwallBinarySensorBase):
    """Whether solar forecast can fill battery to target by demand window."""

    _attr_unique_id = "solar_can_reach_target"
    _attr_name = "Solar Can Reach Target"

# REPLACE
class SolarCanReachTargetSensor(LocalShiftBinarySensorBase):
    """Whether solar forecast can fill battery to target by demand window."""

    _attr_unique_id = "localshift_binary_solar_can_reach_target"
    _attr_name = "Binary Solar Can Reach Target"
```

#### Binary Sensor 7: BoostChargeNeededSensor
```python
# SEARCH
class BoostChargeNeededSensor(AmberPowerwallBinarySensorBase):
    """Whether 3.3kW charge rate is insufficient (need 5kW boost)."""

    _attr_unique_id = "boost_charge_needed"
    _attr_name = "Boost Charge Needed"

# REPLACE
class BoostChargeNeededSensor(LocalShiftBinarySensorBase):
    """Whether 3.3kW charge rate is insufficient (need 5kW boost)."""

    _attr_unique_id = "localshift_binary_charge_boost_needed"
    _attr_name = "Binary Charge Boost Needed"
```

#### Binary Sensor 8: DemandWindowActiveSensor
```python
# SEARCH
class DemandWindowActiveSensor(AmberPowerwallBinarySensorBase):
    """Whether the demand window is currently active."""

    _attr_unique_id = "demand_window_active"
    _attr_name = "Demand Window Active"

# REPLACE
class DemandWindowActiveSensor(LocalShiftBinarySensorBase):
    """Whether the demand window is currently active."""

    _attr_unique_id = "localshift_binary_demand_window"
    _attr_name = "Binary Demand Window"
```

### Checklist for binary_sensor.py
- [ ] Updated all 8 binary sensor unique_id values
- [ ] Updated all 8 binary sensor _attr_name values
- [ ] Updated base class reference: `AmberPowerwallBinarySensorBase` → `LocalShiftBinarySensorBase`
- [ ] Updated module docstring (line 1)
- [ ] Saved file

---

## 3.3 button.py (5 buttons)

**File:** `custom_components/localshift/button.py`

### CRITICAL: Current State

Buttons ALREADY have `amber_powerwall_` prefix:
```python
self._attr_unique_id = f"amber_powerwall_{key}"  # HAS prefix
self._attr_name = BUTTON_NAMES[key]
```

Must change to new convention with category.

### Entity Mapping (5 buttons)

| Current unique_id | New unique_id | New Name |
|-------------------|---------------|----------|
| `amber_powerwall_force_charge` | `localshift_button_force_charge` | `Button Force Charge` |
| `amber_powerwall_force_discharge` | `localshift_button_force_discharge` | `Button Force Discharge` |
| `amber_powerwall_boost_charge` | `localshift_button_boost_charge` | `Button Boost Charge` |
| `amber_powerwall_self_consumption` | `localshift_button_self_consumption` | `Button Self Consumption` |
| `amber_powerwall_update_forecast` | `localshift_button_update_forecast` | `Button Update Forecast` |

### Implementation

The unique_id is constructed in `AmberPowerwallButtonBase.__init__`:

```python
# SEARCH
class AmberPowerwallButtonBase(ButtonEntity):
    """Base class for manual mode buttons."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AmberPowerwallCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialise the button."""
        self.coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"amber_powerwall_{key}"
        self._attr_name = BUTTON_NAMES[key]
        self._attr_icon = BUTTON_ICONS[key]

# REPLACE
class LocalShiftButtonBase(ButtonEntity):
    """Base class for manual mode buttons."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialise the button."""
        self.coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"localshift_button_{key}"
        self._attr_name = f"Button {BUTTON_NAMES[key]}"
        self._attr_icon = BUTTON_ICONS[key]
```

### Update Button Names in const.py

```python
# SEARCH
BUTTON_NAMES = {
    BUTTON_FORCE_CHARGE: "Force Charge",
    BUTTON_FORCE_DISCHARGE: "Force Discharge",
    BUTTON_BOOST_CHARGE: "Boost Charge (5kW)",
    BUTTON_SELF_CONSUMPTION: "Return to Self Consumption",
    BUTTON_UPDATE_FORECAST: "Update Forecast",
}

# REPLACE
BUTTON_NAMES = {
    BUTTON_FORCE_CHARGE: "Force Charge",
    BUTTON_FORCE_DISCHARGE: "Force Discharge",
    BUTTON_BOOST_CHARGE: "Boost Charge",
    BUTTON_SELF_CONSUMPTION: "Self Consumption",
    BUTTON_UPDATE_FORECAST: "Update Forecast",
}
```

### Checklist for button.py
- [ ] Updated base class: `AmberPowerwallButtonBase` → `LocalShiftButtonBase`
- [ ] Updated unique_id format: `f"amber_powerwall_{key}"` → `f"localshift_button_{key}"`
- [ ] Updated name format to include "Button" prefix
- [ ] Updated module docstring
- [ ] Saved file

---

## 3.4 number.py (7 number entities)

**File:** `custom_components/localshift/number.py`

### CRITICAL: Current State

Numbers ALREADY have `amber_powerwall_` prefix:
```python
self._attr_unique_id = f"amber_powerwall_{conf_key}"  # HAS prefix
```

Must change to new convention with category.

### Entity Mapping (7 numbers)

| Current unique_id | New unique_id | New Name |
|-------------------|---------------|----------|
| `amber_powerwall_cheap_price_percentile` | `localshift_number_cheap_price_percentile` | `Number Cheap Price Percentile` |
| `amber_powerwall_max_precharge_price` | `localshift_number_max_pre_charge_price` | `Number Max Pre Charge Price` |
| `amber_powerwall_cheap_price_deadband` | `localshift_number_price_deadband` | `Number Price Deadband` |
| `amber_powerwall_forecast_lookahead_hours` | `localshift_number_forecast_lookahead` | `Number Forecast Lookahead` |
| `amber_powerwall_precharge_battery_threshold` | `localshift_number_pre_charge_battery_threshold` | `Number Pre Charge Battery Threshold` |
| `amber_powerwall_battery_target` | `localshift_number_battery_target` | `Number Battery Target` |
| `amber_powerwall_load_weight_recent` | `localshift_number_load_weight_recent` | `Number Load Weight Recent` |

### Implementation

```python
# SEARCH
class AmberPowerwallNumber(NumberEntity):
    """A user-configurable threshold backed by config entry options."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AmberPowerwallCoordinator,
        entry: ConfigEntry,
        conf_key: str,
        name: str,
        default: float,
    ) -> None:
        """Initialise the number entity."""
        self.coordinator = coordinator
        self._entry = entry
        self._conf_key = conf_key
        self._default = default

        spec = THRESHOLD_RANGES[conf_key]
        self._attr_unique_id = f"amber_powerwall_{conf_key}"
        self._attr_name = name

# REPLACE
class LocalShiftNumber(NumberEntity):
    """A user-configurable threshold backed by config entry options."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
        entry: ConfigEntry,
        conf_key: str,
        name: str,
        default: float,
    ) -> None:
        """Initialise the number entity."""
        self.coordinator = coordinator
        self._entry = entry
        self._conf_key = conf_key
        self._default = default

        spec = THRESHOLD_RANGES[conf_key]
        self._attr_unique_id = f"localshift_number_{conf_key}"
        self._attr_name = f"Number {name}"
```

### Checklist for number.py
- [ ] Updated class name: `AmberPowerwallNumber` → `LocalShiftNumber`
- [ ] Updated unique_id format: `f"amber_powerwall_{conf_key}"` → `f"localshift_number_{conf_key}"`
- [ ] Updated name format to include "Number" prefix
- [ ] Updated module docstring
- [ ] Saved file

---

## 3.5 switch.py (5 switches)

**File:** `custom_components/localshift/switch.py`

### CRITICAL: Current State

Switches ALREADY have `amber_powerwall_` prefix:
```python
self._attr_unique_id = f"amber_powerwall_{key}"  # HAS prefix
```

Must change to new convention with category.

### Entity Mapping (5 switches)

| Current unique_id | New unique_id | New Name |
|-------------------|---------------|----------|
| `amber_powerwall_automation_enabled` | `localshift_switch_automation_enabled` | `Switch Automation Enabled` |
| `amber_powerwall_spike_discharge_enabled` | `localshift_switch_spike_discharge_enabled` | `Switch Spike Discharge Enabled` |
| `amber_powerwall_dry_run` | `localshift_switch_dry_run` | `Switch Dry Run` |
| `amber_powerwall_demand_window_block` | `localshift_switch_demand_window_block` | `Switch Demand Window Block` |
| `amber_powerwall_allow_dw_entry_under_target` | `localshift_switch_allow_dw_entry_under_target` | `Switch Allow DW Entry Under Target` |

### Implementation

```python
# SEARCH
class AmberPowerwallSwitch(SwitchEntity):
    """A toggle switch for automation features."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AmberPowerwallCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialise the switch."""
        self.coordinator = coordinator
        self._entry = entry
        self._key = key

        # Load persisted state from options, or use default
        option_key = f"{SWITCH_STATE_PREFIX}{key}"
        self._is_on = self._entry.options.get(option_key, SWITCH_DEFAULTS[key])

        self._attr_unique_id = f"amber_powerwall_{key}"
        self._attr_name = SWITCH_NAMES[key]

# REPLACE
class LocalShiftSwitch(SwitchEntity):
    """A toggle switch for automation features."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LocalShiftCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Initialise the switch."""
        self.coordinator = coordinator
        self._entry = entry
        self._key = key

        # Load persisted state from options, or use default
        option_key = f"{SWITCH_STATE_PREFIX}{key}"
        self._is_on = self._entry.options.get(option_key, SWITCH_DEFAULTS[key])

        self._attr_unique_id = f"localshift_switch_{key}"
        self._attr_name = f"Switch {SWITCH_NAMES[key]}"
```

### Checklist for switch.py
- [ ] Updated class name: `AmberPowerwallSwitch` → `LocalShiftSwitch`
- [ ] Updated unique_id format: `f"amber_powerwall_{key}"` → `f"localshift_switch_{key}"`
- [ ] Updated name format to include "Switch" prefix
- [ ] Updated module docstring
- [ ] Saved file

---

## Phase 3 Completion Checklist

### Files Updated
- [ ] sensor.py: 12 sensors updated
- [ ] binary_sensor.py: 8 binary sensors updated
- [ ] button.py: 5 buttons updated
- [ ] number.py: 7 numbers updated
- [ ] switch.py: 5 switches updated

### Verification
- [ ] All unique_ids follow `localshift_{category}_{name}` pattern
- [ ] All display names include category word
- [ ] All base class names updated
- [ ] All module docstrings updated
- [ ] Pre-commit passes on all files

---

**Phase Status:** ☐ NOT STARTED | ☐ IN PROGRESS | ☐ COMPLETED