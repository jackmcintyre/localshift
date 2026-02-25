# Entity Reference

Complete reference for all Home Assistant entities provided by the LocalShift integration.

## Overview

The integration creates **67 entities** grouped under a single "LocalShift" device:

| Category | Count | Entity Type |
|----------|-------|-------------|
| Sensors | 29 | `sensor` |
| Binary Sensors | 13 | `binary_sensor` |
| Switches | 13 | `switch` |
| Numbers | 6 | `number` |
| Buttons | 6 | `button` |

**Note:** Grid import/export power values are available as computed values in `CoordinatorData` but are not exposed as separate sensor entities. They can be accessed via template sensors if needed.

---

## Sensors

### 1. sensor.localshift_price_cheap_effective

**Purpose:** Dynamic cheap price threshold that adjusts based on urgency.

The effective cheap price is calculated from the base cheap price percentile, then elevated based on how close the demand window is and whether solar can reach the target.

**State:** Current effective cheap price threshold in $/kWh

**Example Data:**
```
State: 0.09
```

**Attributes:**

| Attribute | Type | Description | Example |
|-----------|------|-------------|---------|
| `state_class` | string | Always `measurement` | `measurement` |
| `unit_of_measurement` | string | Always `$/kWh` | `$/kWh` |

---

### 2. sensor.localshift_price_cheap_charge_stop

**Purpose:** Upper boundary of the deadband zone.

**State:** Price threshold in $/kWh where charging should stop

**Example Data:**
```
State: 0.12
```

**Calculation:** `effective_cheap_price + cheap_price_deadband`

This creates a hysteresis zone:
- Charge when price ≤ effective_cheap_price
- Hold when effective_cheap_price < price < stop_price
- Self-consumption when price ≥ stop_price

---

### 3. sensor.localshift_solar_weighted_avg_fit

**Purpose:** Solar-production-weighted average feed-in tariff for remaining daylight hours.

**State:** Weighted average FIT in $/kWh

**Example Data:**
```
State: -0.0053
```

**Attributes:**

| Attribute | Type | Description | Example |
|-----------|------|-------------|---------|
| `total_solar_remaining_kwh` | float | Estimated solar production remaining today (kWh) | `3.5` |

---

### 4. sensor.localshift_battery_mode

**Purpose:** Current battery automation mode from the state machine.

**State:** One of the following mode strings:

| Mode | Description |
|------|-------------|
| `self_consumption` | Default — battery powers house, grid used as needed |
| `grid_charging` | Force charging from grid at 3.3kW (backup mode) |
| `boost_charging` | Force charging at 5kW (autonomous + reserve=100%) |
| `spike_discharge` | Force discharging to export during price spikes |
| `proactive_export` | Exporting battery before negative FIT periods |
| `demand_block` | Self consumption enforced during demand window |
| `manual` | Automation disabled, user has manual control |

**Example Data:**
```
State: grid_charging
```

**How to Use:** This is the primary sensor for understanding what the automation is doing. Check this first when debugging.

---

### 5. sensor.localshift_forecast_battery

**Purpose:** Predicted battery SOC at demand window start.

