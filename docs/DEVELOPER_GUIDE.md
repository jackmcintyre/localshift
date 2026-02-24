# Developer Guide

This guide covers the technical architecture of the LocalShift integration for developers who want to understand, extend, or contribute to the codebase.

## Project Structure

```
custom_components/localshift/
├── __init__.py              # Integration entry point
├── coordinator.py            # Main coordinator, ties all modules together
├── coordinator_data.py      # Data structures (CoordinatorData, ChargingDecision)
├── const.py                 # Constants, enums, configuration defaults
├── config_flow.py           # Configuration flow (entity selection, options)
├── state_reader.py          # Reads external entities
├── computation_engine.py   # Computes derived values, determines active mode
├── state_machine.py         # Evaluates state machine, executes transitions
├── battery_controller.py    # Controls Powerwall via Teslemetry
├── cost_tracker.py          # Tracks energy costs
├── notification_service.py   # Sends notifications
├── weather_correlation.py   # Weather-based consumption prediction
├── sensor.py                # Sensor entities (11)
├── binary_sensor.py         # Binary sensor entities (11)
├── switch.py                # Switch entities (4)
├── number.py                # Number entities (7)
├── button.py                # Button entities (5)
├── manifest.json            # Home Assistant manifest
├── strings.json            # Localization strings
│
├── computation_engine_lib/ # Computation engine submodules
│   ├── forecast_computer.py  # 15-minute SOC forecasting
│   ├── history_fetcher.py    # Historical consumption data
│   ├── solar_utils.py       # Solar calculation utilities
│   └── utils.py             # General utilities
│
└── translations/
    └── en.json              # English translations
```

## Key Concepts

### Battery Modes

The system operates in one of these modes (defined in `const.py`):

| Mode | Operation Mode | Reserve | Description |
|------|---------------|---------|-------------|
| `SELF_CONSUMPTION` | self_consumption | 10% | Default — battery powers house |
| `GRID_CHARGING` | backup | 10% | Grid charging at 3.3kW |
| `BOOST_CHARGING` | autonomous | 100% | Fast charge at 5kW |
| `SPIKE_DISCHARGE` | autonomous | 10% | Export during price spikes |
| `PROACTIVE_EXPORT` | autonomous | dynamic | Export before negative FIT |
| `DEMAND_BLOCK` | self_consumption | 10% | Self-consumption enforced |
| `MANUAL` | — | — | Manual control (automation disabled) |

### State Machine Priority Chain

The state machine evaluates conditions in priority order (highest first):

```
1. Manual override?     → MANUAL (preserve user command)
2. Demand window?      → DEMAND_BLOCK (enforce self-consumption)
3. Price spike?        → SPIKE_DISCHARGE (export if enabled)
4. Manual button?      → Execute button action
5. Proactive export?  → PROACTIVE_EXPORT (export before negative)
6. Cheap price?       → GRID_CHARGING or BOOST_CHARGING
7. Default            → SELF_CONSUMPTION
```

### Debounce Timers

The state machine uses debounce timers to prevent rapid mode switching:

| Transition | Debounce |
|------------|----------|
| Spike discharge | 0 seconds (immediate) |
| Demand block | 0 seconds (immediate) |
| Manual override | 0 seconds (immediate) |
| Grid charging | 5 minutes |
| Self consumption | 5 minutes |

## Core Classes

### Coordinator (`coordinator.py`)

The `LocalShiftCoordinator` is the central hub:

```python
class LocalShiftCoordinator:
    """Main coordinator that ties all modules together."""

    def __init__(self, hass, entry):
        # Initialize all modules
        self._state_reader = StateReader(hass, entry, self.data)
        self._computation_engine = ComputationEngine(hass, entry, self.data)
        self._state_machine = StateMachine(battery_controller, notifications, ...)
        self._battery_controller = BatteryController(hass, self.data, ...)

        # Subscribe to entity changes
        self._state_reader.subscribe(self._on_external_change)

        # Set up 1-minute periodic tick
        async_track_time_interval(hass, self._on_tick, timedelta(minutes=1))

    async def _on_external_change(self, event):
        """Called when any monitored entity changes."""
        self._read_external_state()
        self._compute_derived_values()
        await self._evaluate_state_machine()

    async def _on_tick(self, now):
        """Called every minute."""
        self._read_external_state()
        self._compute_derived_values()
        await self._evaluate_state_machine()
```

