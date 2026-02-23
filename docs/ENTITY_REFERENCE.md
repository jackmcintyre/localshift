# Entity Reference

Complete reference for all Home Assistant entities provided by the LocalShift integration.

## Overview

The integration creates **54 entities** grouped under a single "LocalShift" device:

| Category | Count | Entity Type |
|----------|-------|-------------|
| Sensors | 20 | `sensor` |
| Binary Sensors | 11 | `binary_sensor` |
| Switches | 10 | `switch` |
| Numbers | 8 | `number` |
| Buttons | 5 | `button` |

**Note:** Grid import/export power values are available as computed values in `CoordinatorData` but are not exposed as separate sensor entities. They can be accessed via template sensors if needed.

---

## Sensors

### 1. sensor.localshift_price_cheap_effective

**Purpose:** Dynamic cheap price threshold that adjusts based on urgency.

The effective cheap price is calculated from the base cheap price percentile, then elevated based on how close the demand window is and whether solar can reach the target.

**State:** Current effective cheap price threshold in $/kWh (e.g., `0.15`)

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `urgency` | float | 0.0-1.0 urgency factor based on time to demand window |
| `is_elevated` | bool | True if the effective price is above the base threshold |
| `base_price` | float | The base cheap price from percentile calculation |
| `min_forecast_price` | float | Minimum price in forecast window (floor to prevent overpaying) |

**Calculation Logic:**

```
base_price = percentile(amber_general_forecast, cheap_price_percentile)
urgency = (time_to_demand_window) / (time_from_start_to_demand_window)
effective_price = base_price + (max_precharge_price - base_price) * urgency
effective_price = max(effective_price, min_forecast_price + 0.02)
```

---

### 2. sensor.localshift_price_cheap_charge_stop

**Purpose:** Upper boundary of the deadband zone.

**State:** Price threshold in $/kWh where charging should stop

**Calculation:** `effective_cheap_price + cheap_price_deadband`

This creates a hysteresis zone:
- Charge when price ≤ effective_cheap_price
- Hold when effective_cheap_price < price < stop_price
- Self-consumption when price ≥ stop_price

---

### 3. sensor.localshift_solar_weighted_avg_fit

**Purpose:** Solar-production-weighted average feed-in tariff for remaining daylight hours.

**State:** Weighted average FIT in $/kWh

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `total_solar_remaining_kwh` | float | Estimated solar production remaining today (kWh) |

**Calculation Logic:**
1. Get Solcast forecast for remaining daylight hours
2. Get Amber feed-in price forecast for same periods
3. Weight each period's FIT by its solar production (pv_estimate)
4. Calculate weighted average: `sum(FIT × solar) / sum(solar)`

This represents the "blended" price you'd get if you exported all remaining solar production.

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

**How to Use:** This is the primary sensor for understanding what the automation is doing. Check this first when debugging.

---

### 5. sensor.localshift_forecast_battery

**Purpose:** Predicted battery SOC at demand window start.

**State:** Predicted SOC percentage at the demand window start time (e.g., `85.5`)

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `predicted_soc` | float | Predicted SOC at demand window |
| `target_soc` | float | Target SOC (from configuration) |
| `solar_kwh` | float | Total solar energy expected before DW |
| `consumption_kwh` | float | Estimated consumption before DW |
| `gap_kwh` | float | kWh gap to reach target |
| `gap_percent` | float | Percent gap to reach target |
| `solar_can_reach` | bool | Whether solar alone can fill battery |
| `boost_needed` | bool | Whether 5kW boost is needed |
| `hourly_forecast` | list | Hour-by-hour SOC projections |
| `time_to_target_hours` | float | Hours until target would be reached |

---

### 6. sensor.localshift_cost_electricity_net

**Purpose:** Net cost for the day (import cost - export revenue).

