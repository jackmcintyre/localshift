---
name: homeassistant
description: Debug Home Assistant issues using native tools and direct log access
license: MIT
compatibility: opencode
metadata:
  audience: developers
  workflow: debugging
---

## How to Access

⚠️ **ALWAYS DELEGATE to the `homeassistant` subagent.** The main agent does NOT have HA tools enabled (by design, to avoid MCP token overhead).

```python
# ALWAYS use task() with subagent_type="homeassistant"
task(
    subagent_type="homeassistant",
    prompt="Check the current state of switch.localshift_automation_enabled",
    run_in_background=False
)
```

The `homeassistant` subagent has exclusive access to all HA MCP tools. Never attempt to call `homeassistant_*` tools or `skill_mcp` directly from the main agent.

## What I Do

Help debug Home Assistant issues by using native Home Assistant tools and direct log file access for real-time state inspection and error tracing.

## When to Use Me

- "Why isn't my LocalShift automation working?"
- "Check the HA logs for errors"
- "What's the current state of my battery?"
- "Debug why my sensor shows unavailable"

## Native Home Assistant Tools (Subagent Only)

These tools are available ONLY within the `homeassistant` subagent. The main agent must delegate to use them.

### State & Entity Inspection

```python
# Get overview of HA system (returns all entities)
homeassistant_GetLiveContext()

# Turn devices on/off
homeassistant_HassTurnOn(name="switch.localshift_automation_enabled")
homeassistant_HassTurnOff(name="switch.localshift_automation_enabled")

# Set light brightness
homeassistant_HassLightSet(name="light.kitchen_downlights", brightness=50)

# Set fan speed
homeassistant_HassFanSetSpeed(name="fan.ali_s_fan", percentage=42)

# Set climate temperature
homeassistant_HassClimateSetTemperature(name="climate.living_room_air_conditioner", temperature=22)

# Control media players
homeassistant_HassMediaPause(name="media_player.wiim_amp")
homeassistant_HassMediaUnpause(name="media_player.wiim_amp")
homeassistant_HassSetVolume(name="media_player.wiim_amp", volume_level=50)
```

### Getting Current Date/Time

```python
homeassistant_GetDateTime()
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

# Check automation traces
grep -i "automation" /homeassistant/home-assistant.log | tail -30
```

## Common Debugging Workflows

### 1. Entity Not Updating

```python
# Check entity state via GetLiveContext (returns all states)
# Then parse the result for your specific entity

# Alternative: Check logs for the entity
grep -i "sensor.my_sensor" /homeassistant/home-assistant.log | tail -20
```

### 2. Check if Automation is Enabled

```python
# Check LocalShift automation status
homeassistant_GetLiveContext()
# Then look for: switch.localshift_automation_enabled in the output
```

### 3. Integration Errors

```bash
# Check logs for LocalShift errors
grep -i "localshift.*error" /homeassistant/home-assistant.log | tail -20

# Look for coordinator updates
grep "localshift.*coordinator" /homeassistant/home-assistant.log | tail -20

# Check for warnings
grep -i "localshift.*warning" /homeassistant/home-assistant.log | tail -20
```

### 4. Performance Issues

```bash
# Check for slow updates
grep -i "took.*seconds\|slow" /homeassistant/home-assistant.log | tail -20

# Check database warnings
grep -i "database\|recorder" /homeassistant/home-assistant.log | tail -20
```

## LocalShift-Specific Debugging

### Check Key Switches Status

Use `homeassistant_GetLiveContext()` and look for:
- `switch.localshift_automation_enabled` — Master toggle
- `switch.localshift_dry_run` — Dry run mode (off = live)
- `switch.localshift_spike_discharge_enabled` — Spike discharge
- `switch.localshift_enable_learning` — Learning system
- `switch.localshift_notifications_enabled` — Notifications

### Check Key Sensors

From `homeassistant_GetLiveContext()`, look for:
- `sensor.localshift_battery_percent` — Battery SOC
- `sensor.localshift_current_mode` — Current mode
- `sensor.localshift_grid_price` — Grid price
- `sensor.localshift_decision_log` — Recent decisions
- `binary_sensor.localshift_price_spike_coming` — Spike forecast
- `binary_sensor.localshift_demand_window` — In demand window

### Read Integration Logs

```bash
# Recent LocalShift logs
tail -100 /homeassistant/home-assistant.log | grep -i localshift

# Look for coordinator updates
grep "localshift.*coordinator" /homeassistant/home-assistant.log | tail -20

# Look for errors
grep -i "localshift.*error" /homeassistant/home-assistant.log | tail -20

# Check optimizer decisions
grep "localshift.*optimizer" /homeassistant/home-assistant.log | tail -20

# Check state transitions
grep "localshift.*transition\|localshift.*mode" /homeassistant/home-assistant.log | tail -20
```

## Tips

1. **Start with GetLiveContext()** — Returns all entity states at once
2. **Use logs for deep debugging** — When you need full error context
3. **Check decision_log sensor** — Shows mode change history with reasons
4. **Combine approaches** — Live context + logs together for complete picture
5. **Use bash for log filtering** — More efficient than reading entire log files
