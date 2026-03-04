---
name: ha-mcp-debug
description: Debug Home Assistant issues using MCP tools and direct log access
license: MIT
compatibility: opencode
metadata:
  audience: developers
  workflow: debugging
---

## What I Do

Help debug Home Assistant issues by combining MCP tools for real-time HA state with direct log file access for detailed error tracing.

## When to Use Me

- "Why isn't my LocalShift automation working?"
- "Check the HA logs for errors"
- "What's the current state of my battery?"
- "Debug why my sensor shows unavailable"

## MCP Tools for HA Debugging

### State & Entity Inspection

```python
# Get overview of HA system
ha_get_overview(detail_level="minimal")

# Check specific entity state
ha_get_state(entity_id="sensor.battery_percent")

# Check multiple entities at once
ha_get_states(["sensor.battery_percent", "sensor.grid_power"])

# Search for entities
ha_search_entities(query="battery", domain_filter="sensor")
```

### Automation Debugging

```python
# Get automation config
ha_config_get_automation("automation.battery_charging")

# Get automation traces (execution history)
ha_get_automation_traces("automation.battery_charging")

# Get detailed trace for specific run
ha_get_automation_traces("automation.battery_charging", run_id="1234567890.123")
```

### Logbook & History

```python
# Recent logbook entries
ha_get_logbook(hours_back=1, limit=50)

# Entity state history
ha_get_history(entity_ids="sensor.battery_percent", start_time="24h")

# Long-term statistics
ha_get_statistics(entity_ids="sensor.total_energy", start_time="30d", period="day")
```

## Direct Log Access

The Home Assistant logs are at:
```
/homeassistant/home-assistant.log
```

### Useful Log Commands

```bash
# Recent LocalShift logs
tail -100 /homeassistant/home-assistant.log | grep -i localshift

# Follow logs in real-time
tail -f /homeassistant/home-assistant.log | grep -i localshift

# Find errors
grep -i "error" /homeassistant/home-assistant.log | tail -50

# Search for specific issue
grep -i "timeout\|failed\|exception" /homeassistant/home-assistant.log | tail -50
```

## Common Debugging Workflows

### 1. Entity Not Updating

```python
# Check entity state
ha_get_state("sensor.my_sensor")

# Check if entity exists
ha_get_entity("sensor.my_sensor")

# Check integration status
ha_get_integration(query="localshift")
```

### 2. Automation Not Triggering

```python
# Check automation is enabled
ha_get_state("automation.my_automation")

# Get automation config
ha_config_get_automation("automation.my_automation")

# Check recent traces
ha_get_automation_traces("automation.my_automation")
```

### 3. Integration Errors

```python
# Check integration status
ha_get_integration(query="localshift")

# Check logs for errors
# Use bash: grep -i localshift /homeassistant/home-assistant.log | tail -50
```

### 4. Performance Issues

```python
# Check system health
ha_get_system_health()

# Check add-on stats
ha_get_addon(include_stats=True)
```

## LocalShift-Specific Debugging

### Check Coordinator Status

```python
# Get all LocalShift sensors
ha_search_entities(query="localshift", domain_filter="sensor")

# Check key sensors
ha_get_states([
    "sensor.localshift_battery_percent",
    "sensor.localshift_current_mode",
    "sensor.localshift_grid_price",
    "binary_sensor.localshift_price_spike_coming"
])
```

### Check Automation State

```python
# Check automation enabled
ha_get_state("switch.localshift_automation_enabled")

# Check recent mode changes
ha_get_history(entity_ids="sensor.localshift_current_mode", start_time="1h")

# Check traces
ha_get_automation_traces("automation.localshift_battery_control")
```

### Read Integration Logs

```bash
# Recent LocalShift logs
tail -100 /homeassistant/home-assistant.log | grep -i localshift

# Look for coordinator updates
grep "localshift.*coordinator" /homeassistant/home-assistant.log | tail -20

# Look for errors
grep -i "localshift.*error" /homeassistant/home-assistant.log | tail -20
```

## Tips

1. **Start with MCP tools** - They're faster and provide structured data
2. **Use logs for deep debugging** - When you need full error context
3. **Check traces** - For automation issues, traces show exactly what happened
4. **Use `ha_get_states()` for multiple entities** - More efficient than multiple `ha_get_state()` calls
5. **Combine approaches** - Use MCP tools + logs together for complete picture