**Responsibilities:**
- Subscribes to all external entity changes
- Runs 1-minute periodic tick
- Coordinates data flow between modules
- Manages configuration updates

### CoordinatorData (`coordinator_data.py`)

A dataclass holding all computed state:

```python
@dataclass
class CoordinatorData:
    """Snapshot of all computed data."""

    # Raw reads from external entities
    grid_power_kw: float
    battery_power_kw: float
    soc: float
    general_price: float
    feed_in_price: float
    # ... more raw values

    # Computed binary sensors
    demand_window_active: bool
    forecast_spike_within_window: bool
    solar_can_reach_target: bool
    # ... more computed bools

    # Computed sensors
    active_mode: BatteryMode
    effective_cheap_price: float
    solar_battery_forecast: dict
    # ... more computed values

    # Cost tracking
    grid_import_cost: float
    grid_export_revenue: float
    battery_savings: float
```

### StateReader (`state_reader.py`)

Reads values from Home Assistant entities:

```python
class StateReader:
    """Reads external entity state into CoordinatorData."""

    def read_all(self):
        """Read all monitored entities."""
        self.data.soc = self._get_entity_state(CONF_TESLEMETRY_SOC)
        self.data.general_price = self._get_entity_state(CONF_AMBER_GENERAL_PRICE)
        # ... reads all required entities
```

**Monitored Entities (14):**
- Teslemetry: operation_mode, backup_reserve, SOC, grid/battery/solar/load power
- Amber: general_price, feed_in_price, general/feed_in forecasts, price_spike
- Solcast: forecast_today, forecast_tomorrow

### ComputationEngine (`computation_engine.py`)

Computes all derived values:

```python
class ComputationEngine:
    """Computes derived values and determines active mode."""

    def compute_derived_values(self, data: CoordinatorData):
        """Main entry point for all computations."""

        # 1. Compute directional power
        data.grid_import_power_kw = max(data.grid_power_kw, 0)
        data.grid_export_power_kw = max(-data.grid_power_kw, 0)

        # 2. Compute demand window
        data.demand_window_active = self._compute_demand_window(data)

        # 3. Compute effective cheap price
        data.effective_cheap_price = self._compute_effective_cheap_price(data)

        # 4. Compute solar forecast
        self._compute_solar_forecast(data)

        # 5. Determine active mode
        data.active_mode = self._compute_active_mode(data)
```

**Key Methods:**
- `_compute_demand_window()`: Is current time within configured window?
- `_compute_effective_cheap_price()`: Dynamic price threshold with urgency
- `_compute_solar_forecast()`: SOC projection using Solcast data
- `_compute_active_mode()`: State machine priority evaluation
- `_compute_daily_15min_forecast()`: Full 24-hour simulation

### ForecastComputer (`forecast_computer.py`)

Simulates battery behavior over 24 hours:

```python
def compute_forecast(self, data, now_dt, historical_avg_kw, ...):
    """Compute 24-hour forecast with 15-minute granularity.

    Returns:
        daily_forecast: List of 96 dicts (one per 15-min slot)
        soc_15min: List of SOC percentages
    """
    daily_forecast = []
    predicted_soc = data.soc

    for slot_idx in range(96):  # 24 hours × 4 slots/hour
        slot_start = now_dt + timedelta(minutes=15 * slot_idx)

        # Get solar for this slot
        solar_kwh = self._get_solar_for_15min_slot(data, slot_start)

        # Get consumption estimate
        consumption_kwh = self._estimate_consumption(slot_start, ...)

        # Get price for this slot
        slot_price = self._get_price_for_slot(data.general_forecast, slot_start)

        # Simulate grid charging if needed
        should_charge, should_boost = self._should_grid_charge_at_slot(...)

        # Update predicted SOC
        predicted_soc += (solar_kwh - consumption_kwh) - charge_delta

        daily_forecast.append({
            "hour": slot_start.hour,
            "minute": slot_start.minute,
            "predicted_soc": predicted_soc,
            "solar_kwh": solar_kwh,
            "consumption_kwh": consumption_kwh,
            "grid_charge": should_charge,
            "grid_charge_boost": should_boost,
        })
```

