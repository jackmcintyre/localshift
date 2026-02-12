# Migration: YAML Package → Custom Component

## Overview

Converting the `amber_powerwall.yaml` automation package (~2,400 lines YAML, 18 automations) into a proper Home Assistant custom component at `custom_components/amber_powerwall/`.

**Why:** UI configuration, HACS distribution, testability, and a state machine architecture that eliminates the class of "stuck state" bugs that the safety net (A6) currently catches.

**Strategy:** Build the component incrementally alongside the existing YAML package. Both can run side-by-side during development. The YAML package remains the production system until Phase 3 is verified.

---

## Phase 1: Scaffold + Config Flow ✅ COMPLETE

**Files created:**

| File | Purpose |
|---|---|
| `manifest.json` | HA/HACS metadata (`iot_class: calculated`, `integration_type: helper`) |
| `const.py` | All constants: `BatteryMode` enum, config keys, defaults, threshold ranges, display config |
| `config_flow.py` | 3-step entity selection (Teslemetry → Amber → Solcast) + options flow for thresholds |
| `coordinator.py` | Subscribes to 14 external entities + 1-min timer. Reads all state. 5 battery command methods with dry-run support |
| `__init__.py` | Creates coordinator, forwards to 5 platforms, handles options updates |
| `sensor.py` | 9 sensor entity classes wired to `CoordinatorData` fields |
| `binary_sensor.py` | 11 binary sensor entity classes wired to `CoordinatorData` fields |
| `number.py` | 6 number entities (thresholds) backed by config entry options |
| `switch.py` | 4 switches (master toggle, spike discharge, dry run, DW block) |
| `button.py` | 5 manual mode buttons (force charge/discharge, hold, boost, self consumption) |
| `strings.json` | Config flow UI text |
| `translations/en.json` | English translations |
| `hacs.json` | HACS distribution metadata |

**Key decisions made:**
- `OptionsFlow` (not deprecated `OptionsFlowWithConfigEntry`)
- `async_forward_entry_setups` (plural — singular deprecated in HA 2025.6)
- `entry.runtime_data` for coordinator storage
- `async_track_state_change_event` + `async_track_time_interval` (not `DataUpdateCoordinator` — we monitor HA entities, not an external API)
- Custom listener pattern so entity classes subscribe to coordinator updates

**Verify:** Component should load in HA, config flow should walk through entity selection, options should be editable.

---

## Phase 2: Sensors + Binary Sensors ✅ COMPLETE

Port all Jinja template logic from the YAML package to Python in `coordinator._compute_derived_values()`.

### Binary sensors to implement:

| Sensor | YAML Source | Complexity |
|---|---|---|
| `demand_window_active` | Time comparison with DW start/end options | Low |
| `forecast_spike_within_window` | Scan Amber feed-in forecast `forecasts` attribute for `spike_status != "none"` within lookahead | Medium |
| `forecast_expensive_period_coming` | Scan Amber general forecast for prices > `max_precharge_price` within lookahead | Medium |
| `force_discharge_active` | `operation_mode == autonomous && reserve < 11` | Low (done in Phase 1) |
| `force_charge_active` | `operation_mode == backup` | Low (done in Phase 1) |
| `boost_charge_active` | `operation_mode == autonomous && reserve > 99` | Low (done in Phase 1) |
| `hold_active` | `operation_mode == self_consumption && reserve > 10` | Low (done in Phase 1) |
| `solar_can_reach_target` | Solcast `detailedForecast` SOC projection vs target by DW start | High |
| `boost_charge_needed` | Time-to-target at 3.3kW vs 5kW before DW | Medium |
| `hold_justified` | Solar coming OR cheap prices forecast within lookahead | Medium |
| `solar_export_hold_justified` | Surplus ratio (1.5× entry / 1.0× stay), current FIT > weighted avg FIT | High |

### Sensors to implement:

| Sensor | YAML Source | Complexity |
|---|---|---|
| `effective_cheap_price` | Urgency-based dynamic threshold (time-to-DW, SOC, solar forecast) | High |
| `cheap_charge_stop_price` | `effective_cheap_price + deadband` | Low |
| `solar_weighted_avg_fit` | Solcast × Amber weighted average (period overlap matching) | High |
| `grid_import_power` / `grid_export_power` | `max(grid_power, 0)` / `max(-grid_power, 0)` | Low (done in Phase 1) |
| `active_mode` | Priority chain evaluation of all binary sensors | Medium |
| `solar_battery_forecast` | Full SOC projection with attributes (hourly breakdown) | High |
| `net_electricity_cost_today` | `import_cost - export_revenue` | Low (wired in Phase 1, needs Phase 4 data) |
| `decision_log` | Mode change history with timestamps and reasons | Medium |

### Approach:
1. Port one sensor at a time into `_compute_derived_values()`
2. Compare output against the YAML sensor values in HA developer tools
3. Start with the simple ones (demand_window_active, directional power) then build up to the complex forecast-based sensors

**Verify:** All sensor values match the YAML package when both run side-by-side.

---

## Phase 3: State Machine ✅ COMPLETE

