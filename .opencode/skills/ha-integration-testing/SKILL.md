---
name: ha-integration-testing
description: Test LocalShift integration in live Home Assistant environment
triggers:
  - "test in HA"
  - "verify integration"
  - "check entities in HA"
  - "test deployment"
  - "is it working in HA"
  - "verify services"
actions:
  - deploy_to_test
  - verify_entities
  - check_services
  - run_integration_tests
  - validate_dashboard
---

## What I Do

Test the LocalShift integration in a live Home Assistant environment. I verify that entities appear correctly, services work, state updates flow, and the integration functions properly after deployment.

## When to Use Me

- "Test this in Home Assistant"
- "Verify the integration works"
- "Are all entities showing up?"
- "Check if services are available"
- "Test after deployment"
- "Validate the dashboard"
- "Is the battery controller responding?"

## Prerequisites

1. Access to Home Assistant instance
2. LocalShift integration deployed (via `./deploy.sh`)
3. Teslemetry integration configured (for battery access)
4. Amber Electric configured (for pricing)
5. Solcast configured (for solar forecasts)

## Deployment Testing

### 1. Deploy and Verify

```bash
# Deploy to test environment
./deploy.sh --reserve
./deploy.sh

# Wait for integration to load
sleep 5
```

### 2. Entity Verification

Use MCP tools to verify entities:

```python
# Check all LocalShift entities exist
ha_search_entities(query="localshift")

# Verify key sensors
ha_get_states([
    "sensor.localshift_battery_percent",
    "sensor.localshift_current_mode", 
    "sensor.localshift_grid_price",
    "sensor.localshift_solar_forecast",
    "binary_sensor.localshift_price_spike_coming",
    "switch.localshift_automation_enabled"
])
```

### 3. Service Testing

```python
# Verify services are registered
ha_list_services(domain="localshift")

# Test mode change service
ha_call_service(
    domain="localshift",
    service="set_mode",
    data={"mode": "self_consumption"}
)
```

## Entity Validation Checklist

### Sensors (27 expected)

```python
# Verify all sensor entities
expected_sensors = [
    "sensor.localshift_battery_percent",
    "sensor.localshift_battery_power",
    "sensor.localshift_grid_power",
    "sensor.localshift_solar_power",
    "sensor.localshift_house_load",
    "sensor.localshift_current_mode",
    "sensor.localshift_grid_price",
    "sensor.localshift_forecast_price",
    "sensor.localshift_solar_forecast",
    "sensor.localshift_consumption_forecast",
    "sensor.localshift_remaining_solar_today",
    "sensor.localshift_remaining_consumption_today",
    "sensor.localshift_cost_saved_today",
    "sensor.localshift_grid_cost_today",
    "sensor.localshift_export_revenue_today",
    "sensor.localshift_total_saved",
    "sensor.localshift_decision_quality",
    "sensor.localshift_optimizer_status",
    "sensor.localshift_weather_correlation",
    "sensor.localshift_learning_progress",
    "sensor.localshift_next_price_change",
    "sensor.localshift_peak_price_today",
    "sensor.localshift_cheap_price_threshold",
    "sensor.localshift_discharge_hours_remaining",
    "sensor.localshift_charge_hours_remaining",
    "sensor.localshift_efficiency_factor",
    "sensor.localshift_version"
]

# Check each sensor
for entity_id in expected_sensors:
    state = ha_get_state(entity_id)
    if state:
        print(f"✅ {entity_id}: {state.state}")
    else:
        print(f"❌ {entity_id}: NOT FOUND")
```

### Binary Sensors (10 expected)

```python
expected_binary_sensors = [
    "binary_sensor.localshift_price_spike_coming",
    "binary_sensor.localshift_price_plunge_coming",
    "binary_sensor.localshift_should_charge",
    "binary_sensor.localshift_should_discharge",
    "binary_sensor.localshift_peak_solar_window",
    "binary_sensor.localshift_grid_exporting",
    "binary_sensor.localshift_grid_importing",
    "binary_sensor.localshift_battery_full",
    "binary_sensor.localshift_battery_empty",
    "binary_sensor.localshift_learning_active"
]
```

### Switches (8 expected)

```python
expected_switches = [
    "switch.localshift_automation_enabled",
    "switch.localshift_dry_run_mode",
    "switch.localshift_learning_enabled",
    "switch.localshift_notifications_enabled",
    "switch.localshift_peak_block_enabled",
    "switch.localshift_spike_discharge_enabled",
    "switch.localshift_proactive_export_enabled",
    "switch.localshift_weather_correlation_enabled"
]
```

## Integration Testing Scenarios

### Scenario 1: Mode Changes