### StateMachine (`state_machine.py`)

Evaluates and executes mode transitions:

```python
class BatteryStateMachine:
    """Manages battery mode state machine evaluation."""

    async def evaluate(self, data, computation_engine):
        """Compare desired mode with commanded mode, execute transitions."""

        desired = data.active_mode

        # Check if transition needed
        if desired == self._commanded_mode:
            return  # No change needed

        # Check debounce
        debounce = self.get_debounce_for_transition(
            self._commanded_mode, desired
        )

        if not self._has_elapsed(desired_since, debounce):
            return  # Still in debounce period

        # Execute transition
        await self._execute_mode_transition(data, desired)
        self._commanded_mode = desired
```

**Key Features:**
- Debounce timers prevent rapid switching
- Health check runs every minute to detect drift
- Manual override timeout (configurable)
- Transition success validation

### WeatherCorrelation (`weather_correlation.py`)

Learns and applies temperature-based consumption adjustments:

```python
class WeatherCorrelation:
    """Weather-aware consumption prediction using degree-day model."""

    async def async_initialize(self):
        """Load persisted coefficients from HA storage."""

    def learn_from_sample(self, hour: int, temperature: float, actual_load_kw: float):
        """Update coefficients based on observed temperature/load pair."""

    def predict_load(self, hour: int, temperature: float, base_load_kw: float) -> tuple[float, str]:
        """Apply learned coefficients to predict load given temperature."""

    def get_temperature_forecast(self) -> list[TemperatureForecast]:
        """Fetch forecasted temperatures from weather entity."""

    def get_diagnostics(self) -> dict[str, Any]:
        """Return learning statistics for diagnostics."""
```

**Key Features:**
- Degree-day model with separate cooling/heating coefficients per hour
- HA storage integration for persistence
- Confidence levels based on sample count (low/medium/high)
- Configurable cooling and heating thresholds

### HistoryFetcher (`history_fetcher.py`)

Fetches historical consumption data with day-of-week awareness:

```python
class HistoryFetcher:
    """Fetches and caches historical load data from HA statistics."""

    async def async_get_historical_hourly_averages(self, entity_id: str):
        """Get hourly averages, cached until midnight."""

    def get_profile_for_day(self, target_date: datetime) -> tuple[dict, dict, str]:
        """Get appropriate hourly profile based on target day's day-of-week."""

    def get_weekday_profile(self) -> tuple[dict[int, float], dict[int, int]]:
        """Get weekday profile for diagnostics."""

    def get_weekend_profile(self) -> tuple[dict[int, float], dict[int, int]]:
        """Get weekend profile for diagnostics."""
```

**Day-of-Week Aware Profiles:**
- Separates historical samples by day type (weekday: Mon-Fri, weekend: Sat-Sun)
- Calculates separate hourly averages for each profile
- Falls back to combined profile if insufficient samples
- Requires minimum 12 hours with 3+ samples each for day-specific profiles

### BatteryController (`battery_controller.py`)

Issues commands to Powerwall via Teslemetry:

```python
class BatteryController:
    """Controls Powerwall via Teslemetry."""

    async def set_force_charge(self, data, dry_run=False):
        """Start grid charging."""
        if dry_run:
            _LOGGER.info("[DRY RUN] Would set force charge")
            return True

        # Set allow_export to pv_only
        await self._set_allow_export("pv_only")

        # Wait 5 seconds for API to process
        await asyncio.sleep(5)

        # Set operation mode to backup
        await self._set_operation_mode("backup")

        await asyncio.sleep(5)

        # Set backup reserve to 10%
        await self._set_backup_reserve(10)

        return True

    async def set_force_discharge(self, data, dry_run=False):
        """Start discharging to export."""
        if dry_run:
            _LOGGER.info("[DRY RUN] Would set force discharge")
            return True

        # Set allow_export to battery_ok (CRITICAL for Powerwall 3)
        await self._set_allow_export("battery_ok")

        await asyncio.sleep(5)

        # Set operation mode to autonomous
        await self._set_operation_mode("autonomous")

        await asyncio.sleep(5)

        # Set backup reserve to 10%
        await self._set_backup_reserve(10)

        return True
```

**Important Notes:**
- 5-second delays between API calls prevent race conditions
- `allow_export=battery_ok` required for battery export (Powerwall 3)
- All methods support `dry_run` for testing

