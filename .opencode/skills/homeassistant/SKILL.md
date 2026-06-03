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

**You have access to:**
- `bash` tool - for hass-cli commands (read operations) - always available

**You MAY have access to (check your tool list):**
- `homeassistant_*` MCP tools - for write operations

**Before any write operation:** Look at the tools available to you. If `homeassistant_HassTurnOn` is NOT in that list, you CANNOT perform writes. Report this immediately - do NOT attempt the call.

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

## Fallback Protocol (CRITICAL)

### Read Operations

1. **Try CLI first** - Use `hass-cli` commands
2. **If CLI fails** - Immediately try MCP: `homeassistant_GetLiveContext()` and extract the entity from the response
3. **If both fail** - STOP. Do not try anything else. Report failure to user with the error details.

```
# Example fallback for getting entity state
# Step 1: CLI
result = bash("HASS_SERVER=... hass-cli state get sensor.battery")

# Step 2: If CLI failed, try MCP
if CLI_failed:
    context = homeassistant_GetLiveContext()
    # Extract entity from context.states
    
# Step 3: If MCP also failed, STOP and report
if MCP_failed:
    report("Failed to get entity state. CLI error: [X], MCP error: [Y]")
    DO NOT try any other approach
```

### Write Operations

**CRITICAL: MCP tools may not be available in your session. Check FIRST.**

1. **LOOK at your available tools** - Do you see `homeassistant_HassTurnOn` in the tool list provided to you?
2. **If NO** → STOP immediately. Output exactly:
   ```
   ❌ MCP tools not available. Enable Home Assistant MCP integration.
   
   Cannot perform write operation. Awaiting user guidance.
   ```
   Do NOT attempt to invoke any homeassistant_* tool - it will fail silently.
3. **If YES** → Proceed with MCP call
4. **Wait for actual result** - Do NOT report success until you see a success response
5. **DO NOT** attempt CLI workarounds for writes

```
# CORRECT workflow:
# 1. Check your tool list - is homeassistant_HassTurnOn there?
# 2. If missing: STOP, report error, wait for user
# 3. If present: Call the tool
# 4. Wait for response (not just <invoke> XML)
# 5. Report result based on actual response

# WRONG: Seeing <invoke> XML and reporting success
# That XML shows what you TRIED, not what HAPPENED
```

**DO NOT:**
- Try more than 2 approaches for reads
- Try any alternative for writes if MCP fails
- Guess or assume entity states
- Continue with stale/cached data
- Silently ignore failures
- Abort without reporting the specific error to the user
- Report success without seeing an actual success result
- Assume a tool invocation = success (wait for the response)

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

### Environment Bootstrapping (Critical)

Do not assume interactive shell exports are available to subprocesses. Source env explicitly.

```bash
# Recommended location (outside repo/worktree)
source ~/.config/localshift/ha.env

# Quick connectivity check before CLI reads
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $HA_LONG_LIVED_TOKEN" \
  "${HA_URL%/}/api/config"
```

If the check returns `000` or DNS errors, `HA_URL` is not reachable from current runtime.
Use a resolvable URL (for example, your LAN or public HA URL), then retry CLI commands.

### Entity Queries

```bash
# Get specific entity state (JSON)
source ~/.config/localshift/ha.env && \
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli --output json state get sensor.battery_level

# List all entities
source ~/.config/localshift/ha.env && \
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli entity list

# Search entities
source ~/.config/localshift/ha.env && \
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli entity list | grep -i battery
```

### History & Statistics

```bash
# Get entity history (last 24h, JSON)
source ~/.config/localshift/ha.env && \
HASS_SERVER="${HA_URL%/}" HASS_TOKEN=$HA_LONG_LIVED_TOKEN \
  uvx --from homeassistant-cli hass-cli --output json raw GET "api/history/period?filter_entity_id=sensor.battery_level"

# Get statistics
source ~/.config/localshift/ha.env && \
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