**State:** Net cost in $ (e.g., `-2.45` — negative means profit)

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `grid_import_cost` | float | Total cost of grid imports today ($) |
| `grid_export_revenue` | float | Total revenue from exports today ($) |
| `battery_savings` | float | Value of solar self-consumed ($) |
| `battery_charge_cost` | float | Cost to charge battery from grid ($) |

**Calculation:**
```
net_cost = grid_import_cost - grid_export_revenue
```

**Note:** Battery savings represents the value of using battery instead of importing from grid (solar that would have exported but was stored instead).

---

### 7. sensor.localshift_decision_log

**Purpose:** History of mode changes with human-readable reasons.

**State:** Most recent decision reason (e.g., "price_below_threshold")

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

**State:** Count of stored predictions (e.g., `48`)

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `history` | list | Array of historical forecast snapshots |

---

### 9. sensor.localshift_forecast_daily

**Purpose:** Core 24-hour forecast with SOC, solar, and consumption data.

Split from the original monolithic sensor to stay under Home Assistant's 16KB attribute limit (Issue #37). Related sensors: `forecast_prices`, `forecast_grid`, `forecast_diagnostics`.

**State:** Count of forecast slots (96 × 15-min slots)

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `forecast_slots` | list | 96 slots with SOC, solar, consumption, prices |
| `soc_series` | list | SOC time series for graphing `[{time, soc}, ...]` |
| `slot_count` | int | Total number of forecast slots |
| `solcast_today_entries` | int | Number of Solcast periods today |
| `solcast_tomorrow_entries` | int | Number of Solcast periods tomorrow |
| `forecast_hourly` | list | Hourly summary (24 entries) |

**Forecast Slot Structure:**

Each slot in `forecast_slots` contains:

| Field | Type | Description |
|-------|------|-------------|
| `time` | string | HH:MM format |
| `hour` | int | Hour (0-23) |
| `minute` | int | Minute (0, 15, 30, 45) |
| `predicted_soc` | float | Predicted battery SOC (%) |
| `solar_kwh` | float | Solar production (kWh) |
| `consumption_kwh` | float | Estimated consumption (kWh) |
| `net_kwh` | float | Net energy (solar - consumption) |
| `buy_price` | float | Buy price ($/kWh) |
| `sell_price` | float | Sell price ($/kWh) |

---

### 10. sensor.localshift_forecast_prices

**Purpose:** Price forecast data for history collection.

