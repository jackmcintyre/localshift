---
name: homeassistant
description: Home Assistant gateway - CLI for reads, MCP for writes
license: MIT
compatibility: opencode
metadata:
  audience: developers
  workflow: debugging
---

## Tool Access - READ THIS FIRST

**You HAVE full access to:**
- `bash` tool - for hass-cli commands (read operations)
- All `homeassistant_*` MCP tools - for write operations

This is not conditional. Call tools directly when needed.

## Decision Matrix: CLI vs MCP

| Operation | Use | Command/Tool |
|-----------|-----|--------------|
| Get entity state | CLI | `hass-cli state get <entity>` |
| List/search entities | CLI | `hass-cli entity list` |
| Get history | CLI | `hass-cli raw GET api/history/...` |
| Get statistics | CLI | `hass-cli raw GET api/statistics/...` |
| Turn on/off device | MCP | `homeassistant_HassTurnOn/Off(name=...)` |
| Set temperature | MCP | `homeassistant_HassClimateSetTemperature(...)` |
| Set light brightness | MCP | `homeassistant_HassLightSet(name=..., brightness=%)` |
| Set volume | MCP | `homeassistant_HassSetVolume(name=..., volume_level=%)` |
| Call service | MCP | `homeassistant_HassTurnOn(name=...)` for most services |

**Rule:** CLI for reads (fast, low overhead). MCP for writes (state changes).

---

## CLI Commands (Preferred for Read Operations)

### Prerequisites

The CLI requires environment variables:
- `HASS_SERVER` - Home Assistant URL (no trailing slash)
- `HASS_TOKEN` - Long-lived access token

This project uses `HA_URL` and `HA_LONG_LIVED_TOKEN`. Wrap commands:
```bash
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN uvx --from homeassistant-cli hass-cli ...
```

### Entity Queries

```bash
# Get specific entity state (JSON)
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli --output json state get sensor.battery_level

# List all entities
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli entity list

# Search entities
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli entity list | grep -i battery
```

### History & Statistics

```bash
# Get entity history (last 24h, JSON)
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli --output json raw GET "api/history/period?filter_entity_id=sensor.battery_level"

# Get statistics
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli --output json raw GET "api/statistics_during_period?entity_ids=sensor.battery_level&period=day"
```

---

## MCP Tools (For Write Operations Only)

### State & Discovery

| Tool | Purpose |
|------|---------|
| `homeassistant_GetLiveContext()` | Returns all entity states and attributes |
| `homeassistant_GetDateTime()` | Returns current date/time from HA system |

### Device Control

| Tool | Purpose |
|------|---------|
| `homeassistant_HassTurnOn(name=...)` | Turn a device/entity on |
| `homeassistant_HassTurnOff(name=...)` | Turn a device/entity off |
| `homeassistant_HassLightSet(name=..., brightness=%)` | Set light brightness (0-100%) |
| `homeassistant_HassClimateSetTemperature(name=..., temperature=...)` | Set target temperature |
| `homeassistant_HassSetVolume(name=..., volume_level=%)` | Set absolute volume (0-100%) |

### Examples

```python
# Turn on a switch
homeassistant_HassTurnOn(name="switch.localshift_automation_enabled")

# Turn off a switch
homeassistant_HassTurnOff(name="switch.localshift_dry_run")

# Set light brightness
homeassistant_HassLightSet(name="light.kitchen", brightness=50)
```

---

## Direct Log Access

Home Assistant logs: `/homeassistant/home-assistant.log`

```bash
tail -100 /homeassistant/home-assistant.log | grep -i localshift
tail -f /homeassistant/home-assistant.log | grep -i localshift
grep -i "error\|exception\|failed" /homeassistant/home-assistant.log | tail -50
```

---

## LocalShift Entity Quick Reference

### Critical Control Entities

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
`/config/home/localshift/docs/ENTITY_REFERENCE.md`

---

## Common Workflows

### 1. Check if automation is enabled

```bash
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli --output json state get switch.localshift_automation_enabled
```

### 2. Get battery state

```bash
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli --output json state get sensor.localshift_battery_percent
```

### 3. Toggle automation (MCP - requires confirmation)

Ask user first, then:
```python
homeassistant_HassTurnOn(name="switch.localshift_automation_enabled")
# or
homeassistant_HassTurnOff(name="switch.localshift_automation_enabled")
```

### 4. Check integration health

```bash
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli --output json state get sensor.localshift_integration_status
```

### 5. Get recent decisions

```bash
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli --output json state get sensor.localshift_decision_log
```

---

## Gatekeeper Policy

- **Read operations:** Execute immediately via CLI
- **Write operations:** Ask for user confirmation before executing via MCP
- **Least privilege:** Only perform the exact action requested

---

## Best Practices

1. **CLI first for reads** - Lower overhead, faster response
2. **MCP only for writes** - State changes need confirmation
3. **Use exact entity names** - If uncertain, search first with `hass-cli entity list | grep`
4. **Check logs for errors** - `/homeassistant/home-assistant.log`
5. **Use `--output json`** - Machine-parseable, pipe to `jq` if needed