Create `state_machine.py` with a single `evaluate()` method that replaces all automation conditions.

### Priority chain (highest to lowest):
1. **Manual override** — user pressed a button, don't touch until cleared
2. **Demand window block** — force self_consumption during DW (A8, A9)
3. **Spike discharge** — export during price spike (A1, A2, A5)
4. **Solar export hold** — hold battery to export solar at high FIT (A17, A18)
5. **Grid charging** — cheap price charging with solar-first philosophy (A3, A4)
6. **Hold** — deadband zone, preserve battery (A10, A11)
7. **Self consumption** — default state

### Key architectural change:
- Every event (state change or 1-min tick) calls `evaluate()` which walks the full priority chain
- No "stuck state" possible — if conditions change, the state machine immediately finds the correct state
- Safety net (A6) becomes unnecessary
- Hysteresis/debounce implemented via `_for` timers on state entry conditions (matching the YAML `for: minutes: N` patterns)

### Files:
- `state_machine.py` — `BatteryStateMachine` class with `evaluate(data: CoordinatorData) -> BatteryMode`
- Update `coordinator.py` — uncomment `_evaluate_state_machine()`, wire to commands
- Switch entities need to be readable by state machine (pass switch states into evaluation)

### Test plan:
- Unit test every transition in `test_state_machine.py`
- Run through all `TEST_SCENARIOS.md` cases
- Side-by-side comparison with YAML package (both running, only component controlling battery)

**Verify:** State transitions match YAML behaviour for all test scenarios.

---

## Phase 4: Cost Tracking + Daily Operations ✅ COMPLETE

### Cost accumulation (replaces A16):
- On each 1-min tick in coordinator: `power_kW × price_$/kWh / 60 = $/min`
- Accumulate into `CoordinatorData` fields (grid_import_cost, grid_export_revenue, battery_savings, battery_charge_cost)
- These are in-memory; persist to `hass.data` or config entry storage for restart survival

### Daily reset (replaces A12):
- At midnight: reset cost accumulators, clear `target_reached_today`
- Use `async_track_time_change` with `hour=0, minute=0, second=0`

### Daily summary (replaces A15):
- At demand window end time: send notification with day's stats
- Use `async_track_time_change` with DW end hour/minute

### Target reached (replaces A13):
- When SOC exceeds battery_target option, set internal flag
- Checked by state machine to prevent re-charging after target hit

### Integration sensors + utility meters:
- **Decision:** Keep as separate YAML for now (simplest). Can programmatically create later.
- The Riemann sum integration sensors (`grid_import_energy`, `grid_export_energy`, `solar_production_energy`) and daily utility meters still reference the component's power sensors by entity ID.

**Verify:** Cost accumulation matches YAML A16 output over a 24-hour period. Daily summary fires correctly.

---

## Phase 5: Dashboard + Entity IDs ✅ COMPLETE

### Entity ID strategy:
- Set `_attr_has_entity_name = True` with `device_info` on all entity base classes
- All entities grouped under a single "Amber Powerwall" device in Settings → Devices & Services
- Entity IDs prefixed with device name: e.g. `sensor.amber_powerwall_effective_cheap_price`, `switch.amber_powerwall_automation_enabled`
- Changed `integration_type` from `"helper"` to `"device"` in manifest.json (Integrations tab, not Helpers)

### Dashboard update (`dashboards/amber_powerwall.yaml`):
- **Controls:** `input_boolean.*` → `switch.amber_powerwall_*`, `input_number.*` → `number.amber_powerwall_*`, removed DW time tiles
- **Manual Actions:** `script.*` → `button.press` with `button.amber_powerwall_*` targets
- **Battery Mode markdown:** Reads `sensor.amber_powerwall_active_mode` instead of individual `input_boolean` entities
- **Solar Plan markdown:** Uses `binary_sensor.amber_powerwall_demand_window_active` and `number.amber_powerwall_battery_target`
- **Insights markdown:** Reads from `sensor.amber_powerwall_net_electricity_cost_today` attributes

### Still TODO:
- Add `README.md` with install instructions, configuration guide, entity list
- Tag a release on GitHub for HACS distribution
- Final cutover procedure:
  1. Disable all YAML automations (toggle `battery_automation_enabled` off in YAML)
  2. Enable the custom component's automation switch
  3. Monitor for 24 hours
  4. Remove YAML package from `packages/` directory
  5. Remove input entities from HA (replaced by component entities)

**Verify:** Load component, confirm entity IDs in Developer Tools → States, dashboard renders without errors.

---

## Effort Estimate

| Phase | Est. Hours | Lines of Python | Status |
|---|---|---|---|
| Phase 1: Scaffold + Config | 3-5 | ~400 | ✅ Complete |
| Phase 2: Sensors | 6-10 | ~800 | ✅ Complete |
| Phase 3: State Machine | 10-15 | ~1,000 | ✅ Complete |
| Phase 4: Cost Tracking | 3-5 | ~200 | ✅ Complete |
| Phase 5: Dashboard + Dist | 2-4 | ~100 | ✅ Complete |
| **Total** | **24-39 hours** | **~2,500 lines** | |
