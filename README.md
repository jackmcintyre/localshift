# Amber Powerwall

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![HA](https://img.shields.io/badge/Home%20Assistant-2025.6+-blue.svg)](https://www.home-assistant.io/)

Automated Tesla Powerwall 2 battery control based on Amber Electric spot pricing, Solcast solar forecasts, and configurable thresholds.

A state machine replaces 18 YAML automations with a single priority-based evaluation that runs every minute and on every relevant state change. No more "stuck state" bugs.

## Features

- **9 battery modes** — Self Consumption, Hold, Grid Charging, Boost Charging, Spike Discharge, Solar Export Hold, Holding for Spike, Demand Block, Manual
- **Dynamic pricing thresholds** — Urgency-based cheap price calculation that factors in time-to-demand-window, SOC, and solar forecast
- **Solcast solar forecast** — SOC projection to demand window, target-by-DW calculation, boost charge detection
- **Solar export hold** — Holds battery when surplus solar can cover demand window deficit, maximising feed-in revenue
- **Price spike discharge** — Automatically exports battery during Amber price spikes
- **Demand window blocking** — Prevents grid charging during peak periods (configurable)
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
4. Search for "Amber Powerwall" and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/amber_powerwall/` folder to your Home Assistant `custom_components/` directory
2. Restart Home Assistant

## Configuration

### Initial Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "Amber Powerwall"
3. **Step 1 — Teslemetry:** Select your Powerwall entities (operation mode, backup reserve, SOC, grid/battery/solar/load power)
4. **Step 2 — Amber Electric:** Select your Amber entities (general price, feed-in price, forecasts, price spike sensor)
5. **Step 3 — Solcast:** Select your Solcast forecast entities and notification service

Default entity IDs are pre-filled based on standard Teslemetry/Amber/Solcast naming.

### Options (Thresholds)

After setup, click **Configure** on the integration to adjust:

| Option | Default | Description |
|---|---|---|
| Cheap Price Threshold | $0.15/kWh | Base price below which grid charging starts |
| Max Pre-charge Price | $0.20/kWh | Maximum price for pre-DW charging when SOC is low |
| Price Deadband | $0.03/kWh | Hysteresis band to prevent rapid charge/stop cycling |
| Forecast Lookahead | 2 hours | How far ahead to scan for spikes and expensive periods |
| Pre-charge Battery Threshold | 50% | SOC below which pre-charging is considered |
| Battery Target | 100% | Target SOC for demand window |
| Demand Window Start | 15:00 | Start of peak/demand period |
| Demand Window End | 21:00 | End of peak/demand period |

These are also available as number entities on the dashboard for quick adjustment.

## Entities

All entities are grouped under a single **Amber Powerwall** device in Settings → Devices & Services.

### Sensors (9)

| Entity ID | Description |
|---|---|
| `sensor.amber_powerwall_effective_cheap_price` | Dynamic cheap price threshold (factors in urgency, SOC, solar) |
| `sensor.amber_powerwall_cheap_charge_stop_price` | Effective cheap price + deadband |
| `sensor.amber_powerwall_solar_weighted_avg_fit` | Solcast × Amber weighted average feed-in tariff |
| `sensor.amber_powerwall_active_mode` | Current battery mode from the state machine |
| `sensor.amber_powerwall_solar_battery_forecast` | SOC projection with detailed attributes |
| `sensor.amber_powerwall_grid_import_power` | Current grid import (kW) |
| `sensor.amber_powerwall_grid_export_power` | Current grid export (kW) |
| `sensor.amber_powerwall_net_electricity_cost_today` | Net cost with import/export/savings/charge cost attributes |
| `sensor.amber_powerwall_decision_log` | Mode change history with reasons |

### Binary Sensors (11)

| Entity ID | Description |
|---|---|
| `binary_sensor.amber_powerwall_demand_window_active` | Whether current time is within the demand window |
| `binary_sensor.amber_powerwall_forecast_spike_within_window` | Price spike forecast within lookahead (with `max_forecast_price` attribute) |
| `binary_sensor.amber_powerwall_forecast_expensive_period_coming` | Expensive period forecast within lookahead |
| `binary_sensor.amber_powerwall_force_discharge_active` | Powerwall is currently force discharging |
| `binary_sensor.amber_powerwall_force_charge_active` | Powerwall is currently force charging |
| `binary_sensor.amber_powerwall_boost_charge_active` | Powerwall is currently boost charging (5kW) |
| `binary_sensor.amber_powerwall_hold_active` | Powerwall is currently holding |
| `binary_sensor.amber_powerwall_solar_can_reach_target` | Solar forecast can reach battery target before DW |
| `binary_sensor.amber_powerwall_boost_charge_needed` | 5kW boost needed to reach target (3.3kW insufficient) |
| `binary_sensor.amber_powerwall_hold_justified` | Hold mode justified (solar coming or cheap prices forecast) |
| `binary_sensor.amber_powerwall_solar_export_hold_justified` | Solar export hold justified (with `surplus_ratio` attribute) |

### Switches (4)

| Entity ID | Default | Description |
|---|---|---|
| `switch.amber_powerwall_automation_enabled` | ON | Master toggle for all automation |
| `switch.amber_powerwall_spike_discharge_enabled` | ON | Allow discharge during price spikes |
| `switch.amber_powerwall_dry_run` | OFF | Log decisions without sending commands |
| `switch.amber_powerwall_demand_window_block` | ON | Block grid charging during demand window |

### Numbers (6)

| Entity ID | Description |
|---|---|
| `number.amber_powerwall_cheap_price_threshold` | Base cheap price threshold ($/kWh) |
| `number.amber_powerwall_max_pre_charge_price` | Maximum pre-charge price ($/kWh) |
| `number.amber_powerwall_price_deadband` | Price deadband ($/kWh) |
| `number.amber_powerwall_forecast_lookahead` | Forecast lookahead window (hours) |
| `number.amber_powerwall_pre_charge_battery_threshold` | Pre-charge SOC threshold (%) |
| `number.amber_powerwall_battery_target` | Battery target SOC (%) |

### Buttons (5)

| Entity ID | Description |
|---|---|
| `button.amber_powerwall_force_charge` | Manually force charge (backup mode, 3.3kW) |
| `button.amber_powerwall_force_discharge` | Manually force discharge (autonomous, reserve=0) |
| `button.amber_powerwall_hold_battery` | Manually hold at current SOC |
| `button.amber_powerwall_boost_charge_5kw` | Manually boost charge at 5kW |
| `button.amber_powerwall_return_to_self_consumption` | Clear manual override, return to automation |

## Dashboard

A ready-to-use Lovelace dashboard is included at `dashboards/amber_powerwall.yaml`.

To install:
1. Go to **Settings → Dashboards → Add Dashboard**
2. Choose "New dashboard from scratch" with YAML mode
3. Paste the contents of `dashboards/amber_powerwall.yaml`

### Required YAML helpers

The dashboard's energy tracking cards require Riemann sum integration sensors and utility meters that are **not** created by the component. Add these to your `configuration.yaml` or a package:

```yaml
sensor:
  - platform: integration
    source: sensor.amber_powerwall_grid_import_power
    name: grid_import_energy
    unit_prefix: k
    round: 3
    method: trapezoidal

  - platform: integration
    source: sensor.amber_powerwall_grid_export_power
    name: grid_export_energy
    unit_prefix: k
    round: 3
    method: trapezoidal

  - platform: integration
    source: sensor.my_home_solar_power
    name: solar_production_energy
    unit_prefix: k
    round: 3
    method: trapezoidal

utility_meter:
  grid_import_energy_daily:
    source: sensor.grid_import_energy
    cycle: daily
  grid_export_energy_daily:
    source: sensor.grid_export_energy
    cycle: daily
  solar_production_energy_daily:
    source: sensor.solar_production_energy
    cycle: daily
```

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

## Migration from YAML Package

If you're migrating from the `amber_powerwall.yaml` package:

1. Install the custom component and configure it
2. Run both side-by-side with the component's **Dry Run** switch ON
3. Compare the component's `sensor.amber_powerwall_active_mode` against YAML automation actions in the logbook
4. When satisfied, turn **Dry Run** OFF and disable YAML automations (`input_boolean.battery_automation_enabled` → OFF)
5. Enable the component's automation switch
6. Monitor for 24 hours
7. Remove the YAML package from `packages/`

## License

MIT
