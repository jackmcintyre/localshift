---
name: homeassistant-cli
description: Lightweight Home Assistant CLI for simple entity queries - use for read-only state/history lookups
license: MIT
compatibility: opencode
metadata:
  audience: developers
  workflow: debugging
---

## What I Do

Provide lightweight, low-context access to Home Assistant via `hass-cli`. Use this for simple read-only queries instead of the full MCP gateway.

## When to Use Me vs MCP

| Use CLI (this skill) | Use MCP (`@homeassistant`) |
|---------------------|---------------------------|
| Get entity state | Control devices (turn on/off) |
| List entities | Call services |
| Get history/statistics | Create/update automations |
| Quick lookups | Configuration changes |
| Read-only operations | Write operations |

**Rule of thumb:** If you just need to read data, use CLI. If you need to change anything or do complex operations, use MCP.

## Prerequisites

The CLI uses the same environment variables as MCP:
- `HASS_SERVER` - Home Assistant URL
- `HASS_TOKEN` - Long-lived access token

## Common Commands

### Entity Queries

```bash
# List all entities
uvx --from homeassistant-cli hass-cli entity list

# Get specific entity state
uvx --from homeassistant-cli hass-cli state get sensor.localshift_battery_percent

# Search entities (outputs JSON)
uvx --from homeassistant-cli hass-cli entity list | jq '.[] | select(.entity_id | contains("battery"))'
```

### History & Statistics

```bash
# Get entity history (last 24h)
uvx --from homeassistant-cli hass-cli raw GET "api/history/period?filter_entity_id=sensor.localshift_battery_percent"

# Get statistics
uvx --from homeassistant-cli hass-cli raw GET "api/statistics_during_period?entity_ids=sensor.localshift_battery_percent&period=day"
```

### Service Discovery

```bash
# List all available services
uvx --from homeassistant-cli hass-cli service list
```

## LocalShift-Specific Entities

Quick reference for common LocalShift entities:

| Entity | Description |
|--------|-------------|
| `sensor.localshift_battery_percent` | Battery state of charge |
| `sensor.localshift_current_mode` | Current operating mode |
| `sensor.localshift_grid_price` | Current grid price |
| `switch.localshift_automation_enabled` | Master automation toggle |
| `binary_sensor.localshift_price_spike_coming` | Price spike forecast |

## Tips

1. **Use `jq` for filtering** - CLI outputs JSON, pipe through `jq` for clean results
2. **Raw API access** - `hass-cli raw GET` gives direct API access for any endpoint
3. **Check state first** - Before using MCP, check current state via CLI to understand context
4. **Low context overhead** - CLI commands don't load 95+ tools into context

## Examples

### Quick battery check
```bash
uvx --from homeassistant-cli hass-cli state get sensor.localshift_battery_percent | jq '.state'
```

### Find all LocalShift entities
```bash
uvx --from homeassistant-cli hass-cli entity list | jq '.[] | select(.entity_id | contains("localshift")) | .entity_id'
```

### Get recent history for debugging
```bash
uvx --from homeassistant-cli hass-cli raw GET "api/history/period?filter_entity_id=sensor.localshift_decision_log&minimal_response"
```