## Configuration Flow

### Entity Selection (Step 1-3)

The config flow has 3 steps:

1. **Teslemetry Entities**: Select Powerwall control entities
2. **Amber Electric Entities**: Select pricing and forecast entities
3. **Solcast Entities**: Select solar forecast entities

### Options Flow

After setup, users can configure all settings via **Configure**:

#### Notification Settings

| Option | Default | Description |
|--------|---------|-------------|
| Notify Service | (first available) | Notification service for alerts |

#### Demand Window Timing

| Option | Default | Description |
|--------|---------|-------------|
| Demand Window Start | 15:00 | Time when demand window begins (battery held for evening peak) |
| Demand Window End | 21:00 | Time when demand window ends (normal operation resumes) |
| Manual Override Timeout | 4 hours | Hours before manual mode automatically returns to self-consumption (0 = never) |

#### Price Thresholds

| Option | Range | Default | Description |
|--------|-------|---------|-------------|
| Cheap Price Percentile | 5-50% | 25% | Price percentile threshold for grid charging (e.g., 25 = charge when price is in bottom 25%) |
| Max Pre-charge Price | $0.00-0.50/kWh | $0.20/kWh | Maximum price to pay for pre-charging battery |
| Price Deadband | $0.00-0.10/kWh | $0.03/kWh | Minimum price difference to start/stop charging (prevents rapid cycling) |
| Spike Price Percentile | 50-95% | 75% | Price percentile for spike discharge (e.g., 75 = discharge at top 25% prices) |

#### Battery Settings

| Option | Range | Default | Description |
|--------|-------|---------|-------------|
| Battery Target | 50-100% | 100% | Target SOC % for demand window (battery reserved for evening peak) |
| Minimum Target SOC | 5-30% | 20% | Minimum SOC % maintained during discharge modes (spike, proactive export) |

#### Advanced Settings

| Option | Range | Default | Description |
|--------|-------|---------|-------------|
| Recent Load Weight | 0.0-1.0 | 0.67 | Weight given to recent load vs historical average (0.67 = 2/3 recent, 1/3 historical) |

**Note:** All options are stored in `entry.options` (not `entry.data`) so they can be changed without reconfiguring the entire integration. For backward compatibility, the coordinator checks `options` first, then falls back to `data` for existing entries.

## Learning System Internals

