---
name: deploy-and-validate
description: Use when deployment is complete and needs to be validated against HA before marking work done
---

# Deploy and Validate

## Overview

Deploy LocalShift changes to Home Assistant and validate the optimizer's business logic is working correctly. This skill ensures deployments are functional before releasing reservation.

## When to Use

- After implementing a feature or fix
- Before marking work as "done"
- When needing to validate optimizer behavior in live HA
- As final step before creating PR (per AGENTS.md workflow)

## Workflow

```
1. Check prerequisites → Reserve → Deploy (restart) → Wait for HA
2. Run business logic validation checklist (8 checks)
3. On failure: restore backup → release → report
4. On pass: release → report success
```

## Prerequisites

Ensure environment variables are set:
- `HA_CONFIG` - path to HA config (default: `/homeassistant`)
- `HA_LONG_LIVED_TOKEN` - HA API token for restart/reload
- `HA_URL` - HA URL (default: `http://homeassistant:8123`)

## Step 1: Check Prerequisites

```bash
# Verify HA connection
curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $HA_LONG_LIVED_TOKEN" "$HA_URL/api/config"

# Check existing reservations
./deploy.sh --status
```

If another agent has reservation, either wait or use `--force` (emergency only).

## Step 2: Reserve HA Instance

```bash
./deploy.sh --reserve
```

This creates reservation file preventing other agents from overwriting your deployment.

## Step 3: Deploy with Restart

```bash
./deploy.sh --restart
```

**Important:** Use `--restart` not plain deploy. The integration requires restart to load new code properly.

## Step 4: Wait for HA to Start

```bash
# Wait 30 seconds for initial startup
sleep 30

# Sync to next 5-minute mark (optimizer runs on price changes, aligns to 5-min intervals)
now_seconds=$(date +%-M*60+%-S)
target_seconds=$(( (now_seconds / 300 + 1) * 300 ))
sleep_seconds=$((target_seconds - now_seconds))
if [ $sleep_seconds -lt 30 ]; then
    sleep_seconds=$((sleep_seconds + 300))
fi
sleep $sleep_seconds
```

## Step 5: Validate Business Logic

**Important:** HomeAssistant MCP (ha-mcp) may not be available in all environments. Use direct API calls with environment passthrough if MCP fails.

```bash
# If ha-mcp unavailable, use this pattern:
export HA_URL && export HA_LONG_LIVED_TOKEN && python3 - <<'EOF'
import os, json, urllib.request

HA_URL = os.environ.get('HA_URL')
HA_TOKEN = os.environ.get('HA_LONG_LIVED_TOKEN')
headers = {'Authorization': f'Bearer {HA_TOKEN}', 'Content-Type': 'application/json'}

def get_state(entity_id):
    url = f"{HA_URL}api/states/{entity_id}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

# Run validation checks...
EOF
```

Query these entities and verify:

### Check 1: Optimizer Succeeded

```
Entity: sensor.localshift_optimizer_summary
Expected: state == "success"
Query: ha_get_state(entity_id="sensor.localshift_optimizer_summary")
```

Pass: `state` is "success" or "computed"
Fail: state is "error", "failed", or "disabled"

### Check 2: Integration Healthy

```
Entity: sensor.localshift_integration_status
Expected: state == "ok"
Query: ha_get_state(entity_id="sensor.localshift_integration_status")
```

Pass: `state` is "ok" or "degraded"
Fail: state is "error"

### Check 3: Solar Reach Correct

```
Entity: binary_sensor.localshift_solar_can_reach_target
Expected: value matches today's solar forecast reality
Query: ha_get_state(entity_id="binary_sensor.localshift_solar_can_reach_target")
```

Pass: state reflects actual solar conditions (on=can reach, off=cannot)
Fail: contradictory to forecast data

### Check 4: Plan Has 96 Slots

```
Entity: sensor.localshift_optimizer_plan
Expected: state is "96" (or close)
Query: ha_get_state(entity_id="sensor.localshift_optimizer_plan")
```

Pass: state shows 96 slots
Fail: incorrect slot count

### Check 5: Terminal Shortfall Reasonable

```
Entity: sensor.localshift_optimizer_summary
Attribute: terminal_shortfall_pct
Query: ha_get_state with attributes
```

Pass: matches expectation (0% for sunny, >0% for cloudy)
Fail: contradictory to solar conditions

### Check 6: No Grid Charge at Expensive Prices

```
Entity: sensor.localshift_optimizer_plan_detailed
Attribute: decisions array
Expected: no charge_grid_* actions at above-median buy_price
Query: ha_get_state with attributes, analyze decisions array
```

Pass: charging only at <= median prices
Fail: charging at expensive peak prices

### Check 7: No Export at Negative FIT

```
Entity: sensor.localshift_optimizer_plan_detailed
Attribute: decisions array
Expected: no export_proactive when sell_price <= 0
Query: ha_get_state with attributes
```

Pass: exports only when sell_price > 0
Fail: exporting at negative/no FIT prices

### Check 8: No Errors in Logs

```
Command: tail -50 /homeassistant/home-assistant.log | grep -i localshift | grep -i error
Expected: no error-level messages
```

Pass: clean logs
Fail: any localshift errors found

## Step 6: Handle Results

### On Failure

```bash
# Restore backup (deploy.sh keeps backups, restore manually if needed)
# Release reservation
./deploy.sh --release

# Report failure with details
echo "VALIDATION FAILED"
echo "- Check that failed: [1-8]"
echo "- Details: [specific failure reason]"
```

### On Success

```bash
# Release reservation
./deploy.sh --release

# Report success
echo "VALIDATION PASSED"
echo "- All 8 checks passed"
echo "- Deployment ready for PR"
```

## Quick Reference

| Check | Entity | Pass Criteria |
|-------|--------|---------------|
| 1 | sensor.localshift_optimizer_summary | state == "success" |
| 2 | sensor.localshift_integration_status | state != "error" |
| 3 | binary_sensor.localshift_solar_can_reach_target | matches forecast |
| 4 | sensor.localshift_optimizer_plan | 96 slots |
| 5 | sensor.localshift_optimizer_summary.terminal_shortfall_pct | reasonable |
| 6 | decisions array | no charge at peak prices |
| 7 | decisions array | no export at negative FIT |
| 8 | /homeassistant/home-assistant.log | no errors |

## Common Issues

### Optimizer shows "error"

- Check error_message attribute in optimizer_summary
- Verify all forecast data is available
- Check SOC entity is returning valid values

### Integration shows "error" status

- Check required entities are available
- Verify external integrations (Teslemetry, Amber, Solcast) are running

### Plan has fewer than 96 slots

- Forecast may not be fully populated
- Wait for next coordinator cycle

### Terminal shortfall incorrect

- May indicate solar forecast quality issue
- Check solcast data is loading properly