Split from `forecast_daily` to stay under 16KB limit (Issue #37).

**State:** Current effective cheap price ($/kWh)

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `buy_prices` | list | 96-slot buy price time series |
| `sell_prices` | list | 96-slot sell price time series |
| `effective_cheap_price` | float | Current cheap price threshold |
| `cheap_charge_stop_price` | float | Stop charging threshold |
| `forecast_import_cost` | float | Expected import cost (rest of day) |
| `forecast_export_revenue` | float | Expected export revenue (rest of day) |
| `forecast_net_cost` | float | Expected net cost (rest of day) |
| `forecast_grid_charge_cost` | float | Expected grid charging cost |
| `forecast_proactive_export_revenue` | float | Expected proactive export revenue |

**Price Slot Structure:**

Each slot in `buy_prices` and `sell_prices` contains:

| Field | Type | Description |
|-------|------|-------------|
| `time` | string | HH:MM format |
| `hour` | int | Hour (0-23) |
| `minute` | int | Minute (0, 15, 30, 45) |
| `price` | float | Price ($/kWh) |

---

### 11. sensor.localshift_forecast_grid

**Purpose:** Grid interaction forecast data for history collection.

Split from `forecast_daily` to stay under 16KB limit (Issue #37).

**State:** Total forecast grid import (kWh)

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `grid_interaction` | list | 96-slot grid interaction time series |
| `total_grid_import_kwh` | float | Total grid import forecast (kWh) |
| `total_grid_export_kwh` | float | Total grid export forecast (kWh) |
| `grid_charge_slots` | int | Number of slots with grid charging |
| `proactive_export_slots` | int | Number of slots with proactive export |

**Grid Interaction Slot Structure:**

Each slot in `grid_interaction` contains:

| Field | Type | Description |
|-------|------|-------------|
| `time` | string | HH:MM format |
| `hour` | int | Hour (0-23) |
| `minute` | int | Minute (0, 15, 30, 45) |
| `grid_import_kwh` | float | Grid import (kWh) |
| `grid_export_kwh` | float | Grid export (kWh) |
| `grid_charge` | bool | Grid charging active |
| `grid_charge_boost` | bool | Boost charging active |
| `proactive_export` | bool | Proactive export active |
| `export_amount_kwh` | float | Export amount (kWh) |

---

### 12. sensor.localshift_forecast_diagnostics

**Purpose:** Diagnostic and debug data for the forecast system.

Split from `forecast_daily` to stay under 16KB limit (Issue #37).

**State:** Consumption data source (e.g., "profile_hour", "live_load_fallback")

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `consumption_source` | string | Source of consumption data |
| `consumption_statistic_id` | string | HA statistic ID used |
| `consumption_profile_hours` | int | Hours of profile data available |
| `consumption_fallback_hours` | int | Hours using fallback data |
| `consumption_weighting` | float | Recent vs historical weighting |
| `forecast_consumption_source_counts` | dict | Count by source type |
| `consumption_hourly_sample_counts` | dict | Samples per hour |
| `consumption_hourly_profile_kw` | dict | Hourly consumption averages (kW) |
| `consumption_profile_type` | string | "weekday_weekend" or "combined" |
| `forecast_profile_selected` | string | Profile used for today |
| `weekday_sample_counts` | dict | Weekday samples per hour |
| `weekend_sample_counts` | dict | Weekend samples per hour |
| `weekday_hourly_profile_kw` | dict | Weekday hourly averages |
| `weekend_hourly_profile_kw` | dict | Weekend hourly averages |
| `recent_load_1hr_kw` | float | Recent 1-hour average load |
| `recent_load_1hr_statistic_id` | string | Statistic ID for recent load |
| `recent_load_1hr_samples` | int | Samples in recent load calc |
| `recent_load_1hr_last_error` | string | Last error message |
| `current_load_kw` | float | Current load (kW) |
| `debug_forecast_slot_found` | bool | Current slot found in forecast |
| `debug_forecast_slot_time` | string | Time of matched slot |
| `debug_first_forecast_slot_time` | string | Time of first forecast slot |
| `debug_time_gap_seconds` | float | Seconds between now and first slot |
| `debug_mode_source` | string | "forecast" or "fallback" |
| `allow_export` | string | Export permission state |
| `weather_entity_id` | string | Weather entity ID |
| `weather_temperature_current` | float | Current temperature (°C) |
| `weather_temperature_forecast` | dict | Hourly temperature forecast |
| `weather_condition` | string | Current weather condition |
| `weather_correlation_confidence` | string | low/medium/high |
| `weather_adjustment_applied` | bool | Weather adjustment used |
| `weather_learning_enabled` | string | Learning enabled |
| `weather_cooling_coefficient` | float | Cooling coefficient (kW/°C) |
| `weather_heating_coefficient` | float | Heating coefficient (kW/°C) |
| `weather_sample_count` | int | Learning samples collected |

---

### 13. sensor.localshift_target_soc_minimum

**Purpose:** Minimum target SOC for discharge modes (base reserve).

**State:** Configured minimum SOC percentage (default 20%)

**Configuration:** Set via `number.localshift_minimum_target_soc`

This is the floor SOC maintained during spike discharge and proactive export modes to protect battery health and ensure reserve capacity.

---

### 14. sensor.localshift_excess_solar_kwh

**Purpose:** Forecasted excess solar energy available for discretionary loads.

**State:** Total excess solar until battery reaches 100% (kWh)

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `excess_current_hour_kwh` | float | Excess available in the current hour |
| `excess_next_2h_kwh` | float | Excess available in the next 2 hours |
| `excess_next_4h_kwh` | float | Excess available in the next 4 hours |
| `excess_until_battery_full_kwh` | float | Excess until battery reaches 100% |
| `excess_until_negative_fit_kwh` | float | Excess before negative FIT window starts |
| `time_until_battery_full_minutes` | int | Minutes until battery is full |
| `negative_fit_window_start` | datetime | When negative FIT begins (or null) |
| `negative_fit_window_duration_minutes` | int | Duration of negative FIT period |
| `can_add_load_now` | bool | **Critical** — Is it safe to add load? |
| `safe_additional_load_kw` | float | Max kW that can be safely added |
| `forecast_confidence` | string | low/medium/high |
| `current_excess_rate_kw` | float | Current excess generation rate |

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

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `recommended_additional_kw` | float | Suggested load change (+ or -) |
| `recommended_duration_minutes` | int | How long to maintain the change |
| `signal_reason` | string | Human-readable explanation |
| `signal_confidence` | string | low/medium/high |
| `current_excess_rate_kw` | float | Current excess generation rate |
| `grid_charge_risk` | bool | Would adding load trigger grid charging? |
| `safe_additional_load_kw` | float | Max kW that can be safely added |

**Icon:** Dynamic based on signal (arrow-up-bold, arrow-down-bold, check-circle, pause-circle)

---

### 16. sensor.localshift_forecast_accuracy

**Purpose:** Compares past forecast predictions with actual outcomes to track accuracy.

Added in Issue #37 Phase 2 to monitor how well the forecast system predicts SOC and prices.

**State:** SOC accuracy percentage for 1-hour predictions (e.g., `95.5`)

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `soc_error_15min` | float | SOC prediction error at 15 minutes (predicted - actual) |
| `soc_error_1h` | float | SOC prediction error at 1 hour (predicted - actual) |
| `soc_error_4h` | float | SOC prediction error at 4 hours (predicted - actual) |
| `soc_accuracy_15min` | float | SOC accuracy percentage at 15 min (100 - abs error) |
| `soc_accuracy_1h` | float | SOC accuracy percentage at 1 hour (100 - abs error) |
| `soc_accuracy_4h` | float | SOC accuracy percentage at 4 hours (100 - abs error) |
| `buy_price_error_1h` | float | Buy price error at 1 hour ($/kWh) |
| `sell_price_error_1h` | float | Sell price error at 1 hour ($/kWh) |
| `comparisons_made` | int | Total comparisons since restart |
| `last_comparison_time` | string | ISO timestamp of last comparison |

**Use Case:** Monitor forecast quality over time. Lower errors indicate better predictions. Use to identify when the forecast model needs adjustment.

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

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `message` | string | Human-readable status message |
| `error_count` | int | Number of entity errors |
| `warning_count` | int | Number of entity warnings |
| `required_entities_healthy` | bool | Whether all required entities are healthy |
| `errors` | list | List of error messages |
| `warnings` | list | List of warning messages |
| `last_check` | string | ISO timestamp of last health check |

**Icon:** Dynamic (check-circle for ok, alert-circle for degraded, close-circle for error)

---

### 18. sensor.localshift_entity_health

**Purpose:** Detailed health status for all tracked entities.

Added in Issue #94 for detailed diagnostics and troubleshooting.

**State:** Count of healthy entities (e.g., `12/15`)

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `entities` | dict | Per-entity health status with details |
| `errors` | list | List of error messages |
| `warnings` | list | List of warning messages |

**Use Case:** Troubleshoot entity availability issues. Each entity entry includes status, last seen time, and error details.

---

### 19. sensor.localshift_average_room_temp

**Purpose:** Average room temperature from configured climate entities.

Added in Issue #63 Phase 6 for real-time thermal control.

**State:** Average temperature in °C (e.g., `23.5`)

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `thermal_mode` | string | Current daily thermal mode (heat/cool/dry/off) |
| `activated_today` | bool | Whether thermal control has activated today |
| `realtime_active` | bool | Whether real-time thermal control is currently active |
| `reason` | string | Reason for current thermal state |
| `preconditioning_active` | bool | Whether pre-conditioning is active |
| `solar_taper_active` | bool | Whether solar tapering is active |

**Use Case:** Monitor average room temperature for thermal automation decisions. Used by the real-time thermal control layer to determine when to turn HVAC on/off.

---

### 20. sensor.localshift_realtime_thermal_status

**Purpose:** Real-time thermal control status and reason.

Added in Issue #63 Phase 6 for visibility into thermal control decisions.

**State:** One of `active` or `inactive`

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `reason` | string | Human-readable reason for current state |
| `activated_today` | bool | Whether thermal control has activated today |
| `avg_room_temp` | float | Current average room temperature (°C) |
| `thermal_mode` | string | Current daily thermal mode |
| `preconditioning_active` | bool | Whether pre-conditioning is active |
| `solar_taper_active` | bool | Whether solar tapering is active |
| `taper_setpoint_offset` | float | Current setpoint offset from solar taper |

**Icon:** Dynamic (air-conditioner when active, air-conditioner-off when inactive)

**Use Case:** Monitor the real-time thermal control layer. Check the `reason` attribute to understand why the system is in its current state.

---

## Binary Sensors

### 1. binary_sensor.localshift_demand_window

**Purpose:** Whether the demand window is currently active.

**State:** `on` if within demand window, `off` otherwise

**Configuration:** Uses `demand_window_start` and `demand_window_end` options (default 15:00-21:00)

---

### 2. binary_sensor.localshift_price_spike_coming

**Purpose:** Whether a price spike is forecast within the lookahead window.

**State:** `on` if spike detected in forecast, `off` otherwise

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `max_forecast_price` | float | Maximum feed-in price in forecast |
| `max_buy_forecast_price` | float | Maximum buy price in forecast |

---

### 3. binary_sensor.localshift_price_expensive_coming

**Purpose:** Whether an expensive period is forecast within lookahead.

**State:** `on` if any price exceeds `max_precharge_price`, `off` otherwise

---

### 4. binary_sensor.localshift_discharge_forced

**Purpose:** Whether battery is currently force discharging.

**State:** `on` when `operation_mode == autonomous` AND `backup_reserve < 11`

---

### 5. binary_sensor.localshift_charge_forced

**Purpose:** Whether battery is currently force charging (backup mode at 3.3kW).

**State:** `on` when `operation_mode == backup`

---

### 6. binary_sensor.localshift_charge_boost

**Purpose:** Whether battery is currently boost charging at 5kW.

**State:** `on` when `operation_mode == autonomous` AND `backup_reserve > 99`

---

### 7. binary_sensor.localshift_solar_can_reach_target

**Purpose:** Whether solar forecast can fill battery to target by demand window.

**State:** `on` if Solcast forecast shows sufficient solar to reach target SOC before DW

---

### 8. binary_sensor.localshift_charge_boost_needed

**Purpose:** Whether 5kW boost charging is needed (3.3kW insufficient).

**State:** `on` if time remaining before DW is insufficient to reach target at 3.3kW

---

### 9. binary_sensor.localshift_excess_solar_available

**Purpose:** Simple ON/OFF trigger for basic automations - excess solar available.

**State:** `on` when excess solar is available and safe to add load, `off` otherwise

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `current_excess_kw` | float | Current excess generation rate |
| `battery_soc` | float | Current battery SOC |
| `battery_charging` | bool | Whether battery is currently charging |
| `can_add_load_now` | bool | Whether it's safe to add load |
| `safe_additional_load_kw` | float | Max kW that can be safely added |

**Icon:** Dynamic (solar-power-variant when on, solar-power-variant-outline when off)

---

### 10. binary_sensor.localshift_tesla_override_active

**Purpose:** Whether Tesla has taken control of the Powerwall (Storm Watch, Grid Event, VPP).

Added in Issue #110 to provide visibility into when Tesla has override control.

**State:** `on` when Tesla has control, `off` otherwise

**Behavior:**
When Tesla activates Storm Watch, Grid Events, or VPP events, they set `backup_reserve` to 80% and `operation_mode` to `self_consumption`, ignoring external API commands until the event ends. This sensor detects when that override is active.

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `operation_mode` | string | Current Powerwall operation mode |
| `backup_reserve` | float | Current backup reserve percentage |
| `description` | string | Human-readable status description |

**Icon:** Dynamic (shield-alert when active, shield-check when inactive)

**Use Case:** When this sensor is `on`, LocalShift automation will pause and wait for Tesla to release control. No need to manually intervene.

---

### 11. binary_sensor.localshift_forecast_expensive_period

**Purpose:** Whether an expensive period is forecast within lookahead.

**State:** `on` if any price exceeds `max_precharge_price`, `off` otherwise

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `max_forecast_price` | float | Maximum feed-in price in forecast |
| `max_buy_forecast_price` | float | Maximum buy price in forecast |

---

## Switches

### 1. switch.localshift_automation_enabled

**Purpose:** Master toggle for all automation.

**Default:** ON

**Behavior:**
- ON: State machine evaluates and controls battery
- OFF: Returns to self consumption, no commands sent

---

### 2. switch.localshift_spike_discharge_enabled

**Purpose:** Allow/disallow discharge during price spikes.

**Default:** ON

**Behavior:**
- ON: Spike discharge allowed when price spike detected
- OFF: No discharge during spikes (even if spike detected)

---

### 3. switch.localshift_spike_discharge_conservative

**Purpose:** Enable conservative spike discharge mode.

**Default:** OFF

**Behavior:**
- ON: During spike discharge, calculates a dynamic reserve SOC to ensure battery can survive the spike period without depleting below minimum target SOC
- OFF: Standard spike discharge with fixed minimum reserve

**Use Case:** Enable during extended spike periods or when you want to ensure battery reserve for overnight/demand window.

---

### 4. switch.localshift_dry_run

**Purpose:** Log decisions without sending commands.

**Default:** OFF

**Behavior:**
- ON: All mode changes logged but not executed
- OFF: Normal operation, commands sent to Powerwall

Use this to test automation behavior without affecting actual battery state.

---

### 5. switch.localshift_demand_window_block

**Purpose:** Block grid charging during demand window.

**Default:** ON

**Behavior:**
- ON: Grid charging blocked during demand window
- OFF: Grid charging allowed during demand window

---

### 6. switch.localshift_allow_dw_entry_under_target

**Purpose:** Allow demand window entry when SOC is under target but solar can reach it.

**Default:** OFF

**Behavior:**
- ON: Enter demand window mode even if SOC < target, if solar forecast shows target can be reached
- OFF: Only enter demand window mode when SOC >= target

---

### 7. switch.localshift_notify_transitions

**Purpose:** Enable notifications for mode transitions.

**Default:** ON

**Behavior:**
- ON: Send notification when battery mode changes (grid charging, spike discharge, etc.)
- OFF: No transition notifications

---

### 8. switch.localshift_notify_daily_summary

**Purpose:** Enable daily summary notification.

**Default:** ON

**Behavior:**
- ON: Send daily energy/cost summary at demand window end
- OFF: No daily summary notification

---

### 9. switch.localshift_notify_manual_actions

**Purpose:** Enable notifications for manual button actions.

**Default:** ON

**Behavior:**
- ON: Send notification when manual control buttons are pressed
- OFF: No manual action notifications

---

### 10. switch.localshift_notify_alerts

**Purpose:** Enable alert notifications.

**Default:** ON

**Behavior:**
- ON: Send alert notifications (automation disabled, health check failures, etc.)
- OFF: No alert notifications

---

## Numbers (Configuration Thresholds)

### 1. number.localshift_cheap_price_percentile

**Purpose:** Percentile of near-term forecast prices used as base cheap threshold.

| Property | Value |
|----------|-------|
| Range | 5-50% |
| Default | 25% |
| Unit | % |

**Example:** 25th percentile means the bottom 25% of prices are considered "cheap".

---

### 2. number.localshift_max_pre_charge_price

**Purpose:** Maximum price willing to pay for grid charging when urgent.

| Property | Value |
|----------|-------|
| Range | $0.00-$0.50/kWh |
| Default | $0.20/kWh |
| Unit | $/kWh |

Used as the ceiling for effective cheap price when approaching demand window with low SOC.

---

### 3. number.localshift_cheap_price_deadband

**Purpose:** Hysteresis band to prevent rapid charge/stop cycling.

| Property | Value |
|----------|-------|
| Range | $0.00-$0.10/kWh |
| Default | $0.03/kWh |
| Unit | $/kWh |

**Calculation:** `stop_price = effective_cheap_price + deadband`

---

### 4. number.localshift_forecast_lookahead_hours

**Purpose:** How far ahead to scan for spikes and expensive periods.

| Property | Value |
|----------|-------|
| Range | 1-8 hours |
| Default | 2 hours |
| Unit | hours |

---

### 5. number.localshift_battery_target

**Purpose:** Target SOC for demand window.

| Property | Value |
|----------|-------|
| Range | 50-100% |
| Default | 100% |
| Unit | % |

The battery aims to reach this SOC by the demand window start time.

---

### 6. number.localshift_load_weight_recent

**Purpose:** Weight given to recent vs historical consumption data.

| Property | Value |
|----------|-------|
| Range | 0.0-1.0 |
| Default | 0.67 (2/3) |
| Unit | (ratio) |

**Calculation:** `weighted_avg = recent × weight + historical × (1 - weight)`

---

### 7. number.localshift_spike_price_percentile

**Purpose:** Price percentile threshold for spike discharge activation.

| Property | Value |
|----------|-------|
| Range | 50-95% |
| Default | 75% |
| Unit | % |

**Example:** 75th percentile means spike discharge only activates when feed-in price is in the top 25% of forecast prices.

---

### 8. number.localshift_minimum_target_soc

**Purpose:** Minimum SOC maintained during discharge modes.

| Property | Value |
|----------|-------|
| Range | 5-30% |
| Default | 20% |
| Unit | % |

This is the floor SOC during spike discharge and proactive export modes. Protects battery health and ensures reserve capacity.

---

## Buttons (Manual Controls)

### 1. button.localshift_force_charge

**Action:** Start force charging at 3.3kW (backup mode).

**Effect:** Sets Powerwall to `backup` operation mode with reserve=10%

**Use Case:** Manually charge from grid at slow rate.

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
   - `demand_window_active` — Are we in peak hours?
   - `forecast_spike_within_window` — Is a spike coming?
   - `solar_can_reach_target` — Can solar fill the battery?
   - `excess_solar_available` — Is there excess solar for load shifting?

3. **Look at attributes** — Many sensors have extra diagnostic attributes:
   - `effective_cheap_price` shows urgency calculation
   - `net_electricity_cost_today` shows cost breakdown
   - `solar_battery_forecast` shows predicted SOC
   - `excess_solar_kwh` shows detailed excess calculations

4. **Use decision log** — `sensor.localshift_decision_log` shows the last 10 mode changes with reasons.

5. **Enable dry run** — Use `switch.localshift_dry_run` to test without affecting the battery.

6. **Monitor load shift signal** — Use `sensor.localshift_load_shift_signal` for simple automation triggers.

7. **Check forecast diagnostics** — `sensor.localshift_forecast_diagnostics` contains debug fields and consumption profile information for troubleshooting forecast accuracy.