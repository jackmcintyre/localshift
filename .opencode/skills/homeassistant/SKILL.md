---
name: homeassistant
description: Home Assistant read-only gateway - states, attributes, history and logbook
license: MIT
compatibility: opencode
metadata:
  audience: developers
  workflow: debugging
---

## Read-only: READ THIS FIRST

This gateway only reads Home Assistant: entity states, attributes, history,
statistics and the logbook. It never changes anything.

- The token must be a **read-only (non-admin) user's** long-lived token. Never
  use or ask for an admin token.
- Service calls are expected to fail with that token. Do not call services,
  turn anything on or off, set values, reload, restart or edit configuration,
  and do not look for another way to do it.
- Any change to the house goes through a reviewed PR that merges to `main`.
  If a task needs a write, stop and say so; a human or a PR does it.

## Prerequisites

The launcher sets these for the agent process:
- `HOMEASSISTANT_URL` - Home Assistant URL
- `HA_LONG_LIVED_TOKEN` - the read-only user's long-lived token

`hass-cli` wants `HASS_SERVER` and `HASS_TOKEN`, so wrap each command:

```bash
HASS_SERVER="${HOMEASSISTANT_URL%/}" HASS_TOKEN="$HA_LONG_LIVED_TOKEN" \
  uvx --from homeassistant-cli hass-cli ...
```

Do not assume interactive shell exports reach subprocesses. Quick connectivity
check before reads:

```bash
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $HA_LONG_LIVED_TOKEN" \
  "${HOMEASSISTANT_URL%/}/api/config"
```

`200` means reachable. `000` or a DNS error means the URL is not reachable
from this runtime; `401` means the token is wrong. Stop and report either one.

## If a read fails

Try at most two approaches (for example `state get`, then the REST call), then
STOP and report the exact error. Do not guess or assume entity states, do not
continue with stale data, and do not silently ignore failures.

---

## Reading state

```bash
# Get one entity (JSON)
HASS_SERVER="${HOMEASSISTANT_URL%/}" HASS_TOKEN="$HA_LONG_LIVED_TOKEN" \
  uvx --from homeassistant-cli hass-cli --output json state get sensor.localshift_battery_percent

# List entities, or search them
HASS_SERVER="${HOMEASSISTANT_URL%/}" HASS_TOKEN="$HA_LONG_LIVED_TOKEN" \
  uvx --from homeassistant-cli hass-cli entity list | grep -i battery
```

Attributes are in the `attributes` object of the JSON state.

## History, statistics and logbook

```bash
# History (default last 24h)
HASS_SERVER="${HOMEASSISTANT_URL%/}" HASS_TOKEN="$HA_LONG_LIVED_TOKEN" \
  uvx --from homeassistant-cli hass-cli --output json raw GET \
  "api/history/period?filter_entity_id=sensor.localshift_battery_percent"

# Statistics
HASS_SERVER="${HOMEASSISTANT_URL%/}" HASS_TOKEN="$HA_LONG_LIVED_TOKEN" \
  uvx --from homeassistant-cli hass-cli --output json raw GET \
  "api/statistics_during_period?entity_ids=sensor.localshift_battery_percent&period=day"

# Logbook
HASS_SERVER="${HOMEASSISTANT_URL%/}" HASS_TOKEN="$HA_LONG_LIVED_TOKEN" \
  uvx --from homeassistant-cli hass-cli --output json raw GET \
  "api/logbook?entity=sensor.localshift_current_mode"
```

---

## LocalShift Entity Quick Reference

### Control Entities (read their state; do not change them)

| Entity | Type | Purpose |
|--------|------|---------|
| `switch.localshift_automation_enabled` | switch | Master automation toggle |
| `switch.localshift_dry_run` | switch | Dry run mode (off = live) |
| `switch.localshift_spike_discharge_enabled` | switch | Enable spike discharge |
| `switch.localshift_enable_learning` | switch | Learning system toggle |
| `select.localshift_battery_mode` | select | Manual mode control |
| `select.localshift_optimization_mode` | select | Optimizer strategy |

### Key Sensors

| Entity | Purpose |
|--------|---------|
| `sensor.localshift_battery_percent` | Battery SOC (%) |
| `sensor.localshift_current_mode` | Current operating mode |
| `sensor.localshift_grid_price` | Current grid price ($/kWh) |
| `sensor.localshift_decision_log` | Recent mode changes with reasons |
| `sensor.localshift_forecast_battery` | Predicted SOC at demand window |
| `sensor.localshift_cost_electricity_net` | Daily net cost |
| `sensor.localshift_optimizer_plan` | 24-hour optimizer plan |
| `sensor.localshift_optimizer_summary` | Optimizer run summary |
| `sensor.localshift_integration_status` | Integration health (ok/degraded/error) |
| `sensor.localshift_automation_ready` | Automation readiness |
| `sensor.localshift_load_shift_signal` | Load shift signal (INCREASE/MAINTAIN/REDUCE_LOAD) |

### Binary Sensors

| Entity | Purpose |
|--------|---------|
| `binary_sensor.localshift_demand_window` | In demand window (peak hours) |
| `binary_sensor.localshift_price_spike_coming` | Price spike forecast |
| `binary_sensor.localshift_solar_can_reach_target` | Solar can fill battery |
| `binary_sensor.localshift_charge_boost_needed` | 5kW boost needed |
| `binary_sensor.localshift_excess_solar_available` | Excess solar available |
| `binary_sensor.localshift_tesla_override_active` | Tesla has control |

### Configuration Numbers

| Entity | Purpose | Range |
|--------|---------|-------|
| `number.localshift_battery_target` | Target SOC for demand window | 50-100% |
| `number.localshift_cheap_price_percentile` | Base cheap price threshold | 5-50% |
| `number.localshift_minimum_target_soc` | Min SOC during discharge | 5-30% |
| `number.localshift_max_pre_charge_price` | Max price for grid charging | $0.00-$0.50/kWh |

**For complete entity reference with all attributes:**
`docs/ENTITY_REFERENCE.md`

---

## Common Reads

```bash
# Is automation enabled?
... state get switch.localshift_automation_enabled
# Battery state
... state get sensor.localshift_battery_percent
# Integration health
... state get sensor.localshift_integration_status
# Recent decisions
... state get sensor.localshift_decision_log
# Which commit is running
... state get sensor.localshift_build
```

(`...` is the `HASS_SERVER=... HASS_TOKEN=... uvx --from homeassistant-cli hass-cli --output json` prefix above.)

---

## Best Practices

1. Use exact entity names; if unsure, search first with `entity list | grep`.
2. Use `--output json` and pipe to `jq` when you need a field.
3. Read only the exact data the task needs.
4. If the answer would need a write, report that instead of attempting it.
