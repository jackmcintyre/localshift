# LocalShift

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![HA](https://img.shields.io/badge/Home%20Assistant-2025.6+-blue.svg)](https://www.home-assistant.io/)

Automated Tesla Powerwall battery control based on Amber Electric spot pricing, Solcast solar forecasts, and configurable thresholds.

A state machine replaces 18 YAML automations with a single priority-based evaluation that runs every minute and on every relevant state change. No more "stuck state" bugs.

## Features

- **7 battery modes** — Self Consumption, Grid Charging, Boost Charging, Spike Discharge, Proactive Export, Demand Block, Manual
- **Dynamic pricing thresholds** — Urgency-based cheap price calculation that factors in time-to-demand-window, SOC, and solar forecast
- **Solcast solar forecast** — SOC projection to demand window, target-by-DW calculation, boost charge detection
- **Solar export hold** — Holds battery when surplus solar can cover demand window deficit, maximising feed-in revenue
- **Adaptive learning system** — Continuously optimizes decision parameters based on measured outcomes to minimize electricity costs
- **Price spike discharge** — Automatically exports battery during Amber price spikes
- **Demand window blocking** — Prevents grid charging during peak periods (configurable)
- **Weather-aware consumption prediction** — Learns temperature/load correlation for more accurate forecasts during hot/cold days
- **Day-of-week consumption profiles** — Separate weekday and weekend profiles for households with different daily patterns
- **Cost tracking** — Accumulates grid import cost, export revenue, battery savings, and charge cost per day
- **Daily summary notification** — Sends energy and cost summary at demand window end
- **Dry-run mode** — Logs what the state machine would do without sending commands to the Powerwall
- **Included dashboard** — Ready-to-use Lovelace YAML dashboard

## Prerequisites

The following integrations must be installed and configured in Home Assistant:

| Integration | Purpose | Entities Used |
|---|---|---|
| [Teslemetry](https://www.home-assistant.io/integrations/teslemetry/) | Powerwall control | Operation mode, backup reserve, SOC, grid/battery/solar/load power |
| [Amber Electric](https://www.home-assistant.io/integrations/amber/) | Spot pricing | General price, feed-in price, forecasts, price spike |
| [Solcast](https://github.com/BJReplay/ha-solcast-solar) | Solar forecast | Forecast today, forecast tomorrow |

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add `https://github.com/jackmcintyre/ha-solar-battery-automation` as an **Integration**
4. Search for "LocalShift" and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/localshift/` folder to your Home Assistant `custom_components/` directory
2. Restart Home Assistant

## Configuration

### Initial Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "LocalShift"
3. **Step 1 — Teslemetry:** Select your Powerwall entities (operation mode, backup reserve, SOC, grid/battery/solar/load power)
4. **Step 2 — Amber Electric:** Select your Amber entities (general price, feed-in price, forecasts, price spike sensor)
5. **Step 3 — Solcast:** Select your Solcast forecast entities and notification service

Default entity IDs are pre-filled based on standard Teslemetry/Amber/Solcast naming.

### Options (Thresholds)

After setup, click **Configure** on the integration to adjust:

| Option | Default | Description |
|---|---|---|
| Cheap Price Percentile | 25% | Percentile of near-term forecast prices used as the base cheap-price trigger |
| Max Pre-charge Price | $0.20/kWh | Maximum price for pre-DW charging when SOC is low |
| Price Deadband | $0.03/kWh | Hysteresis band to prevent rapid charge/stop cycling |
| Forecast Lookahead | 2 hours | How far ahead to scan for spikes and expensive periods |
| Pre-charge Battery Threshold | 50% | SOC below which pre-charging is considered |
| Battery Target | 100% | Target SOC for demand window |
| Demand Window Start | 15:00 | Start of peak/demand period |
| Demand Window End | 21:00 | End of peak/demand period |

These are also available as number entities on the dashboard for quick adjustment.

## Entities

All entities are grouped under a single **LocalShift** device in Settings → Devices & Services.

### Sensors (26)

| Entity ID | Description |
|---|---|
| `sensor.localshift_price_cheap_effective` | Dynamic cheap price threshold (factors in urgency, SOC, solar) |
| `sensor.localshift_price_cheap_charge_stop` | Effective cheap price + deadband |
| `sensor.localshift_solar_weighted_avg_fit` | Solcast × Amber weighted average feed-in tariff |
| `sensor.localshift_battery_mode` | Current battery mode from the state machine |
| `sensor.localshift_forecast_battery` | SOC projection with detailed attributes |
| `sensor.localshift_cost_electricity_net` | Net cost with import/export/savings/charge cost attributes |
| `sensor.localshift_decision_log` | Mode change history with reasons |
| `sensor.localshift_forecast_history` | Historical forecast predictions for comparison |
| `sensor.localshift_forecast_daily` | Core 24-hour forecast with SOC, solar, and consumption data |
| `sensor.localshift_forecast_prices` | Price forecast data for history collection |
| `sensor.localshift_forecast_grid` | Grid interaction forecast data for history collection |
| `sensor.localshift_forecast_diagnostics` | Diagnostic and debug data for the forecast system |
| `sensor.localshift_target_soc_minimum` | Minimum target SOC for discharge modes |
| `sensor.localshift_excess_solar_kwh` | Forecasted excess solar for load shifting |
| `sensor.localshift_load_shift_signal` | Actionable signal for load-shifting automations |
| `sensor.localshift_forecast_accuracy` | Forecast prediction accuracy tracking |
| `sensor.localshift_integration_status` | Overall integration health status (ok/degraded/error) |
| `sensor.localshift_entity_health` | Detailed health status for all tracked entities |
| `sensor.localshift_daily_thermal_mode` | Current daily thermal mode (HEAT/COOL/DRY/OFF) |
| `sensor.localshift_baseline_load_profile` | Non-HVAC baseline load profile by hour |
| `sensor.localshift_hvac_load_profile` | HVAC load profile by hour |
| `sensor.localshift_learning_status` | Learning system status (observing/tuning/optimizing) |
| `sensor.localshift_decision_quality` | Today's average decision quality score (%) |
| `sensor.localshift_learning_decision_history` | Recent mode decisions with outcomes |
| `sensor.localshift_average_room_temp` | Average room temperature from climate entities |
| `sensor.localshift_realtime_thermal_status` | Real-time thermal control status (active/inactive) |

### Binary Sensors (10)

| Entity ID | Description |
|---|---|
| `binary_sensor.localshift_demand_window` | Whether current time is within the demand window |
| `binary_sensor.localshift_price_spike_coming` | Price spike forecast within lookahead (with `max_forecast_price` attribute) |
| `binary_sensor.localshift_price_expensive_coming` | Expensive period forecast within lookahead |
| `binary_sensor.localshift_discharge_forced` | Powerwall is currently force discharging |
| `binary_sensor.localshift_charge_forced` | Powerwall is currently force charging |
| `binary_sensor.localshift_charge_boost` | Powerwall is currently boost charging (5kW) |
| `binary_sensor.localshift_solar_can_reach_target` | Solar forecast can reach battery target before DW |
| `binary_sensor.localshift_charge_boost_needed` | 5kW boost needed to reach target (3.3kW insufficient) |
| `binary_sensor.localshift_excess_solar_available` | Excess solar available for load shifting |
| `binary_sensor.localshift_tesla_override_active` | Whether Tesla has taken control (Storm Watch, Grid Event, VPP) |
| `binary_sensor.localshift_forecast_expensive_period` | Whether expensive period is forecast within lookahead |

### Switches (11)

| Entity ID | Default | Description |
|---|---|---|
| `switch.localshift_automation_enabled` | ON | Master toggle for all automation |
| `switch.localshift_spike_discharge_enabled` | ON | Allow discharge during price spikes |
| `switch.localshift_spike_discharge_conservative` | OFF | Conservative spike discharge with dynamic reserve |
| `switch.localshift_dry_run` | OFF | Log decisions without sending commands |
| `switch.localshift_demand_window_block` | ON | Block grid charging during demand window |
| `switch.localshift_allow_dw_entry_under_target` | OFF | Allow DW entry under target when solar can reach it |
| `switch.localshift_notify_transitions` | ON | Enable mode transition notifications |
| `switch.localshift_notify_daily_summary` | ON | Enable daily summary notification |
| `switch.localshift_notify_manual_actions` | ON | Enable manual action notifications |
| `switch.localshift_notify_alerts` | ON | Enable alert notifications |
| `switch.localshift_enable_learning` | OFF | Enable learning system parameter optimization |

### Numbers (8)

| Entity ID | Description |
|---|---|
| `number.localshift_cheap_price_percentile` | Forecast price percentile used for cheap-charge baseline (%) |
| `number.localshift_max_pre_charge_price` | Maximum pre-charge price ($/kWh) |
| `number.localshift_cheap_price_deadband` | Price deadband ($/kWh) |
| `number.localshift_forecast_lookahead_hours` | Forecast lookahead window (hours) |
| `number.localshift_battery_target` | Battery target SOC (%) |
| `number.localshift_load_weight_recent` | Weight for recent vs historical consumption |
| `number.localshift_spike_price_percentile` | Price percentile for spike discharge activation |
| `number.localshift_minimum_target_soc` | Minimum SOC during discharge modes |

### Selects (1)

| Entity ID | Description |
|---|---|
| `select.localshift_battery_mode` | Select battery operating mode (self_consumption, grid_charging, boost_charging, spike_discharge, proactive_export). Changing this disables automation and applies manual control. |

### Buttons (3)

| Entity ID | Description |
|---|---|
| `button.localshift_update_forecast` | Force forecast update and clear historical load cache |
| `button.localshift_reset_learning` | Reset learning system data and restart observation |
| `button.localshift_learn_hvac_power` | Proactively learn HVAC power consumption by running each climate entity briefly |

## Dashboard

A ready-to-use Lovelace dashboard is included at `dashboards/localshift.yaml`. All entities used by the dashboard are created automatically by the integration — no additional YAML configuration required.

### Installation

1. Add the dashboard to your `configuration.yaml`:
   ```yaml
   lovelace:
     mode: storage
     dashboards:
       local-shift:  # URL path requires a hyphen
         mode: yaml
         filename: custom_components/localshift/dashboard.yaml
         title: LocalShift
         icon: mdi:battery-sync
   ```
2. Restart Home Assistant
3. Access the dashboard from the sidebar

### Prerequisites

The dashboard uses custom cards that must be installed via HACS:
- [power-flow-card-plus](https://github.com/ulic75/power-flow-card-plus)
- [apexcharts-card](https://github.com/RomRider/apexcharts-card)

The dashboard also references entities from:
- **Teslemetry** — `my_home_*` entities (Powerwall)
- **Amber Electric** — `100h_*` entities (pricing)

Adjust entity IDs in the dashboard YAML to match your setup if different.

## State Machine

The state machine evaluates the full priority chain on every state change and every 1-minute tick:

```
1. Automation disabled?     → MANUAL (no commands sent)
2. Demand window active?    → DEMAND_BLOCK (self consumption)
3. Price spike + enabled?   → SPIKE_DISCHARGE (force discharge)
4. Manual override active?  → MANUAL (preserve user command)
5. Solar export hold?       → SOLAR_EXPORT_HOLD (hold battery)
6. Price < cheap threshold? → GRID_CHARGING (force charge / boost)
7. Price < stop threshold?  → HOLD (deadband — keep current state)
8. Spike forecast?          → HOLDING_FOR_SPIKE (hold battery)
9. None of the above?       → SELF_CONSUMPTION (default)
```

Context-dependent debounce prevents rapid mode switching:
- **0 seconds**: Spike discharge, demand window, manual actions (immediate)
- **2 minutes**: Solar export hold entry/exit
- **5 minutes**: Price-driven transitions (grid charging, hold, self consumption)

## Migration from amber_powerwall (Rebranding)

**If you previously had the `amber_powerwall` integration installed:**

The integration has been rebranded from `amber_powerwall` to `localshift`. You need to manually migrate:

1. **Remove the old integration:**
   - Go to **Settings → Devices & Services**
   - Find the old "Amber Powerwall" integration
   - Click the three dots → **Delete** (this removes the config entry and all old entities)

2. **Clean up stale entities (if any remain):**
   - Go to **Developer Tools → States**
   - Search for `amber_powerwall` - if any entities remain, note them down
   - Go to **Settings → Devices & Services → Entities**
   - Find and delete any orphaned `amber_powerwall_*` entities

3. **Restart Home Assistant** (important for clean entity registration)

4. **Set up the new integration:**
   - Go to **Settings → Devices & Services → Add Integration**
   - Search for "LocalShift"
   - Configure as described in the Configuration section above

5. **Update your dashboard:**
   - The dashboard at `dashboards/localshift.yaml` already uses the new entity IDs
   - If you have a custom dashboard, update entity references:
     - `amber_powerwall_*` → `localshift_*`

## Migration from YAML Package

If you're migrating from the `amber_powerwall.yaml` or `localshift.yaml` package:

1. Install the custom component and configure it
2. Run both side-by-side with the component's **Dry Run** switch ON
3. Compare the component's `sensor.localshift_battery_mode` against YAML automation actions in the logbook
4. When satisfied, turn **Dry Run** OFF and disable YAML automations (`input_boolean.battery_automation_enabled` → OFF)
5. Enable the component's automation switch
6. Monitor for 24 hours
7. Remove the YAML package from `packages/`

## Troubleshooting

### Buttons not working / Entities missing

If button entities show as "missing or not currently available":

1. **Check if the integration is loaded:**
   - Go to **Settings → Devices & Services**
   - Look for the LocalShift integration - it should show as "Loaded"
   - If it shows an error, click **Reload**

2. **Check for old domain conflicts:**
   - If you previously had `amber_powerwall` installed, follow the migration steps above
   - Old entity registry entries can prevent new entities from registering

3. **Check the logs:**
   - Go to **Settings → System → Logs**
   - Filter for "localshift" - look for any error messages
   - On startup, you should see "LocalShift integration set up successfully"

4. **Full restart:**
   - Sometimes Home Assistant needs a full restart to register new entities
   - Restart Home Assistant and check again

## License

MIT