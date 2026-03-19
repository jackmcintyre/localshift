# Entity Reference

Complete reference for all Home Assistant entities provided by the LocalShift integration.

## Overview

The integration creates **60 entities** grouped under a single "LocalShift" device:

| Category | Count | Entity Type |
|----------|-------|-------------|
| Sensors | 32 | `sensor` |
| Binary Sensors | 11 | `binary_sensor` |
| Switches | 8 | `switch` |
| Numbers | 6 | `number` |
| Selects | 2 | `select` |
| Buttons | 2 | `button` |

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

### 4. sensor.localshift_forecast_battery

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

### 5. sensor.localshift_cost_electricity_net

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

### 6. sensor.localshift_decision_log

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

### 7. sensor.localshift_forecast_history

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

### 8. sensor.localshift_optimizer_plan

**Purpose:** Core 24-hour optimizer plan with SOC, solar, and consumption data.

**Note:** Phase 5 (#447) renamed from `sensor.localshift_forecast_daily`. Now reads from DP optimizer decisions.

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

**Plan Slot Structure:**

Each slot in the optimizer plan contains:

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `slot_idx` | int | Slot index (0-95) | `0` |
| `action` | string | Optimizer action | `hold`, `charge_grid_normal`, `export_proactive` |
| `reason_code` | string | Why this action was chosen | `IDLE`, `PRICE_ABOVE_THRESHOLD` |
| `objective_terms` | dict | Cost/revenue breakdown | `{import_cost: 0.0, export_revenue: 0.0, ...}` |
| `predicted_soc_pct` | float | Predicted battery SOC (%) | `75.5` |
| `grid_import_kwh` | float | Grid import (kWh) | `0.825` |
| `grid_export_kwh` | float | Grid export (kWh) | `0.0` |

---

### 9. sensor.localshift_forecast_prices

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

### 10. sensor.localshift_optimizer_plan_grid

**Purpose:** Grid interaction plan data projected by the DP optimizer.

**Note:** Phase 5 (#447) renamed from `sensor.localshift_forecast_grid`. Now reads projected import/export from optimizer.

**State Class:** `measurement` — Supports long-term statistics (Issue #266)

**State:** Total projected grid import (kWh)

**Example Data:**
```
State: 22.245
Attributes:
  projected_import_kwh: 12.5
  projected_export_kwh: 8.3
  projected_net_cost: 2.45
  action_breakdown:
    HOLD: 70
    CHARGE_GRID_NORMAL: 10
    EXPORT_PROACTIVE: 8
    CHARGE_FOR_DEMAND_WINDOW: 8
  planner: "DP_OPTIMIZER"
```

---

### 11. sensor.localshift_forecast_diagnostics

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

### 12. sensor.localshift_target_soc_minimum

**Purpose:** Minimum target SOC for discharge modes (base reserve).

**State:** Configured minimum SOC percentage

**Example Data:**
```
State: 10.0
```

**Configuration:** Set via `number.localshift_minimum_target_soc`

This is the floor SOC maintained during spike discharge and proactive export modes to protect battery health and ensure reserve capacity.

---

### 13. sensor.localshift_excess_solar

**Purpose:** Forecasted excess solar energy available for discretionary loads.

**Note:** Entity ID is `sensor.localshift_excess_solar`.

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

### 14. sensor.localshift_load_shift_signal

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

### 15. sensor.localshift_forecast_accuracy

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

### 16. sensor.localshift_integration_status

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

### 17. sensor.localshift_entity_health

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

### 18. sensor.localshift_learning_status

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

### 19. sensor.localshift_decision_quality

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

### 20. sensor.localshift_learning_decision_history

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

### 21. sensor.localshift_optimizer_advantage

**Purpose:** Counterfactual TOU baseline comparison showing optimizer value.

Added in Issue #683 to measure whether the optimizer performs better than a simple time-of-use baseline strategy (charge during cheapest 4 hours, discharge during peak).

**State Class:** `measurement`

**State:** Daily optimizer advantage in $ (negative = TOU cheaper, positive = optimizer cheaper)

**Example Data:**
```
State: 1.25
Attributes:
  advantage_7d: 8.75
  advantage_daily_avg: 1.25
  advantage_percent: 15.2
  tou_cost: 8.25
  actual_cost: 7.00
  degrading: false
```

**Key Attributes:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `advantage_7d` | float | 7-day rolling total advantage ($) |
| `advantage_daily_avg` | float | Daily average over 7 days ($) |
| `advantage_percent` | float | Advantage as percentage of TOU cost |
| `tou_cost` | float | Estimated cost with TOU baseline strategy ($) |
| `actual_cost` | float | Actual cost with optimizer decisions ($) |
| `degrading` | bool | Whether advantage trend is negative |

**Use Case:** This sensor answers "Is the optimizer actually saving money compared to simple TOU rules?" The TOU baseline strategy charges during the cheapest 4 hours and discharges/self-consumes during peak hours. A positive value means the optimizer is outperforming this simple baseline.

**Icon:** `mdi:scale-balance`

---

### 22. sensor.localshift_decision_lag

**Purpose:** Time between decision timestamp and implementation timestamp.

Added in Issue #501. Tracks the latency between when a decision is made and when it is actually implemented by the Powerwall.

**State Class:** `measurement`

**State:** Decision lag in seconds

**Example Data:**
```
State: 45.2
Attributes:
  current_lag: 45.2
  last_transition:
    timestamp: "2026-02-26T09:05:37"
    lag_seconds: 45.2
    mode_from: "self_consumption"
    mode_to: "grid_charging"
  history: [...last 20 transitions...]
  avg_lag_24h: 38.5
  max_lag_24h: 120.3
  min_lag_24h: 12.1
  total_transitions: 15
  decision_timestamp: "2026-02-26T09:05:00"
  implementation_timestamp: "2026-02-26T09:05:45"
```

---

### 23. sensor.localshift_forecast_status

**Purpose:** Current forecast data readiness status.

Added in Issue #319. Indicates whether forecast data is ready for automation decisions.

**State:** One of: `ready`, `partial`, `stale`, `error`

| Status | Meaning |
|--------|---------|
| `ready` | All forecast data available and fresh |
| `partial` | Some forecast data missing or stale |
| `stale` | Forecast data is outdated |
| `error` | Error loading forecast data |

**Example Data:**
```
State: ready
Attributes:
  forecast_ready: true
  solcast_today_entries: 48
  solcast_tomorrow_entries: 48
  debug_mode_source: "normal"
```

**Icon:** Dynamic (check-circle for ready, alert-circle for partial, close-circle for stale, weather-sunny-alert for error)

---

### 24. sensor.localshift_automation_ready

**Purpose:** Overall automation readiness status.

Added in Issue #349. Combines all readiness checks into a single indicator.

**State:** `ready` or `not_ready`

**Example Data:**
```
State: ready
Attributes:
  automation_ready: true
  status_checks:
    soc_available: true
    prices_available: true
    forecast_ready: true
    mode_valid: true
  missing_inputs: []
  soc: 75.5
  operation_mode: "self_consumption"
  backup_reserve: 10.0
  prices_available: true
  forecast_status: "ready"
```

**Icon:** Dynamic (check-decagram for ready, decagram-outline for not ready)

---

### 25. sensor.localshift_solar_forecast_accuracy

**Purpose:** Solar forecast accuracy compared to actual generation.

Tracks how well the Solcast solar forecast matches actual solar production.

**State Class:** `measurement`

**State:** Solar forecast accuracy percentage (0-100%)

**Example Data:**
```
State: 92.5
Attributes:
  bias: 0.05
  mape: 7.5
  sample_count: 30
```

**Icon:** `mdi:solar-power-variant`

---

### 26. sensor.localshift_extended_forecast_accuracy

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

### 27. sensor.localshift_load_deviation

**Purpose:** Real-time diagnostic for current load-vs-forecast deviation while an optimizer runtime plan is active.

Added in Issue #678. This sensor tracks the absolute deviation between live household load and the current forecast slot, along with the sustained/spike breach window and re-optimization cooldown state.

**State Class:** `measurement`

**State:** Absolute load deviation in kW for the current optimizer slot

**Example Data:**
```
State: 1.25
Attributes:
  status: triggered
  triggered: true
  breach_type: sustained
  actual_kw: 2.25
  forecast_kw: 1.0
  current_slot_index: 0
  sustained_started_at: 2026-03-12T12:00:00+00:00
  spike_started_at: null
  last_triggered_at: 2026-03-12T12:11:00+00:00
  cooldown_until: 2026-03-12T12:26:00+00:00
  cooldown_remaining_seconds: 900
```

**Status values:**

| Status | Meaning |
|--------|---------|
| `inactive` | Optimizer runtime plan is not currently active |
| `no_current_slot` | No forecast slot matches the current time |
| `normal` | Deviation is below all trigger thresholds |
| `pending` | A sustained or spike breach window is accumulating |
| `cooldown` | Re-optimization is temporarily blocked after a recent trigger |
| `triggered` | A sustained/spike breach exceeded its duration and re-optimization was requested |

---

### 25. sensor.localshift_cloud_event

**Purpose:** Real-time diagnostic for solar cloud event detection and re-optimization status.

Added in Issue #685. Monitors actual solar production vs forecast for sudden cloud events. Triggers re-optimization when production drops significantly below Solcast forecast (onset: <50% for >10min; severe: <25% immediate) and detects clearing (production >120% of depressed average for >10min).

**State:** Current actual-to-forecast ratio (dimensionless, 0.0-1.0+); 0.0 when inactive or no forecast

**Example Data:**
```
State: 0.2000
Attributes:
  status: triggered
  triggered: true
  event_type: onset_severe
  actual_kw: 1.0
  forecast_kw: 5.0
  ratio: 0.2
  cloud_scale_factor: 0.2
  depressed_avg_kw: null
  onset_started_at: null
  clearing_started_at: null
  last_triggered_at: 2026-03-12T12:00:00+00:00
  cooldown_until: 2026-03-12T12:15:00+00:00
  cooldown_remaining_seconds: 900
```

**Status values:**

| Status | Meaning |
|--------|---------|
| `inactive` | Optimizer runtime plan is not currently active |
| `no_forecast` | Solcast forecast unavailable or below minimum threshold (0.3 kW) |
| `normal` | Solar production within normal range of forecast |
| `cooldown` | Onset re-trigger is temporarily blocked after a recent event |
| `onset_pending` | Moderate onset (<50%) detected but 10-min window accumulating |
| `cloud_event` | Currently in cloud event, tracking samples for clearing detection |
| `clearing_pending` | Clearing detected but 10-min window accumulating |
| `triggered` | Event threshold exceeded and re-optimization was requested |

**Event types:**

| Event Type | Trigger |
|------------|---------|
| `onset_severe` | Ratio < 25% — triggers immediately |
| `onset_moderate` | Ratio < 50% — triggers after 10-min confirmation |
| `clearing` | Production > 120% of depressed average — triggers after 10-min confirmation |

**Icon:** `mdi:weather-partly-cloudy`

---

### 26. sensor.localshift_optimizer_plan_detailed

**Purpose:** Detailed DP optimizer plan with full decision breakdown.

**Note:** Phase 5 (#447) renamed from `sensor.localshift_optimizer_shadow_plan`. DP optimizer is now the active planner.

**Recorder Note (#467):** The `decisions` attribute is excluded from database recording because it can exceed 26KB (larger than the 16KB recorder limit). The attribute remains available in real-time for dashboard charts, but historical queries will not include it. Other attributes (`enabled`, `success`, `total_slots`, `computed_at`) are recorded normally.

**State:** One of: `computed`, `error`, `disabled`

**Example Data:**
```
State: computed
Attributes:
  enabled: true
  success: true
  error_message: null
  decisions:
    - slot_index: 0
      timestamp_iso: "2026-03-01T10:00:00+11:00"
      action: "hold"
      reason_code: "PRICE_ABOVE_THRESHOLD"
      predicted_soc_pct: 75.5
      grid_import_kwh: 0.0
      grid_export_kwh: 0.0
    - ...
  total_slots: 48
  computed_at: "2026-03-01T10:00:00+11:00"
```

**Icon:** Dynamic (mdi:check-circle for success, mdi:alert-circle for error/disabled)

---

### 29. sensor.localshift_optimizer_summary

**Purpose:** DP optimizer run summary.

**Note:** Phase 5 (#447) renamed from `sensor.localshift_optimizer_shadow_summary`. Shows aggregate metrics from the optimizer run.

**State:** One of: `success`, `failed`, `disabled`

**Example Data:**
```
State: success
Attributes:
  enabled: true
  success: true
  error_message: null
  computed_at: "2026-03-01T10:00:00+11:00"
  config_options:
    battery_target: 80
    cheap_price_percentile: 25
```

**Attributes (existing):**
- `terminal_shortfall_pct` - Projected % shortfall from target at horizon end
- `computed_at` - Timestamp of last optimization run
- `solve_status` - Status of optimizer solve

**Attributes (new):**
- `peak_soc_pct` - Maximum SOC projected in the plan
- `dw_entry_soc_pct` - SOC at demand window entry (null if no DW)
- `projected_solar_gain_pct` - Raw projected solar SOC gain
- `forecast_accuracy` - Current forecast accuracy (0-1)
- `accuracy_discount_factor` - Applied discount (0.5-1.0)
- `adjusted_solar_gain_pct` - Discounted projected solar SOC gain
- `effective_soc_at_terminal` - SOC used in terminal cost calculation

**Icon:** Dynamic (mdi:check-circle-outline for success, mdi:alert-circle-outline for failed, mdi:minus-circle-outline for disabled)

---

### 30. sensor.localshift_comparison_result (Issue #300)

**Purpose:** Comparison result between primary and shadow pricing sources.

Added in Issue #300 for A/B comparison between Amber and Amber Express pricing data.

**State:** `match` if primary and shadow decisions align, `mismatch` otherwise

**Example Data:**
```
State: match
```

**Icon:** `mdi:compare`

---

### 31. sensor.localshift_price_delta (Issue #300)

**Purpose:** Price difference between primary and shadow pricing sources.

Added in Issue #300 to show the delta between Amber and Amber Express prices.

**State:** Price difference in $/kWh

**Example Data:**
```
State: 0.0200
Attributes:
  unit_of_measurement: $/kWh
```

**Icon:** `mdi:currency-usd`

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

### 11. binary_sensor.localshift_amber_demand_window (Issue #300)

**Purpose:** Demand window status from Amber Express integration.

Added in Issue #300 to provide real-time demand window detection from Amber Express.

**State:** `on` when Amber Express demand window is active, `off` otherwise

**Example Data:**
```
State: on
```

**Icon:** Dynamic (clock-alert when on, clock-check when off)

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

### 7. switch.localshift_notifications_enabled

**Purpose:** Enable all LocalShift notifications (consolidated switch).

**Default:** ON

**Controls:**
- Mode transition notifications (e.g., spike discharge started, grid charging active)
- Daily summary notifications (end-of-day cost and energy report)
- Manual action notifications (when force charge/discharge buttons are pressed)
- Alert notifications (health check corrections, automation disabled, errors)

**Example Data:**
```
State: on
```

**Behavior:**
- ON: All notification types are sent
- OFF: All notifications are silenced (useful if you automate on other entities and don't want notification spam)

---

### 8. switch.localshift_enable_learning

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

> **Note:** These entities have `entity_category="config"` and are hidden from the main dashboard by default. Access them via:
> - Device → Configuration section
> - Settings → Devices & Services → LocalShift → Configure → Advanced

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

### 5. number.localshift_cycle_penalty

**Purpose:** Cost penalty per kWh of battery cycling (wear + efficiency loss).

| Property | Value |
|----------|-------|
| Range | $0.00-$0.20/kWh |
| Default | $0.08/kWh |
| Unit | $/kWh |

**Tuning Guide:**
- **Increase** to reduce cycling frequency (more conservative, less arbitrage)
- **Decrease** to enable more arbitrage opportunities
- **$0.00** disables cycle penalty (pure cost minimization, ignores battery wear)

**Example Data:**
```
State: 0.08
```

---

### 6. number.localshift_target_shortfall_penalty

**Purpose:** Penalty per percentage-point below demand window target SOC.

| Property | Value |
|----------|-------|
| Range | $0.000-$0.100/%-point |
| Default | $0.015/%-point |
| Unit | $/%-point |

**Tuning Guide:**
- **Increase** to force earlier/more aggressive grid charging to hit target
- **Decrease** to rely more on solar (wait longer before grid charging)
- **$0.000** disables demand window target enforcement (not recommended)

**Impact:** Lower values (e.g., $0.015 vs $0.030) reduce overnight grid charging urgency.

**Example Data:**
```
State: 0.015
```

---

## Selects (Mode Control)

### 1. select.localshift_battery_mode

**Purpose:** Select battery operating mode for manual control.

Added in Issue #382 to replace manual control buttons with a single select entity.

**Options:**

| Option | Description |
|--------|-------------|
| `automatic` | Enable automation — optimizer controls mode selection |
| `self_consumption` | Default — battery powers house, grid used as needed |
| `grid_charging` | Force charging from grid at 3.3kW (backup mode) |
| `boost_charging` | Force charging at 5kW (autonomous + reserve=100%) |
| `spike_discharge` | Force discharging to export during price spikes |
| `proactive_export` | Exporting battery before negative FIT periods |

**Behavior:**
- When automation is ON: Select displays the actual mode from the optimizer (`self_consumption`, `grid_charging`, `demand_block`, `hold`, etc.)
- When automation is OFF: Select displays the user's manually chosen mode
- When user selects a mode (not "automatic"): Automation disables, user's choice is applied
- When user selects "automatic": Automation enables, optimizer takes control

**Displayed Values:** The select may show modes not in the options list (e.g., `demand_block`, `hold`) when the optimizer transitions to internal states. This reflects the actual system state.

**Example Data:**
```
State: spike_discharge
State: demand_block  (internal optimizer mode, shown when automation ON)
```

**Use Case:** Manual control of battery mode. Select a mode to disable automation and apply that mode to the battery. Select "automatic" to return to automated control. Use `switch.localshift_automation_enabled` to toggle automation directly.

---

### 2. select.localshift_optimization_mode

**Purpose:** Select optimizer objective strategy.

Added in Issue #406 to allow runtime switching between self-consumption and arbitrage objectives.

**Options:**

| Option | Description |
|--------|-------------|
| `self_consumption` | Prioritize keeping battery energy for household load; only export when profitable versus retained value |
| `arbitrage` | Prioritize buy-low/sell-high behavior across available forecast slots |

**Behavior:**
- Updates integration option `optimization_mode`
- Triggers immediate recompute/evaluate cycle
- Changes planner objective and action feasibility

**Example Data:**
```
State: self_consumption
```

**Use Case:** Tune optimizer behavior to match household strategy without editing YAML or restarting Home Assistant.

---

## Buttons (Utility Controls)

### 1. button.localshift_update_forecast

**Action:** Force forecast update and clear historical load cache.

**Effect:** Clears the historical load cache and triggers a forecast regeneration

**Use Case:** Manually refresh forecast data after external changes or for debugging.

**Example Data:**
```
State: 2026-02-24T02:32:04+00:00 (last pressed)
```

---

### 2. button.localshift_reset_learning

**Action:** Reset learning system decision tracking and start fresh.

**Effect:**
- Clears decision tracker state
- Clears recent decision log
- Resets performance metrics
- Returns learning status to "observing" phase

**Use Case:** Start fresh after significant household changes, or if the system has learned sub-optimal parameters.

**Note:** This clears decision history and metrics, not all learned parameters.

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
Selects provide manual mode control
Buttons trigger utility actions
```

---

## Phase 5 Migration (#447)

### Entity Renames

The following entity IDs were changed in Phase 5 when migrating to the DP optimizer:

| Old Entity ID | New Entity ID | Notes |
|--------------|---------------|-------|
| `sensor.localshift_forecast_daily` | `sensor.localshift_optimizer_plan` | Now reads from DP optimizer decisions |
| `sensor.localshift_forecast_grid` | `sensor.localshift_optimizer_plan_grid` | Now reads projected import/export from optimizer |
| `sensor.localshift_optimizer_shadow_plan` | `sensor.localshift_optimizer_plan_detailed` | Removed "shadow" naming |
| `sensor.localshift_optimizer_shadow_summary` | `sensor.localshift_optimizer_summary` | Removed "shadow" naming |
| `sensor.localshift_optimizer_comparison` | **DELETED** | No legacy planner to compare against |

### Attribute Changes

#### sensor.localshift_optimizer_plan (was forecast_daily)

New attributes structure:
```yaml
slots:
  - slot_idx: 0
    action: "hold"
    reason_code: "IDLE"
    objective_terms:
      import_cost: 0.0
      export_revenue: 0.0
      cycle_penalty: 0.0
      shortfall_penalty: 0.0
total_slots: 96
forecast_horizon_hours: 24.0
planner: "DP_OPTIMIZER"
```

#### sensor.localshift_optimizer_plan_grid (was forecast_grid)

New attributes structure:
```yaml
projected_import_kwh: 12.5
projected_export_kwh: 8.3
projected_net_cost: 2.45
action_breakdown:
  HOLD: 70
  CHARGE_GRID_NORMAL: 10
  EXPORT_PROACTIVE: 8
  CHARGE_FOR_DEMAND_WINDOW: 8
planner: "DP_OPTIMIZER"
```

### Binary Sensor Notes

`binary_sensor.localshift_solar_can_reach_target` now reads from `optimizer_result.can_solar_reach_target` — powered by DP optimizer terminal shortfall analysis.

### Dashboard Migration

If you have dashboards or automations referencing the old entity IDs:

1. **Update entity IDs** in your dashboard YAML/configuration
2. **Update attribute references** - the attribute structures have changed
3. **Remove comparison sensor** - delete any references to `sensor.localshift_optimizer_comparison`

---

## Debugging Tips

1. **Start with `select.localshift_battery_mode`** — This shows the current battery mode and allows manual control. When automation is enabled, it displays the automated mode; when disabled, you can manually select modes.

2. **Check binary sensors** — They show the conditions being evaluated:
   - `demand_window` — Are we in peak hours?
   - `price_spike_coming` — Is a spike coming?
   - `solar_can_reach_target` — Can solar fill the battery?
   - `excess_solar_available` — Is there excess solar for load shifting?

3. **Look at attributes** — Many sensors have extra diagnostic attributes:
   - `forecast_prices` shows price thresholds
   - `cost_electricity_net` shows cost breakdown
   - `forecast_battery` shows predicted SOC
   - `excess_solar_kwh` shows detailed excess calculations

4. **Use decision log** — `sensor.localshift_decision_log` shows the last 10 mode changes with reasons.

5. **Enable dry run** — Use `switch.localshift_dry_run` to test without affecting the battery.

6. **Monitor load shift signal** — Use `sensor.localshift_load_shift_signal` for simple automation triggers.

7. **Check forecast diagnostics** — `sensor.localshift_forecast_diagnostics` contains debug fields and consumption profile information for troubleshooting forecast accuracy.