The learning system (Issue #170) provides adaptive parameter optimization. This section covers how to extend and debug it.

### Architecture Overview

The learning system consists of four main components:

| Component | File | Purpose |
|-----------|------|---------|
| `DecisionOutcomeTracker` | `decision_outcome_tracker.py` | Records mode transitions and backfills outcomes |
| `ParameterOptimizer` | `parameter_optimizer.py` | Adjusts parameters using Thompson sampling |
| `PatternAnalyzer` | `pattern_analyzer.py` | Detects systematic biases across dimensions |
| `OptimizationController` | `optimization_controller.py` | Real-time parameter evaluation |

### Adding New Optimizable Parameters

To add a new parameter that the learning system can adjust:

#### 1. Define the parameter in `const.py`

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class OptimizableParam:
    """Definition of a parameter that the learning system can adjust."""
    name: str
    default: float
    min_val: float
    max_val: float
    step: float
    description: str

OPTIMIZABLE_PARAMS: dict[str, OptimizableParam] = {
    # ... existing parameters ...
    "new_parameter": OptimizableParam(
        name="new_parameter",
        default=0.0,
        min_val=-10.0,
        max_val=10.0,
        step=1.0,
        description="Description of what this parameter does.",
    ),
}
```

#### 2. Apply the parameter in the decision engine

```python
# In grid_charge_decision.py, proactive_export.py, or forecast_computer.py

def _should_grid_charge_at_slot(self, ...):
    # Get the adaptive parameter
    new_param = self._adaptive_params.get("new_parameter", 0.0) if self._adaptive_params else 0.0
    
    # Apply it to the decision logic
    adjusted_value = base_value + new_param
```

#### 3. Update `ForecastComputer.set_adaptive_params()`

Ensure the new parameter is propagated to the relevant sub-engines:

```python
def set_adaptive_params(self, params: AdaptiveParameters | None) -> None:
    self._adaptive_params = params
    if self._grid_charge_decision:
        self._grid_charge_decision.set_adaptive_params(params)
    # Propagate to other engines as needed
```

### Testing the Feedback Loop

The learning system can be tested using the integration test framework:

```python
# tests/test_learning_integration.py

async def test_learning_improves_decisions():
    """Verify that learning improves decision quality over time."""
    # 1. Create mock coordinator with learning enabled
    # 2. Simulate 100 decisions with varying outcomes
    # 3. Verify parameter adjustments are made
    # 4. Verify later decisions have higher scores
```

### Debugging Learning Issues

**Parameter not changing:**
- Check `sensor.localshift_learning_status` — phase should be "tuning" or "optimizing"
- Verify 50+ decisions have been recorded (warm-up requirement)
- Check `switch.localshift_enable_learning` is ON

**Unexpected parameter values:**
- Review `sensor.localshift_decision_history` for recent decisions
- Check outcome scores — low scores trigger parameter adjustments
- Use `button.localshift_reset_learning` to start fresh

**Learning stuck in "observing":**
- Normal during first 2-3 days
- Verify decisions are being recorded in `decision_history`
- Check coordinator logs for decision tracking

### Storage Structure

Learning data is persisted in HA Storage under these keys:

```python
# Storage keys (scoped to entry_id)
f"localshift.decision_outcomes.{entry_id}"  # DecisionRecord list
f"localshift.param_optimizer.{entry_id}"    # ParameterOptimizer state
f"localshift.pattern_analysis.{entry_id}"   # PatternAnalyzer data
f"localshift.opt_controller.{entry_id}"     # OptimizationController weights
```

### Safety Mechanisms

The learning system has built-in safety rails:

| Mechanism | Implementation |
|-----------|----------------|
| Warm-up period | `min_observations = 50` in ParameterOptimizer |
| Step limits | Parameters move max 1 step per update |
| Bounds clamping | All values clamped to `min_val`/`max_val` |
| Rollback | 3 consecutive days of declining score triggers revert |

---

## Adding New Battery Modes

To add a new battery mode:

### 1. Add to BatteryMode enum (`const.py`)

```python
class BatteryMode(str, Enum):
    SELF_CONSUMPTION = "self_consumption"
    GRID_CHARGING = "grid_charging"
    BOOST_CHARGING = "boost_charging"
    SPIKE_DISCHARGE = "spike_discharge"
    DEMAND_BLOCK = "demand_block"
    MANUAL = "manual"
    NEW_MODE = "new_mode"  # Add here
```

### 2. Add to state machine priority (`computation_engine.py`)

```python
def _compute_active_mode(self, data):
    # ... existing conditions ...

    # Add new mode condition
    if self._new_condition(data):
        return BatteryMode.NEW_MODE

    return BatteryMode.SELF_CONSUMPTION
```

### 3. Add transition logic (`state_machine.py`)

```python
elif target == BatteryMode.NEW_MODE:
    transition_success = await self._battery_controller.set_new_mode(...)
```

### 4. Add controller method (`battery_controller.py`)

```python
async def set_new_mode(self, data, dry_run=False):
    """Implement new mode."""
    ...
```

### 5. Add expected state (`state_machine.py`)

```python
def _get_expected_state_for_mode(self, mode):
    if mode == BatteryMode.NEW_MODE:
        return ("operation_mode", reserve, "export_mode")
```

## Testing

### Manual Testing

1. **Dry Run Mode**: Enable `switch.localshift_dry_run` to test without affecting the battery.

2. **Check Logs**: Filter logs by `localshift` to see state machine decisions.

3. **Developer Tools**: Use Developer Tools → States to verify entity values.

### Test Scenarios

See `TEST_SCENARIOS.md` for comprehensive test cases covering:
- Deadband/hysteresis behavior
- Manual override protection
- Price spike discharge
- Cheap grid charging
- Demand window blocking

## Debugging Tips

### Common Issues

**Battery not charging:**
- Check `sensor.localshift_battery_mode` — should be `grid_charging` or `boost_charging`
- Check `binary_sensor.localshift_demand_window` — charging blocked during DW
- Check price — must be below `effective_cheap_price`

**Spike discharge not working:**
- Check `switch.localshift_spike_discharge_enabled` is ON
- Check time — spike discharge only allowed 6am-midnight
- Check `binary_sensor.localshift_price_spike_coming`

**Battery not exporting:**
- Check Powerwall 3 requires `allow_export` set to `battery_ok`
- Check `select.my_home_allow_export` entity

### Log Messages

Key log levels:
- `INFO`: Mode transitions, user actions
- `DEBUG`: Debounce timing, health checks
- `WARNING`: Hardware drift detected
- `ERROR`: Command failures

### Sensor Attributes

Many sensors have extra attributes for debugging:
- `effective_cheap_price.urgency`: Urgency factor (0-1)
- `solar_battery_forecast.hourly_forecast`: Detailed SOC projection
- `net_electricity_cost_today.*_cost`: Cost breakdown

## Code Style

The project follows these conventions:

- **Type hints**: All functions have type annotations
- **Docstrings**: Google-style docstrings for public methods
- **Logging**: Use `_LOGGER.info/debug/warning/error`
- **Constants**: All magic numbers in `const.py`
- **Async**: Use `async/await` throughout, no blocking calls

## Prerequisites for Development

1. **Home Assistant**: A running HA instance (dev or production)
2. **Python**: 3.11+ for type hints
3. **Editor**: VS Code with Pylance recommended

### Local Development

```bash
# Clone the repository
git clone https://github.com/jackmcintyre/ha-solar-battery-automation.git

# Copy to HA custom_components
cp -r custom_components/localshift ~/.config/homeassistant/custom_components/

# Restart HA to load changes
```

## Automated Deployment

When running Cline and Home Assistant on the same host (in different Docker containers), you can automate deployment when `main` is updated.

### Prerequisites

1. **Mount HA config into Cline container:**

   Add the HA config directory as a volume mount to your Cline/VSCode container:

   ```yaml
   # In docker-compose.yml for Cline container
   services:
     cline:
       volumes:
         - /mnt/user/appdata/Home-Assistant-Container:/homeassistant
   ```

   Or with Docker run:
   ```bash
   docker run ... -v /mnt/user/appdata/Home-Assistant-Container:/homeassistant ...
   ```

2. **Create a Home Assistant Long-Lived Access Token:**

   - Go to HA Profile → Security → Long-Lived Access Tokens
   - Create a token named "Cline Deploy"
   - Set as environment variable: `export HA_LONG_LIVED_TOKEN="your_token_here"`

### Deployment Script

The `deploy.sh` script handles deployment:

```bash
# Basic deployment (pulls latest main, copies files, reloads integration)
./deploy.sh

# Dry run to see what would happen
./deploy.sh --dry-run

# Deploy without reloading (if you want to restart HA manually)
./deploy.sh --no-reload
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HA_CONFIG` | `/homeassistant` | Path to HA config directory |
| `HA_URL` | `http://homeassistant:8123` | HA API URL |
| `HA_LONG_LIVED_TOKEN` | (none) | Token for API reload |

### Automatic Deployment on Pull

To automatically deploy when pulling changes, create a git post-merge hook:

```bash
# Create .git/hooks/post-merge
#!/bin/bash
./deploy.sh

# Make executable
chmod +x .git/hooks/post-merge
```

Now whenever you `git pull` on main, the integration will automatically deploy.

### What Gets Reloaded vs Requires Restart

| Change Type | Reload Works? | Restart Needed? |
|-------------|---------------|-----------------|
| Python code changes | ✅ Yes | No |
| `manifest.json` changes | ⚠️ Partial | Recommended |
| New dependencies | ❌ No | Yes |
| Config flow changes | ⚠️ Partial | Sometimes |

### Manual Deployment

If you prefer manual control:

```bash
# 1. Pull latest changes
git pull origin main

# 2. Copy to HA config
cp -r custom_components/localshift /homeassistant/custom_components/

# 3. Reload integration via HA UI
# Settings → Devices & Services → LocalShift → ⋮ → Reload
```

## References

- `docs/ARCHITECTURE.md` - System architecture diagrams
- `docs/ENTITY_REFERENCE.md` - Complete entity documentation
- `docs/FORECAST_DRIVEN_CONTROL.md` - Forecast design
- `docs/CHANGE_DETECTION.md` - Change detection design
- `TEST_SCENARIOS.md` - Test cases
