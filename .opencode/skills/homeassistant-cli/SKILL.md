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

The CLI requires these environment variables:
- `HASS_SERVER` - Home Assistant URL (no trailing slash)
- `HASS_TOKEN` - Long-lived access token

**Note:** This project uses `HA_URL` and `HA_LONG_LIVED_TOKEN`. Wrap commands like:
```bash
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN uvx --from homeassistant-cli hass-cli ...
```

## Common Commands

### Entity Queries

```bash
# List all entities (table format)
uvx --from homeassistant-cli hass-cli entity list

# Get specific entity state (JSON format)
uvx --from homeassistant-cli hass-cli --output json state get sensor.battery_level

# Search entities (filter with grep)
uvx --from homeassistant-cli hass-cli entity list | grep -i battery
```

### History & Statistics

```bash
# Get entity history (last 24h, JSON)
uvx --from homeassistant-cli hass-cli --output json raw GET "api/history/period?filter_entity_id=sensor.battery_level"

# Get statistics
uvx --from homeassistant-cli hass-cli --output json raw GET "api/statistics_during_period?entity_ids=sensor.battery_level&period=day"
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

1. **Use `--output json`** - Add this flag for machine-parseable output, then pipe to `jq`
2. **Strip trailing slash** - `${HA_URL%/}` removes trailing slash to avoid double-slash errors
3. **Raw API access** - `hass-cli raw GET` gives direct API access for any endpoint
4. **Low context overhead** - CLI commands don't load 95+ tools into context

## Examples

All examples assume env vars are set inline. In practice, export them first or use the wrapper pattern.

### Quick battery check
```bash
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli --output json state get sensor.battery_level | jq '.[0].state'
```

### Find all battery entities
```bash
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli entity list | grep -i battery
```

### Get recent history for debugging
```bash
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli raw GET "api/history/period?filter_entity_id=sensor.my_entity&minimal_response"
```