```python
# Test switching between modes
def test_mode_changes():
    modes = [
        "self_consumption",
        "grid_charging",
        "boost_charging",
        "spike_discharge",
        "proactive_export",
        "demand_block",
        "manual"
    ]
    
    for mode in modes:
        # Set mode
        ha_call_service(
            "localshift",
            "set_mode",
            {"mode": mode}
        )
        
        # Wait for state update
        time.sleep(2)
        
        # Verify
        current = ha_get_state("sensor.localshift_current_mode")
        if current.state.upper() == mode.upper():
            print(f"✅ Mode {mode} set successfully")
        else:
            print(f"❌ Mode {mode} failed. Current: {current.state}")
```

### Scenario 2: Service Calls

```python
# Test service availability
services_to_test = [
    ("localshift", "set_mode"),
    ("localshift", "reset_learning"),
    ("localshift", "force_refresh"),
    ("localshift", "run_optimizer"),
    ("localshift", "export_data"),
    ("localshift", "import_config"),
]

for domain, service in services_to_test:
    available = ha_list_services(domain=domain)
    if any(s == f"{domain}.{service}" for s in available):
        print(f"✅ Service {domain}.{service} available")
    else:
        print(f"❌ Service {domain}.{service} NOT FOUND")
```

### Scenario 3: State Updates

```python
# Verify coordinator is updating
def test_coordinator_updates():
    # Get initial state
    before = ha_get_state("sensor.localshift_last_update")
    
    # Wait for update
    time.sleep(65)  # Coordinator updates every 60s
    
    # Check updated
    after = ha_get_state("sensor.localshift_last_update")
    
    if before.state != after.state:
        print("✅ Coordinator updating correctly")
    else:
        print("❌ Coordinator not updating")
```

## Dashboard Validation

### 1. Verify Dashboard Exists

```bash
# Check if LocalShift dashboard is configured
curl -s -H "Authorization: Bearer $HA_TOKEN" \
  http://$HA_HOST/api/config/dashboard \
  | jq '.[] | select(.url_path == "localshift")'
```

### 2. Validate Dashboard Cards

```python
# Check dashboard configuration
dashboard = ha_config_get_dashboard(url_path="localshift")

# Verify expected cards
cards_to_check = [
    "Current Mode",
    "Battery Status",
    "Grid Price",
    "Solar Forecast",
    "Cost Savings"
]

for card in dashboard.get("views", [{}])[0].get("cards", []):
    title = card.get("title", "")
    if title in cards_to_check:
        print(f"✅ Card found: {title}")
```

## Testing Workflow

### Complete Integration Test

```bash
#!/bin/bash
# test-integration.sh

set -e

echo "🧪 Starting LocalShift integration test..."

# 1. Deploy
echo "📤 Deploying..."
./deploy.sh --reserve
./deploy.sh

# 2. Wait for load
echo "⏳ Waiting for integration to load..."
sleep 5

# 3. Verify entities
echo "🔍 Verifying entities..."
# (Use Python script or MCP tools)

# 4. Test services
echo "🎛️  Testing services..."
# (Use Python script)

# 5. Check state updates
echo "🔄 Checking state updates..."
# (Use Python script)

# 6. Validate dashboard
echo "📊 Validating dashboard..."
# (Use Python script)

echo "✅ Integration test complete!"
```

## Error Diagnostics

### Common Issues

**1. Entities not appearing:**
```python
# Check integration status
ha_get_integration(query="localshift")

# Check logs
tail -100 /homeassistant/home-assistant.log | grep -i localshift
```

**2. Services not available:**
```python
# Verify integration loaded
ha_get_state("sensor.localshift_version")

# Check for errors
ha_get_logbook(hours_back=1, entity_id="sensor.localshift_version")
```

**3. State not updating:**
```python
# Check coordinator health
ha_get_state("binary_sensor.localshift_coordinator_ready")

# Check last update time
ha_get_state("sensor.localshift_last_update")
```

## Automated Testing with Watch Mode

Use the existing watch mode for continuous testing:

```bash
# Start watch mode
./deploy.sh --reserve
./deploy.sh --watch

# Every file change triggers:
# 1. Auto-deploy
# 2. Auto-reload HA
# 3. Integration reload

# Then verify:
# ha_get_state("sensor.localshift_version")
```

## Tips

1. **Always test after deployment** - Don't assume it works
2. **Check all entity types** - Sensors, binary sensors, switches, etc.
3. **Test services** - Ensure they respond correctly
4. **Monitor state updates** - Verify coordinator is running
5. **Use dry-run mode** - Test without affecting battery
6. **Check logs** - Look for errors and warnings
7. **Validate dashboard** - Ensure UI works correctly
8. **Test edge cases** - Empty data, errors, timeouts

## Quick Commands

```bash
# Deploy and test
./deploy.sh && sleep 5 && ha_search_entities(query="localshift")

# Check integration status
ha_get_integration(query="localshift")

# View recent logs
tail -50 /homeassistant/home-assistant.log | grep localshift

# Monitor entities
watch -n 5 'ha_get_states(["sensor.localshift_current_mode", "sensor.localshift_grid_price"])'

# Test service
ha_call_service("localshift", "force_refresh")
```
