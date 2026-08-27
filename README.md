# LocalShift

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![HA](https://img.shields.io/badge/Home%20Assistant-Custom%20Integration-blue.svg)](https://www.home-assistant.io/)

LocalShift is a forecast-driven optimizer for a Tesla Powerwall. Every few
minutes it computes the cost-optimal 24-hour charge/discharge plan from Amber
Electric spot prices, Solcast solar forecasts, and learned household
consumption — then drives the Powerwall to follow that plan.

The plan is produced by a **dynamic-programming (DP) optimizer**, not a pile of
threshold rules. Replacing the original 18 hand-written YAML automations with a
single planning model eliminates "stuck state" bugs and makes every decision
testable, observable, and explainable: the forecast *is* the plan, and the
control logic simply executes it.

## How it works

LocalShift runs as four cooperating layers. The deeper design lives in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[`docs/PLANNING_MODEL.md`](docs/PLANNING_MODEL.md); this is the short version.

```
 prices ┐
 solar  ├─▶ forecast pipeline ─▶ DP optimizer ─▶ plan ─▶ state machine ─▶ Powerwall
 load   ┘    (24h of slots)      (engine/)     (per-slot   (state/)        (Teslemetry)
                                                 action)         │
                                                                 ▼
                                            measured outcomes ─▶ learning (learning/)
                                                                 (adapts parameters)
```

1. **Forecast pipeline** (`forecast/`) builds 24 hours of *hybrid slots* — 5-minute
   resolution for roughly the first hour (matching Amber's 5-minute pricing) and
   30-minute resolution further out (matching Solcast) — each carrying a price,
   solar forecast, and load forecast.

2. **DP optimizer** (`engine/`) searches that horizon for the cheapest feasible plan
   using a three-layer model:
   - `feasible_actions()` — **hard constraints**: SOC floor/ceiling, demand-window
     grid-charge block, price/solar/export gates, and an anti-cycling
     *minimum-cycle-saving* gate that refuses to cycle the battery for a thin margin.
   - `stage_cost()` — **soft economic penalties**: grid-import cost, export revenue,
     a switching penalty that damps mode flapping, and a solar-opportunity penalty.
   - `terminal_cost()` — **the deadline**: a penalty for missing the demand-window
     SOC target.

   The output is the optimal action for each slot (hold, grid-charge, boost-charge,
   pre-charge, export, spike-discharge).

3. **State machine** (`state/`) *executes* the current slot's action by mapping it to
   a Powerwall mode, applying context-dependent debounce and honoring manual and
   Tesla (Storm Watch / Grid Event / VPP) overrides. It follows the plan — it does
   not decide it.

4. **Learning system** (`learning/`) measures the outcome of each decision and slowly
   adapts the optimizer's parameters to reduce cost over time. It is **off by default**
   and moves through *observing → tuning → optimizing* phases with safety rails. See
   [`docs/LEARNING_SYSTEM.md`](docs/LEARNING_SYSTEM.md).

### Optimization modes

The objective is selectable at runtime via `select.localshift_optimization_mode`:

- **`self_consumption`** (default) — retain stored energy for household load; export
  only when the sale beats the value of keeping that energy. Low overnight SOC is
  expected, not a bug.
- **`arbitrage`** — maximize buy-low / sell-high margin, exporting proactively
  whenever the spread clears the thresholds.

Changes take effect on the next recompute cycle.

## Features

- **8 battery modes** — Self Consumption, Grid Charging, Boost Charging, Spike
  Discharge, Proactive Export, Demand Block, Hold, Manual
- **DP-optimized 24-hour plan** — global cost minimization over hybrid 5-/30-minute
  slots, recomputed whenever the decision context changes
- **Dynamic pricing thresholds** — urgency-based cheap-price calculation that factors
  in time-to-demand-window, SOC, and solar forecast
- **Solcast solar forecast** — SOC projection to the demand window, target-by-DW
  calculation, and 3.3 kW-vs-5 kW boost-charge detection
- **Solar export hold** — holds the battery when surplus solar can cover the
  demand-window deficit, maximising feed-in revenue
- **Price spike discharge** — exports during Amber price spikes
- **Demand window blocking** — prevents grid charging during peak periods (configurable)
- **Anti-cycling gate** — a minimum-cycle-saving feasibility gate that blocks marginal
  overnight arbitrage while preserving genuine pre-charge and spike capture
- **Adaptive learning** — continuously tunes decision parameters from measured outcomes
- **Counterfactual advantage tracking** — quantifies how much the optimizer saved vs a
  baseline strategy
- **Weather-aware consumption prediction** — learns temperature/load correlation for
  more accurate forecasts on hot/cold days
- **Day-of-week consumption profiles** — separate weekday and weekend load profiles
- **Cost tracking** — accumulates grid import cost, export revenue, battery savings,
  and charge cost per day
- **Daily summary notification** — energy and cost summary at demand-window end
- **Dry-run mode** — logs what the optimizer would do without sending commands
- **Included dashboard** — ready-to-use Lovelace dashboard built on bundled custom cards

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

### Options

After setup, click **Configure** on the integration to adjust:

| Option | Default | Description |
|---|---|---|
| Cheap Price Percentile | 25% | Percentile of near-term forecast prices used as the base cheap-price trigger |
| Max Pre-charge Price | $0.20/kWh | Maximum price for pre-DW charging when SOC is low |
| Price Deadband | $0.03/kWh | Hysteresis band to prevent rapid charge/stop cycling |
| Min Cycle Saving | $0.25/kWh | Minimum saving over holding required to justify cycling the battery |
| Forecast Lookahead | 2 hours | How far ahead to scan for spikes and expensive periods |
| Battery Target | 100% | Target SOC for the demand window |
| Minimum Target SOC | 20% | Minimum SOC retained during discharge modes |
| Target Shortfall Penalty | $0.015/%-pt | Optimizer cost per %-point below the demand-window target |
| Demand Window Start | 15:00 | Start of peak/demand period |
| Demand Window End | 21:00 | End of peak/demand period |

Many of these — plus extra optimizer knobs (Switching Penalty, Stale-Solar
Confidence Ceiling) — are also exposed as number entities on the dashboard for
quick adjustment without reopening the Configure dialog.

## Entities

All entities are grouped under a single **LocalShift** device in Settings →
Devices & Services. Full canonical reference:
[`docs/ENTITY_REFERENCE.md`](docs/ENTITY_REFERENCE.md).

### Sensors (34)

| Entity ID | Description |
|---|---|
| `sensor.localshift_price_cheap_effective` | Dynamic cheap-price threshold (factors in urgency, SOC, solar) |
| `sensor.localshift_price_cheap_charge_stop` | Effective cheap price + deadband (charge-stop hysteresis) |
| `sensor.localshift_solar_weighted_avg_fit` | Solcast × Amber weighted-average feed-in tariff |
| `sensor.localshift_comparison_result` | Optimizer-vs-baseline comparison result |
| `sensor.localshift_price_delta` | Price-delta metrics for the comparison harness |
| `sensor.localshift_forecast_battery` | SOC projection to the demand window, with detailed attributes |
| `sensor.localshift_cost_electricity_net` | Net daily cost (import/export/savings/charge-cost attributes) |
| `sensor.localshift_decision_log` | Mode-change history with reasons |
| `sensor.localshift_forecast_history` | Historical forecast predictions for accuracy comparison |
| `sensor.localshift_optimizer_plan` | Core 24-hour plan with SOC, solar, and consumption per slot |
| `sensor.localshift_forecast_prices` | Price-forecast series for history collection |
| `sensor.localshift_optimizer_plan_grid` | Grid-interaction forecast series |
| `sensor.localshift_load_deviation` | Live load vs forecast deviation |
| `sensor.localshift_forecast_diagnostics` | Diagnostic/debug data for the forecast system |
| `sensor.localshift_target_soc_minimum` | Minimum target SOC for discharge modes |
| `sensor.localshift_excess_solar` | Forecasted excess solar available for load shifting |
| `sensor.localshift_load_shift_signal` | Actionable signal for load-shifting automations |
| `sensor.localshift_forecast_accuracy` | Forecast prediction accuracy tracking |
| `sensor.localshift_integration_status` | Overall integration health (ok/degraded/error) |
| `sensor.localshift_entity_health` | Per-entity health detail for all tracked entities |
| `sensor.localshift_learning_status` | Learning-system phase (observing/tuning/optimizing) |
| `sensor.localshift_decision_quality` | Today's average decision-quality score (%) |
| `sensor.localshift_learning_decision_history` | Recent mode decisions with measured outcomes |
| `sensor.localshift_optimizer_advantage` | Counterfactual advantage of the optimizer vs baseline |
| `sensor.localshift_decision_lag` | Decision-to-implementation lag |
| `sensor.localshift_forecast_status` | Forecast freshness/readiness status |
| `sensor.localshift_automation_ready` | Whether automation has everything it needs to act |
| `sensor.localshift_optimizer_plan_detailed` | Full slot-by-slot DP plan (drives the Debug view) |
| `sensor.localshift_optimizer_summary` | Plan summary metrics (cost, shortfall, charge window) |
| `sensor.localshift_solar_forecast_accuracy` | Measured solar-forecast accuracy |
| `sensor.localshift_cloud_event` | Tesla cloud event (Storm Watch / Grid Event / VPP) |
| `sensor.localshift_solcast_confidence_today` | Solcast forecast confidence for today |
| `sensor.localshift_solcast_confidence_tomorrow` | Solcast forecast confidence for tomorrow |
| `sensor.localshift_forecast_accuracy_comparison` | Forecast-accuracy comparison across sources |

### Binary Sensors (11)

| Entity ID | Description |
|---|---|
| `binary_sensor.localshift_demand_window` | Whether current time is within the demand window |
| `binary_sensor.localshift_price_spike_coming` | Price spike forecast within lookahead (with `max_forecast_price` attribute) |
| `binary_sensor.localshift_price_expensive_coming` | Expensive period forecast within lookahead |
| `binary_sensor.localshift_discharge_forced` | Powerwall is currently force-discharging |
| `binary_sensor.localshift_charge_forced` | Powerwall is currently force-charging |
| `binary_sensor.localshift_charge_boost` | Powerwall is currently boost-charging (5 kW) |
| `binary_sensor.localshift_solar_can_reach_target` | Solar forecast can reach the battery target before the DW |
| `binary_sensor.localshift_charge_boost_needed` | 5 kW boost needed to reach target (3.3 kW insufficient) |
| `binary_sensor.localshift_excess_solar_available` | Excess solar available for load shifting |
| `binary_sensor.localshift_tesla_override_active` | Tesla has taken control (Storm Watch, Grid Event, VPP) |
| `binary_sensor.localshift_amber_demand_window` | Amber Express demand-window signal active |

### Switches (9)

| Entity ID | Default | Description |
|---|---|---|
| `switch.localshift_automation_enabled` | ON | Master toggle for all automation |
| `switch.localshift_spike_discharge_enabled` | ON | Allow discharge during price spikes |
| `switch.localshift_spike_discharge_conservative` | OFF | Conservative spike discharge with dynamic reserve |
| `switch.localshift_dry_run` | OFF | Log decisions without sending commands |
| `switch.localshift_demand_window_block` | ON | Block grid charging during the demand window |
| `switch.localshift_allow_dw_entry_under_target` | OFF | Allow DW entry under target when solar can reach it |
| `switch.localshift_stale_solar_conservative` | ON | Cap solar confidence when Solcast is stale/absent |
| `switch.localshift_notifications_enabled` | ON | Enable all notifications (transitions, summaries, manual actions, alerts) |
| `switch.localshift_enable_learning` | OFF | Enable learning-system parameter optimization |

### Numbers (8)

| Entity ID | Description |
|---|---|
| `number.localshift_cheap_price_percentile` | Forecast price percentile used for the cheap-charge baseline (%) |
| `number.localshift_max_pre_charge_price` | Maximum pre-charge price ($/kWh) |
| `number.localshift_min_cycle_saving` | Minimum saving over holding to justify a battery cycle ($/kWh) |
| `number.localshift_battery_target` | Battery target SOC for the demand window (%) |
| `number.localshift_minimum_target_soc` | Minimum SOC during discharge modes (%) |
| `number.localshift_target_shortfall_penalty` | Optimizer cost per %-point of demand-window shortfall ($) |
| `number.localshift_stale_solar_confidence_ceiling` | Solar-confidence cap applied when Solcast is stale/absent |
| `number.localshift_switching_penalty` | Per-switch disincentive that damps mode flapping ($) |

### Selects (2)

| Entity ID | Description |
|---|---|
| `select.localshift_battery_mode` | Battery operating mode (`automatic`, `self_consumption`, `grid_charging`, `boost_charging`, `spike_discharge`, `proactive_export`). Selecting a manual mode disables automation and applies that command; `automatic` hands control back to the optimizer. |
| `select.localshift_optimization_mode` | Optimizer objective (`self_consumption` or `arbitrage`). Takes effect on the next recompute cycle. |

### Buttons (2)

| Entity ID | Description |
|---|---|
| `button.localshift_update_forecast` | Force a forecast update and clear the historical-load cache |
| `button.localshift_reset_learning` | Reset learning-system data and restart observation |

## Dashboard

A ready-to-use Lovelace dashboard ships with the integration at
`custom_components/localshift/dashboard.yaml`. All entities it uses are created
automatically by the integration — no extra YAML configuration required.

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
2. Install the dashboard's custom cards (see Prerequisites below)
3. Restart Home Assistant
4. Access the dashboard from the sidebar

The dashboard has three views: **Overview** (mission control — a command card
with live status, power flows, and a fused plan timeline, plus the decision
feed, money, and quick controls), **Settings** (all controls grouped by how
often you touch them), and **Debug** (full state dump and the slot-by-slot
optimizer plan).

### Prerequisites

The Overview is built on LocalShift's own Lovelace cards — **no HACS cards
required**. The bundle ships with the integration at
`custom_components/localshift/www/localshift-cards.js` and defines three card
types: `localshift-command-card`, `localshift-decisions-card`, and
`localshift-money-card`. Install it once:

1. Copy `custom_components/localshift/www/localshift-cards.js` to
   `<your-ha-config>/www/localshift/localshift-cards.js`
2. **Settings → Dashboards → Resources → Add resource:**
   - URL: `/local/localshift/localshift-cards.js`
   - Type: `JavaScript module`

The dashboard also references entities from:
- **Teslemetry** — `my_home_*` entities (Powerwall)
- **Amber Electric** — `100h_*` entities (pricing)

Adjust entity IDs to match your setup if different — each card accepts an
`entities:` map, and the command card takes `dw_start` / `dw_end` for your
peak (demand-window) hours.

## Troubleshooting

See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) for optimizer- and
state-machine-specific issues. For entity/registration problems:

### Buttons not working / Entities missing

If entities show as "missing or not currently available":

1. **Check if the integration is loaded:**
   - Go to **Settings → Devices & Services**
   - Look for the LocalShift integration - it should show as "Loaded"
   - If it shows an error, click **Reload**

2. **Check for old domain conflicts:**
   - If you previously ran another battery-automation integration, delete it and
     any orphaned entities it left behind
   - Old entity registry entries can prevent new entities from registering

3. **Check the logs:**
   - Go to **Settings → System → Logs**
   - Filter for "localshift" - look for any error messages
   - On startup, you should see "LocalShift integration set up successfully"

4. **Full restart:**
   - Sometimes Home Assistant needs a full restart to register new entities
   - Restart Home Assistant and check again

## Documentation

| Doc | Contents |
|---|---|
| [`docs/INDEX.md`](docs/INDEX.md) | Documentation index — **start here** |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design, components, and data flow |
| [`docs/PLANNING_MODEL.md`](docs/PLANNING_MODEL.md) | The DP model: feasible actions, stage cost, terminal cost |
| [`docs/FORECAST_DRIVEN_CONTROL.md`](docs/FORECAST_DRIVEN_CONTROL.md) | "The forecast IS the plan" — single source of truth |
| [`docs/ENTITY_REFERENCE.md`](docs/ENTITY_REFERENCE.md) | Complete entity catalog |
| [`docs/LEARNING_SYSTEM.md`](docs/LEARNING_SYSTEM.md) | Adaptive learning: phases, parameters, safety rails |
| [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) | Setup, project structure, extension patterns |
| [`VISION.md`](VISION.md) | Mission, goals, constraints, success metrics |

## License

MIT
