# Entity Reference

Complete reference for all Home Assistant entities provided by the LocalShift integration.

## Overview

The integration creates **44 entities** grouped under a single "LocalShift" device:

| Category | Count | Entity Type |
|----------|-------|-------------|
| Sensors | 12 | `sensor` |
| Binary Sensors | 9 | `binary_sensor` |
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

**Purpose:** Full 24-hour forecast with hybrid granularity (5-min near-term, 15-min long-term).

**State:** Count of forecast slots (112 total: 24 × 5-min + 88 × 15-min)

**Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `forecast_slots` | list | All 112 forecast slots with compact keys |
| `forecast_hourly` | list | Hourly summary (24 entries) |
| `soc_series_15min` | list | SOC at each slot |
| `debug_15min_slots` | list | Key slots for debugging |
| `forecast_15min_slots` | int | Total number of slots |
| `solcast_today_entries` | int | Number of Solcast periods |
| `solcast_tomorrow_entries` | int | Number of tomorrow periods |
| `current_load_kw` | float | Current load estimation |
| `consumption_source` | string | Source of consumption data |
| `consumption_profile_hours` | int | Hours of profile data available |
| `consumption_weighting` | float | Recent vs historical weighting |
| `forecast_import_cost` | float | Expected grid import cost (rest of day) |
| `forecast_export_revenue` | float | Expected grid export revenue (rest of day) |
| `forecast_net_cost` | float | Expected net cost (rest of day) |

---

### 10. sensor.localshift_target_soc_minimum

**Purpose:** Minimum target SOC for discharge modes (base reserve).

**State:** Configured minimum SOC percentage (default 20%)

**Configuration:** Set via `number.localshift_minimum_target_soc`

This is the floor SOC maintained during spike discharge and proactive export modes to protect battery health and ensure reserve capacity.

---

### 11. sensor.localshift_excess_solar_kwh

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

### 12. sensor.localshift_load_shift_signal

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

**Purpose:** Simple ON/OFF trigger for basic load-shifting automations.

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
         └─► Battery Controller (executes commands)
                   │
                   └─► Teslemetry (controls Powerwall)

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