**State Class:** `measurement` — Supports long-term statistics (Issue #266)

**State:** Predicted SOC percentage at the demand window start time

**Example Data:**
```
State: 88.8
Attributes:
  predicted_soc: 88.8
  solar_before_dw_kwh: 3.5
  consumption_estimate_kwh: 12.22
  net_solar_kwh: -8.72
  deficit_kwh: 8.24
  can_reach_target: false
  boost_needed: true
  hours_to_target_time: 5.8
  target_reached_today: false
```

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `predicted_soc` | float | Predicted SOC at demand window |
| `solar_before_dw_kwh` | float | Solar energy expected before DW |
| `consumption_estimate_kwh` | float | Estimated consumption before DW |
| `net_solar_kwh` | float | Net energy (solar - consumption) |
| `deficit_kwh` | float | kWh gap to reach target |
| `can_reach_target` | bool | Whether solar alone can fill battery |
| `boost_needed` | bool | Whether 5kW boost is needed |
| `hours_to_target_time` | float | Hours until demand window |
| `target_reached_today` | bool | Whether target was reached today |

---

### 6. sensor.localshift_cost_electricity_net

**Purpose:** Net cost for the day (import cost - export revenue).

**State:** Net cost in $ (negative means profit)

**Example Data:**
```
State: 0.76
Attributes:
  grid_import_cost: 0.76
  grid_export_revenue: 0.0
  battery_savings: 0.37
  battery_charge_cost: 0.02
```

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `grid_import_cost` | float | Total cost of grid imports today ($) |
| `grid_export_revenue` | float | Total revenue from exports today ($) |
| `battery_savings` | float | Value of solar self-consumed ($) |
| `battery_charge_cost` | float | Cost to charge battery from grid ($) |

---

### 7. sensor.localshift_decision_log

**Purpose:** History of mode changes with human-readable reasons.

**State:** Most recent decision reason

**Example Data:**
```
State: Mode changed: Boost Charging -> Grid Charging
Attributes:
  reason: Mode changed: Boost Charging -> Grid Charging
  soc: 34
  buy_price: 0.09
  sell_price: 0.02
  timestamp: 2026-02-26T09:05:37.417348+11:00
  history: [...last 10 decisions...]
```

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `reason` | string | Reason for most recent decision |
| `soc` | float | Battery SOC at time of decision |
| `buy_price` | float | Buy price at time of decision |
| `sell_price` | float | Feed-in price at time of decision |
| `timestamp` | datetime | When decision was made |
| `history` | list | Last 10 decisions |

---

### 8. sensor.localshift_forecast_history

**Purpose:** Historical forecast predictions for comparison with actuals.

**State:** Count of stored predictions

**Example Data:**
```
State: 200
```

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `history` | list | Array of historical forecast snapshots |

---

### 9. sensor.localshift_forecast_daily

**Purpose:** Core 24-hour forecast with SOC, solar, and consumption data.

Split from the original monolithic sensor to stay under Home Assistant's 16KB attribute limit (Issue #37). Related sensors: `forecast_prices`, `forecast_grid`, `forecast_diagnostics`.

**State:** Count of forecast slots (96 × 15-min slots)

**Example Data:**
```
State: 96
Attributes:
  slot_count: 96
  solcast_today_entries: 48
  solcast_tomorrow_entries: 48
  forecast_slots: [...96 slots...]
  soc_series: [...96 entries...]
  forecast_hourly: [...24 entries...]
```

**Forecast Slot Structure:**

Each slot in `forecast_slots` contains:

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `time` | string | HH:MM format | `09:05` |
| `hour` | int | Hour (0-23) | `9` |
| `minute` | int | Minute (0, 15, 30, 45) | `5` |
| `predicted_soc` | float | Predicted battery SOC (%) | `33.9` |
| `solar_kwh` | float | Solar production (kWh) | `0.7259` |
| `consumption_kwh` | float | Estimated consumption (kWh) | `0.2522` |
| `net_kwh` | float | Net energy (solar - consumption) | `0.4737` |
| `buy_price` | float | Buy price ($/kWh) | `0.07` |
| `sell_price` | float | Sell price ($/kWh) | `0.01` |

---

### 10. sensor.localshift_forecast_prices

**Purpose:** Price forecast data for history collection.

Split from `forecast_daily` to stay under 16KB limit (Issue #37).

**State Class:** `measurement` — Supports long-term statistics (Issue #266)

**State:** Current effective cheap price ($/kWh)

**Example Data:**
```
State: 0.09
Attributes:
  effective_cheap_price: 0.09
  cheap_charge_stop_price: 0.12
  forecast_import_cost: 0.92
  forecast_export_revenue: -0.01
  forecast_net_cost: 0.93
  forecast_grid_charge_cost: 0.59
  forecast_proactive_export_revenue: 0.0
  buy_prices: [...96 slots...]
  sell_prices: [...96 slots...]
```

---

### 11. sensor.localshift_forecast_grid

**Purpose:** Grid interaction forecast data for history collection.

Split from `forecast_daily` to stay under 16KB limit (Issue #37).

**State Class:** `measurement` — Supports long-term statistics (Issue #266)

**State:** Total forecast grid import (kWh)

**Example Data:**
```
State: 22.245
Attributes:
  total_grid_import_kwh: 22.245
  total_grid_export_kwh: 1.206
  grid_charge_slots: 23
  proactive_export_slots: 0
  grid_interaction: [...96 slots...]
```

**Grid Interaction Slot Structure:**

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `time` | string | HH:MM format | `09:05` |
| `grid_import_kwh` | float | Grid import (kWh) | `0.825` |
| `grid_export_kwh` | float | Grid export (kWh) | `0.0` |
| `grid_charge` | bool | Grid charging active | `true` |
| `grid_charge_boost` | bool | Boost charging active | `true` |
| `proactive_export` | bool | Proactive export active | `false` |

---

### 12. sensor.localshift_forecast_diagnostics

**Purpose:** Diagnostic and debug data for the forecast system.

Split from `forecast_daily` to stay under 16KB limit (Issue #37).

**State:** Consumption data source (e.g., "statistics", "profile_hour")

**Example Data:**
```
State: statistics
Attributes:
  consumption_source: statistics
  consumption_statistic_id: sensor.my_home_load_power
  consumption_profile_hours: 24
  consumption_weighting: 0.67
  current_load_kw: 0.791
  weather_entity_id: weather.crows_nest_hourly
  weather_temperature_current: 21.6
  weather_condition: rainy
  weather_correlation_confidence: medium
  weather_learning_enabled: true
  weather_cooling_coefficient: 0.3022
  weather_sample_count: 5723
```

---

### 13. sensor.localshift_target_soc_minimum

**Purpose:** Minimum target SOC for discharge modes (base reserve).

**State:** Configured minimum SOC percentage

**Example Data:**
```
State: 10.0
```

**Configuration:** Set via `number.localshift_minimum_target_soc`

This is the floor SOC maintained during spike discharge and proactive export modes to protect battery health and ensure reserve capacity.

---

### 14. sensor.localshift_excess_solar

**Purpose:** Forecasted excess solar energy available for discretionary loads.

**State:** Total excess solar until battery reaches 100% (kWh)

**Example Data:**
```
State: 0.0
Attributes:
  excess_current_hour_kwh: 2.07
  excess_next_2h_kwh: 3.85
  excess_next_4h_kwh: 4.6
  excess_until_battery_full_kwh: 0.0
  can_add_load_now: false
  safe_additional_load_kw: 0.0
  forecast_confidence: high
  current_excess_rate_kw: 0.26
  negative_fit_window_start: 2026-02-26T09:30:00+11:00
  negative_fit_window_duration_minutes: 320
```

**Use Case:** Automate discretionary loads (AC, pool pump, EV) to consume excess solar that would otherwise export at negative or low FIT prices.

---

### 15. sensor.localshift_load_shift_signal

**Purpose:** Simple actionable signal for load-shifting automations.

**State:** One of the following signals:

| Signal | Meaning | Action |
|--------|---------|--------|
| `INCREASE_LOAD` | Excess solar available | Turn on discretionary loads |
| `MAINTAIN_LOAD` | Current balance is good | No changes needed |
| `REDUCE_LOAD` | Risk of grid charging | Turn off discretionary loads |
| `HOLD` | No signal / initializing | Wait for data |

**Example Data:**
```
State: REDUCE_LOAD
Attributes:
  recommended_additional_kw: -1.0
  recommended_duration_minutes: 60
  signal_reason: Current load may trigger grid charging
  signal_confidence: high
  current_excess_rate_kw: 0.26
  grid_charge_risk: true
  safe_additional_load_kw: 0.0
```

**Icon:** Dynamic based on signal (arrow-up-bold, arrow-down-bold, check-circle, pause-circle)

---

### 16. sensor.localshift_forecast_accuracy

**Purpose:** Compares past forecast predictions with actual outcomes to track accuracy.

Added in Issue #37 Phase 2 to monitor how well the forecast system predicts SOC and prices.

**State Class:** `measurement` — Supports long-term statistics (Issue #266)

**State:** SOC accuracy percentage for 1-hour predictions

**Example Data:**
```
State: 92.9
Attributes:
  soc_error_15min: 4.3
  soc_error_1h: 7.1
  soc_error_4h: 0.0
  soc_accuracy_15min: 95.7
  soc_accuracy_1h: 92.9
  soc_accuracy_4h: 100.0
  buy_price_error_1h: -0.03
  sell_price_error_1h: -0.02
  comparisons_made: 604
  last_comparison_time: 2026-02-26T09:08:34+11:00
  first_prediction_time: 2026-02-22T17:50:14+11:00
  history_count: 203
```

---

### 17. sensor.localshift_integration_status

**Purpose:** Overall integration health status.

Added in Issue #94 to provide a simple status indicator for dashboards.

**State:** One of the following status strings:

| Status | Meaning |
|--------|---------|
| `ok` | All required entities are available |
| `degraded` | Some optional entities unavailable |
| `error` | Required entities are unavailable |

**Example Data:**
```
State: ok
Attributes:
  message: All systems operational
  error_count: 0
  warning_count: 0
  required_entities_healthy: true
  errors: []
  warnings: []
  last_check: 2026-02-26T09:08:34+11:00
```

**Icon:** Dynamic (check-circle for ok, alert-circle for degraded, close-circle for error)

---

### 18. sensor.localshift_entity_health

**Purpose:** Detailed health status for all tracked entities.

Added in Issue #94 for detailed diagnostics and troubleshooting.

**State:** Count of healthy entities (e.g., `15/16`)

**Example Data:**
```
State: 15/16
Attributes:
  entities: {...per-entity status...}
  errors: []
  warnings: []
```

---

### 19. sensor.localshift_daily_thermal_mode

**Purpose:** Current daily thermal mode (HEAT/COOL/DRY/OFF).

Added in Issue #140 for HVAC automation. Determined once per day at thermal_mode_decision_time based on weather forecast. Mode is locked until next day's decision time.

**State:** One of: `off`, `cool`, `heat`, `dry`

**Example Data:**
```
State: cool
Attributes:
  mode_locked: true
  determined_at: 2026-02-26T06:00:00+11:00
  climate_entities:
    - climate.ali_aircon
    - climate.james_aircon
    - climate.living_aircon
    - climate.study_aircon
    - climate.master_aircon
  controlled_entities:
    - climate.living_aircon
    - climate.study_aircon
  learned_hvac_power: {}
  baseline_load_hours: 24
```

**Icon:** Dynamic (fire for heat, snowflake for cool, water-off for dry, thermometer-off for off)

---

### 20. sensor.localshift_baseline_load_profile

**Purpose:** Non-HVAC baseline load profile by hour.

Added in Issue #140 for thermal management. Exposes the estimated baseline load (non-HVAC) for each hour, derived from historical data with HVAC samples excluded.

**State:** Average baseline load in kW

**Example Data:**
```
State: 0.458
Attributes:
  hourly_profile_kw:
    "0": 0.492
    "1": 0.39
    "2": 0.375
    ...
  total_hours: 24
  average_kw: 0.458
```

---

### 21. sensor.localshift_hvac_load_profile

**Purpose:** HVAC load profile by hour.

Added in Issue #140 for thermal management. Exposes the estimated HVAC load for each hour based on learned power consumption and climate entity state tracking.

**State:** Average HVAC load in kW

**Example Data:**
```
State: 0.0
Attributes:
  hourly_profile_kw: {...}
  total_hours: 24
  average_kw: 0.0
  learned_power: {}
```

---

### 22. sensor.localshift_learning_status

**Purpose:** Current learning system status and parameter values.

Added in Issue #170 Phase 5 for learning system observability.

**State:** Current learning phase: `observing`, `tuning`, `optimizing`, or `disabled`

**Example Data:**
```
State: observing
Attributes:
  total_decisions_today: 15
  avg_decision_score_today: 0.383
  avg_decision_score_7d: 0.424
  cost_trend: stable
  mode_durations_today:
    boost_charging: 37.5
    self_consumption: 91.0
    grid_charging: 8.5
  mode_cost_attribution:
    grid_charging: -0.0
  optimization_weights: {}
  contextual_adjustments_active: []
```

**Icon:** Dynamic (brain for optimizing, tune for tuning, eye for observing, brain-off for disabled)

---

### 23. sensor.localshift_decision_quality

**Purpose:** Rolling decision quality score.

Added in Issue #170 Phase 5 for tracking learning system effectiveness.

**State:** Today's average decision quality as percentage (0-100%)

**Example Data:**
```
State: 38.3
Attributes:
  total_decisions_today: 15
  avg_score_7d: 42.4
  cost_trend: stable
  grid_charge_efficiency: 0.0
  export_loss_ratio: 0.0
  unnecessary_grid_charge_kwh: 0.0
  mode_durations_today: {...}
  mode_cost_attribution: {...}
```

---

### 24. sensor.localshift_learning_decision_history

**Purpose:** Recent decision history with outcomes.

Added in Issue #170 Phase 5 for learning system debugging.

**State:** Count of decisions in last 24 hours

**Example Data:**
```
State: 20
Attributes:
  decisions: [...last 20 decisions...]
```

---

### 25. sensor.localshift_average_room_temperature

**Purpose:** Average room temperature from configured climate entities.

Added in Issue #63 Phase 6 for real-time thermal control.

**State:** Average temperature in °C

**Example Data:**
```
State: 24.0
Attributes:
  thermal_mode: cool
  activated_today: false
  realtime_active: false
  reason: Waiting to activate (room: 23.9°C)
  preconditioning_active: false
  solar_taper_active: false
```

---

### 26. sensor.localshift_realtime_thermal_status

**Purpose:** Real-time thermal control status and reason.

Added in Issue #63 Phase 6 for visibility into thermal control decisions.

**State:** One of `active` or `inactive`

**Example Data:**
```
State: inactive
Attributes:
  reason: Waiting to activate (room: 23.9°C)
  activated_today: false
  avg_room_temp: 23.9
  thermal_mode: cool
  preconditioning_active: false
  solar_taper_active: false
  taper_setpoint_offset: 0.0
  climate_read_success: true
  climate_missing_entities: []
  climate_unavailable_entities: []
  climate_entities_configured:
    - climate.ali_aircon
    - climate.james_aircon
    - climate.living_aircon
    - climate.study_aircon
    - climate.master_aircon
  climate_entities_controlled:
    - climate.living_aircon
    - climate.study_aircon
```

**Icon:** Dynamic (air-conditioner when active, air-conditioner-off when inactive)

---

### 27. sensor.localshift_backfill_status

**Purpose:** Statistics backfill validation status.

Added in Issue #267. Shows the status of the last backfill operation that validates decision outcomes against metered statistics from Home Assistant.

**State:** One of: `not_run`, `validated`, `discrepancies`, `error`

**Example Data:**
```
State: not_run
```

**Icon:** Dynamic (check-circle for validated, alert-circle for discrepancies, close-circle for error, database-clock for not_run)

---

### 28. sensor.localshift_cost_reconciliation

**Purpose:** Cost reconciliation status.

Added in Issue #269. Shows variance between estimated and actual costs based on metered statistics from Home Assistant.

**State:** One of: `not_run`, `validated`, `variance`, `error`

**Example Data:**
```
State: not_run
```

**Icon:** Dynamic (check-circle for validated, alert-circle for variance, close-circle for error, currency-usd-off for not_run)

---

### 29. sensor.localshift_extended_forecast_accuracy

**Purpose:** Extended forecast accuracy with long-term metrics.

Added in Issue #270. Multi-horizon validation with bias detection. Shows 24h, 7d, and 30d accuracy metrics.

**State Class:** `measurement`

**State:** 24-hour accuracy percentage

**Example Data:**
```
State: 100.0
Attributes:
  accuracy_24h: 100.0
  accuracy_7d: 100.0
  accuracy_30d: 100.0
  bias: 0.0
  mape: 0.0
  sample_count: 0
  last_updated: null
```

---

## Binary Sensors

### 1. binary_sensor.localshift_demand_window

**Purpose:** Whether the demand window is currently active.

**State:** `on` if within demand window, `off` otherwise

**Example Data:**
```
State: off
```

**Configuration:** Uses `demand_window_start` and `demand_window_end` options (default 15:00-21:00)

---

### 2. binary_sensor.localshift_price_spike_coming

**Purpose:** Whether a price spike is forecast within the lookahead window.

**State:** `on` if spike detected in forecast, `off` otherwise

**Example Data:**
```
State: off
Attributes:
  max_forecast_price: 0.0
  max_buy_forecast_price: 0.0
```

---

### 3. binary_sensor.localshift_price_expensive_coming

**Purpose:** Whether an expensive period is forecast within lookahead.

**State:** `on` if any price exceeds `max_precharge_price`, `off` otherwise

**Example Data:**
```
State: off
```

---

### 4. binary_sensor.localshift_discharge_forced

**Purpose:** Whether battery is currently force discharging.

**State:** `on` when `operation_mode == autonomous` AND `backup_reserve < 11`

**Example Data:**
```
State: off
```

---

### 5. binary_sensor.localshift_charge_forced

**Purpose:** Whether battery is currently force charging (backup mode at 3.3kW).

**State:** `on` when `operation_mode == backup`

**Example Data:**
```
State: on
```

---

### 6. binary_sensor.localshift_charge_boost

**Purpose:** Whether battery is currently boost charging at 5kW.

**State:** `on` when `operation_mode == autonomous` AND `backup_reserve > 99`

**Example Data:**
```
State: off
```

---

### 7. binary_sensor.localshift_solar_can_reach_target

**Purpose:** Whether solar forecast can fill battery to target by demand window.

**State:** `on` if Solcast forecast shows sufficient solar to reach target SOC before DW

**Example Data:**
```
State: off
```

---

### 8. binary_sensor.localshift_charge_boost_needed

**Purpose:** Whether 5kW boost charging is needed (3.3kW insufficient).

**State:** `on` if time remaining before DW is insufficient to reach target at 3.3kW

**Example Data:**
```
State: off
```

---

### 9. binary_sensor.localshift_excess_solar_available

**Purpose:** Simple ON/OFF trigger for basic automations - excess solar available.

**State:** `on` when excess solar is available and safe to add load, `off` otherwise

**Example Data:**
```
State: off
Attributes:
  current_excess_kw: 0.26
  battery_soc: 33.9
  battery_charging: true
  can_add_load_now: false
  safe_additional_load_kw: 0.0
```

**Icon:** Dynamic (solar-power-variant when on, solar-power-variant-outline when off)

---

### 10. binary_sensor.localshift_tesla_override_active

**Purpose:** Whether Tesla has taken control of the Powerwall (Storm Watch, Grid Event, VPP).

Added in Issue #110 to provide visibility into when Tesla has override control.

**State:** `on` when Tesla has control, `off` otherwise

**Example Data:**
```
State: off
Attributes:
  operation_mode: autonomous
  backup_reserve: 10.0
  description: Tesla is not overriding control
```

**Icon:** Dynamic (shield-alert when active, shield-check when inactive)

**Use Case:** When this sensor is `on`, LocalShift automation will pause and wait for Tesla to release control. No need to manually intervene.

---

### 11. binary_sensor.localshift_preconditioning_active

**Purpose:** Whether pre-conditioning is actively adjusting climate setpoints.

Added in Issue #137. Pre-conditioning runs before the demand window to pre-heat or pre-cool the home using battery power instead of grid power during expensive periods.

**State:** `on` when pre-conditioning is active, `off` otherwise

**Example Data:**
```
State: off
Attributes:
  daily_thermal_mode: cool
  taper_setpoint_offset: 0.0
```

**Icon:** Dynamic (thermometer-auto when on, thermometer when off)

---

### 12. binary_sensor.localshift_solar_taper_active

**Purpose:** Whether solar tapering is actively adjusting climate setpoints.

Added in Issue #137. Solar tapering increases heating/cooling during excess solar periods to use surplus solar energy that would otherwise be exported at low FIT.

**State:** `on` when solar tapering is active, `off` otherwise

**Example Data:**
```
State: off
Attributes:
  current_excess_kw: 0.26
  taper_setpoint_offset: 0.0
  daily_thermal_mode: cool
```

**Icon:** Dynamic (solar-power when on, solar-power-outline when off)

---

### 13. binary_sensor.localshift_thermal_management_enabled

**Purpose:** Whether thermal management is enabled and configured.

Added in Issue #137. This reflects the thermal_management_enabled switch state and indicates whether the integration is actively managing HVAC for load shifting.

**State:** `on` when thermal management is enabled, `off` otherwise

**Example Data:**
```
State: on
Attributes:
  climate_entities:
    - climate.living_aircon
    - climate.study_aircon
  daily_thermal_mode: cool
  solar_taper_enabled: true
```

---

## Switches

### 1. switch.localshift_automation_enabled

**Purpose:** Master toggle for all automation.

**Default:** ON

**Example Data:**
```
State: on
```

**Behavior:**
- ON: State machine evaluates and controls battery
- OFF: Returns to self consumption, no commands sent

---

### 2. switch.localshift_spike_discharge_enabled

**Purpose:** Allow/disallow discharge during price spikes.

**Default:** ON

**Example Data:**
```
State: on
```

**Behavior:**
- ON: Spike discharge allowed when price spike detected
- OFF: No discharge during spikes (even if spike detected)

---

### 3. switch.localshift_spike_discharge_conservative

**Purpose:** Enable conservative spike discharge mode.

**Default:** OFF

**Example Data:**
```
State: on
```

**Behavior:**
- ON: During spike discharge, calculates a dynamic reserve SOC to ensure battery can survive the spike period without depleting below minimum target SOC
- OFF: Standard spike discharge with fixed minimum reserve

---

### 4. switch.localshift_dry_run

**Purpose:** Log decisions without sending commands.

**Default:** OFF

**Example Data:**
```
State: off
```

**Behavior:**
- ON: All mode changes logged but not executed
- OFF: Normal operation, commands sent to Powerwall

---

### 5. switch.localshift_demand_window_block

**Purpose:** Block grid charging during demand window.

**Default:** ON

**Example Data:**
```
State: on
```

**Behavior:**
- ON: Grid charging blocked during demand window
- OFF: Grid charging allowed during demand window

---

### 6. switch.localshift_allow_dw_entry_under_target

**Purpose:** Allow demand window entry when SOC is under target but solar can reach it.

**Default:** OFF

**Example Data:**
```
State: on
```

**Behavior:**
- ON: Enter demand window mode even if SOC < target, if solar forecast shows target can be reached
- OFF: Only enter demand window mode when SOC >= target

---

### 7. switch.localshift_notify_mode_transitions

**Purpose:** Enable notifications for mode transitions.

**Default:** ON

**Example Data:**
```
State: on
```

---

### 8. switch.localshift_notify_daily_summary

**Purpose:** Enable daily summary notification.

**Default:** ON

**Example Data:**
```
State: on
```

---

### 9. switch.localshift_notify_manual_actions

**Purpose:** Enable notifications for manual button actions.

**Default:** ON

**Example Data:**
```
State: on
```

---

### 10. switch.localshift_notify_alerts

**Purpose:** Enable alert notifications.

**Default:** ON

**Example Data:**
```
State: on
```

---

### 11. switch.localshift_thermal_management

**Purpose:** Enable thermal management for HVAC load shifting.

Added in Issue #137 for temperature-based automation.

**Default:** OFF

**Example Data:**
```
State: on
```

**Behavior:**
- ON: Thermal management active, climate entities controlled based on daily thermal mode
- OFF: No thermal automation, climate entities not controlled

---

### 12. switch.localshift_solar_taper

**Purpose:** Enable solar tapering for climate setpoint adjustment.

Added in Issue #137. When enabled, increases heating/cooling during excess solar periods.

**Default:** ON

**Example Data:**
```
State: on
```

**Behavior:**
- ON: Adjusts climate setpoints during excess solar to use surplus energy
- OFF: No solar tapering, setpoints unchanged

---

### 13. switch.localshift_enable_learning

**Purpose:** Enable the learning system to adjust parameters.

Added in Issue #170 Phase 4 for user control over learning system.

**Default:** OFF

**Example Data:**
```
State: on
```

**Behavior:**
- ON: Learning system can adjust parameters based on outcomes
- OFF: Learning system observes only, parameters stay at defaults

**Use Case:** Enable after the system has collected enough data (~50 decisions, typically 2-3 days). Disable if you want to pause learning or return to default behavior.

---

## Numbers (Configuration Thresholds)

### 1. number.localshift_cheap_price_percentile

**Purpose:** Percentile of near-term forecast prices used as base cheap threshold.

| Property | Value |
|----------|-------|
| Range | 5-50% |
| Default | 25% |
| Unit | % |

**Example Data:**
```
State: 25.0
```

---

### 2. number.localshift_max_pre_charge_price

**Purpose:** Maximum price willing to pay for grid charging when urgent.

| Property | Value |
|----------|-------|
| Range | $0.00-$0.50/kWh |
| Default | $0.20/kWh |
| Unit | $/kWh |

**Example Data:**
```
State: 0.18
```

---

### 3. number.localshift_battery_target

**Purpose:** Target SOC for demand window.

| Property | Value |
|----------|-------|
| Range | 50-100% |
| Default | 100% |
| Unit | % |

**Example Data:**
```
State: 95.0
```

---

### 4. number.localshift_minimum_target_soc

**Purpose:** Minimum SOC maintained during discharge modes.

| Property | Value |
|----------|-------|
| Range | 5-30% |
| Default | 20% |
| Unit | % |

**Example Data:**
```
State: 10.0
```

---

### 5. number.localshift_cooling_trigger_temp

**Purpose:** Temperature threshold for committing to cooling mode.

Added in Issue #137 for thermal management.

| Property | Value |
|----------|-------|
| Range | 20.0-35.0°C |
| Default | 28.0°C |
| Unit | °C |

**Example Data:**
```
State: 23.0
```

If the day's maximum forecast temperature exceeds this value, the system commits to COOL mode.

---

### 6. number.localshift_heating_trigger_temp

**Purpose:** Temperature threshold for committing to heating mode.

Added in Issue #137 for thermal management.

| Property | Value |
|----------|-------|
| Range | 5.0-20.0°C |
| Default | 15.0°C |
| Unit | °C |

**Example Data:**
```
State: 12.0
```

If the day's minimum forecast temperature is below this value, the system commits to HEAT mode.

---

## Buttons (Manual Controls)

### 1. button.localshift_force_charge

**Action:** Start force charging at 3.3kW (backup mode).

**Effect:** Sets Powerwall to `backup` operation mode with reserve=10%

**Use Case:** Manually charge from grid at slow rate.

**Example Data:**
```
State: 2026-02-24T02:32:04+00:00 (last pressed)
```

---

### 2. button.localshift_force_discharge

**Action:** Start force discharging to export to grid.

**Effect:** Sets Powerwall to `autonomous` mode with reserve=10% and allow_export=battery_ok

**Use Case:** Manually export battery during high feed-in prices.

---

### 3. button.localshift_boost_charge

**Action:** Start boost charging at 5kW.

**Effect:** Sets Powerwall to `autonomous` mode with reserve=100%

**Use Case:** Fast charge from grid when time is short before demand window.

---

### 4. button.localshift_self_consumption

**Action:** Clear manual override, return to automation control.

**Effect:** Clears manual override flag, resumes state machine evaluation

**Use Case:** Exit manual control mode after using any of the above buttons.

---

### 5. button.localshift_update_forecast

**Action:** Force forecast update and clear historical load cache.

**Effect:** Clears the historical load cache and triggers a forecast regeneration

**Use Case:** Manually refresh forecast data after external changes or for debugging.

---

### 6. button.localshift_reset_learning_data

**Action:** Reset all learning system data and start fresh.

Added in Issue #170 Phase 5 for user control over learning system state.

**Effect:**
- Clears all decision records
- Resets learned parameters to defaults
- Returns learning status to "observing" phase
- Clears pattern analysis data
- Resets optimization weights

**Use Case:** Start fresh after significant household changes, or if the system has learned sub-optimal parameters.

**Warning:** This erases all learned data. The system will need another 2-3 days of observation before parameter optimization resumes.

---

## Entity Relationships

```
External Inputs (Teslemetry/Amber/Solcast)
         │
         ▼
    Coordinator
         │
         ├─► State Reader (reads entities)
         │
         ├─► Computation Engine (computes derived values)
         │         │
         │         ├─► Forecast Computer
         │         ├─► Cost Tracker
         │         └─► State Machine (determines desired mode)
         │
         └► Battery Controller (executes commands)
                   │
                   └► Teslemetry (controls Powerwall)

Sensors/Binary Sensors display computed values
Switches/Numbers control configuration
Buttons trigger manual actions
```

---

## Debugging Tips

1. **Start with `sensor.localshift_battery_mode`** — This tells you what the automation thinks it should be doing.

2. **Check binary sensors** — They show the conditions being evaluated:
   - `demand_window` — Are we in peak hours?
   - `price_spike_coming` — Is a spike coming?
   - `solar_can_reach_target` — Can solar fill the battery?
   - `excess_solar_available` — Is there excess solar for load shifting?

3. **Look at attributes** — Many sensors have extra diagnostic attributes:
   - `forecast_prices` shows price thresholds
   - `cost_electricity_net` shows cost breakdown
   - `forecast_battery` shows predicted SOC
   - `excess_solar` shows detailed excess calculations

4. **Use decision log** — `sensor.localshift_decision_log` shows the last 10 mode changes with reasons.

5. **Enable dry run** — Use `switch.localshift_dry_run` to test without affecting the battery.

6. **Monitor load shift signal** — Use `sensor.localshift_load_shift_signal` for simple automation triggers.

7. **Check forecast diagnostics** — `sensor.localshift_forecast_diagnostics` contains debug fields and consumption profile information for troubleshooting forecast accuracy.

8. **Monitor thermal status** — Use `sensor.localshift_realtime_thermal_status` to understand why thermal control is or isn't active.