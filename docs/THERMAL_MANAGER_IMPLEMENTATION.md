# Implementation Plan: Thermal Manager

[Overview]
Implement an optional thermal management module that provides HVAC-aware load forecasting, daily thermal mode determination, and automated climate control for the LocalShift integration.

This implementation addresses issue #137 (chicken-and-egg feedback loop in load forecasting) by separating HVAC load from baseline consumption, and issue #63 (proactive thermal management) by adding pre-conditioning and solar tapering capabilities. The system determines a daily thermal mode (HEAT/COOL/DRY/OFF) based on weather forecast, monitors all configured climate entities for load correlation, and optionally controls a subset for automated thermal management.

[Types]

## ThermalMode (Enum)
```python
class ThermalMode(StrEnum):
    """Daily thermal operating mode."""
    OFF = "off"      # No thermal automation needed (mild day)
    COOL = "cool"    # Cooling mode (hot day)
    HEAT = "heat"    # Heating mode (cold day)
    DRY = "dry"      # Dehumidification mode (humid day)
```

## ClimateEntityState (Dataclass)
```python
@dataclass
class ClimateEntityState:
    """Snapshot of a climate entity's current state."""
    entity_id: str
    state: str  # "off", "cool", "heat", "dry", "auto"
    hvac_action: str  # "off", "cooling", "heating", "drying", "idle"
    setpoint: float  # Target temperature in °C
    current_temperature: float | None  # Current room temperature
    is_controlled: bool  # Whether this entity is in the control subset
```

## LearnedHVACPower (Dataclass)
```python
@dataclass
class LearnedHVACPower:
    """Learned power consumption for a climate entity."""
    entity_id: str
    cooling_power_kw: float = 0.0  # kW when cooling
    heating_power_kw: float = 0.0  # kW when heating  
    drying_power_kw: float = 0.0   # kW when drying
    sample_count: int = 0
    confidence: str = "low"  # "low", "medium", "high"
```

## ThermalManagerData (Dataclass)
```python
@dataclass
class ThermalManagerData:
    """Complete thermal manager state for CoordinatorData."""
    # Daily mode (set once per day at decision time)
    daily_thermal_mode: ThermalMode = ThermalMode.OFF
    daily_mode_locked: bool = False
    daily_mode_determined_at: datetime | None = None
    
    # Mode determination thresholds (configurable)
    cooling_trigger_temp: float = 28.0  # °C
    heating_trigger_temp: float = 15.0  # °C
    dehumidify_trigger_humidity: float = 70.0  # %
    
    # Current climate states
    climate_states: dict[str, ClimateEntityState] = field(default_factory=dict)
    
    # Learned power consumption
    learned_hvac_power: dict[str, LearnedHVACPower] = field(default_factory=dict)
    
    # Separated load profiles
    baseline_load_kw: dict[int, float] = field(default_factory=dict)  # Non-HVAC load by hour
    hvac_load_kw: dict[int, float] = field(default_factory=dict)  # HVAC load by hour
    
    # Control signals
    preconditioning_active: bool = False
    solar_taper_active: bool = False
    taper_setpoint_offset: float = 0.0  # Degrees to adjust setpoint
```

[Files]

## New Files to Create

### `/custom_components/localshift/thermal_manager.py`
Main thermal management class. Handles:
- Daily mode determination from weather forecast (HEAT > COOL > DRY priority)
- Climate entity state monitoring
- HVAC power learning from state changes
- Load profile separation (baseline vs HVAC)
- Pre-conditioning logic
- Solar tapering logic

### `/tests/test_thermal_manager.py`
Unit tests for thermal manager functionality.

## Files to Modify

### `/custom_components/localshift/const.py`
Add new configuration keys and defaults:
```python
# Climate entity configuration
CONF_CLIMATE_ENTITIES = "climate_entities"  # All climate entities (monitored)
CONF_CLIMATE_CONTROL_ENTITIES = "climate_control_entities"  # Subset to control

# Thermal management switches
CONF_THERMAL_MANAGEMENT_ENABLED = "thermal_management_enabled"
CONF_SOLAR_TAPER_ENABLED = "solar_taper_enabled"

# Mode determination thresholds
CONF_COOLING_TRIGGER_TEMP = "cooling_trigger_temp"
CONF_HEATING_TRIGGER_TEMP = "heating_trigger_temp"
CONF_DEHUMIDIFY_TRIGGER_HUMIDITY = "dehumidify_trigger_humidity"
CONF_THERMAL_MODE_DECISION_TIME = "thermal_mode_decision_time"

# Pre-conditioning settings
CONF_PRECONDITION_HOURS_BEFORE_DW = "precondition_hours_before_dw"
CONF_PRECONDITION_TEMP_OFFSET = "precondition_temp_offset"

# Solar tapering settings
CONF_TAPER_MAX_SETPOINT_OFFSET = "taper_max_setpoint_offset"

# Defaults
DEFAULT_THERMAL_MANAGEMENT_ENABLED = False
DEFAULT_SOLAR_TAPER_ENABLED = True
DEFAULT_COOLING_TRIGGER_TEMP = 28.0
DEFAULT_HEATING_TRIGGER_TEMP = 15.0
DEFAULT_DEHUMIDIFY_TRIGGER_HUMIDITY = 70.0
DEFAULT_THERMAL_MODE_DECISION_TIME = "06:00"
DEFAULT_PRECONDITION_HOURS_BEFORE_DW = 1.0
DEFAULT_PRECONDITION_TEMP_OFFSET = 2.0
DEFAULT_TAPER_MAX_SETPOINT_OFFSET = 3.0
```

### `/custom_components/localshift/config_flow.py`
Add new configuration step for thermal management:
- Multi-select for climate_entities (all entities to monitor)
- Multi-select for climate_control_entities (subset to control)
- Threshold sliders for mode determination
- Pre-conditioning settings
- Solar tapering settings

### `/custom_components/localshift/coordinator_data.py`
Add thermal manager fields to CoordinatorData:
```python
# Thermal manager fields (Issue #137, #63)
thermal_management_enabled: bool = False
daily_thermal_mode: str = "off"
daily_mode_locked: bool = False
daily_mode_determined_at: str = ""
climate_states: dict[str, Any] = field(default_factory=dict)
learned_hvac_power: dict[str, Any] = field(default_factory=dict)
baseline_load_kw: dict[int, float] = field(default_factory=dict)
hvac_load_kw: dict[int, float] = field(default_factory=dict)
preconditioning_active: bool = False
solar_taper_active: bool = False
taper_setpoint_offset: float = 0.0
```

### `/custom_components/localshift/coordinator.py`
Initialize thermal manager and integrate with existing flow:
- Create ThermalManager instance in `async_start()`
- Add climate entities to monitored entities list
- Call thermal manager methods in periodic tick
- Wire up daily mode determination at decision time

### `/custom_components/localshift/state_reader.py`
Add climate entity state reading:
- `_read_climate_states()` method
- Integrate with `read_all_external_state()`

### `/custom_components/localshift/computation_engine_lib/history_fetcher.py`
Add HVAC-aware load separation:
- `_separate_hvac_load()` method
- Use 25th percentile for baseline filtering
- Track HVAC vs non-HVAC samples separately

### `/custom_components/localshift/computation_engine_lib/forecast_computer.py`
Use separated load profiles:
- `_estimate_hourly_consumption_kw()` uses baseline only for grid charging decisions
- Add HVAC load prediction based on weather forecast
- Weather-adjusted total = baseline + predicted_hvac

### `/custom_components/localshift/sensor.py`
Add new thermal sensors:
- `DailyThermalModeSensor` - Current daily mode
- `BaselineLoadProfileSensor` - Baseline consumption by hour
- `HVACLoadProfileSensor` - HVAC consumption by hour

### `/custom_components/localshift/binary_sensor.py`
Add new thermal binary sensors:
- `ThermalManagementEnabledSensor` - Is thermal management active
- `PreconditioningActiveSensor` - Is pre-conditioning running
- `SolarTaperActiveSensor` - Is solar tapering active

### `/custom_components/localshift/switch.py`
Add new thermal switches:
- `thermal_management_enabled` - Master switch
- `solar_taper_enabled` - Enable/disable solar tapering

### `/custom_components/localshift/number.py`
Add new thermal number entities:
- `cooling_trigger_temp` - Temperature threshold for COOL mode
- `heating_trigger_temp` - Temperature threshold for HEAT mode
- `dehumidify_trigger_humidity` - Humidity threshold for DRY mode
- `precondition_temp_offset` - Degrees to pre-condition

[Functions]

## New Functions

### `thermal_manager.py`

#### `ThermalManager.__init__(self, hass: HomeAssistant, entry: ConfigEntry, get_entity_id_func: Callable, get_switch_state_func: Callable)`
Initialize thermal manager with HA instance and config.

#### `async def async_initialize(self) -> None`
Load persisted learned HVAC power data from storage.

#### `async def async_determine_daily_mode(self, weather_forecast: list[TemperatureForecast]) -> ThermalMode`
Determine today's thermal mode based on weather forecast.
Priority: HEAT > COOL > DRY (per user requirement).

Logic:
```python
def async_determine_daily_mode(self, forecasts):
    max_temp = max(f.temperature for f in forecasts)
    min_temp = min(f.temperature for f in forecasts)
    avg_humidity = sum(f.humidity for f in forecasts) / len(forecasts)
    
    # Priority: HEAT > COOL > DRY
    if min_temp < self.heating_trigger_temp:
        return ThermalMode.HEAT
    elif max_temp > self.cooling_trigger_temp:
        return ThermalMode.COOL
    elif avg_humidity > self.dehumidify_trigger_humidity:
        return ThermalMode.DRY
    else:
        return ThermalMode.OFF
```

#### `def read_climate_states(self, data: CoordinatorData) -> None`
Read current state of all configured climate entities.

#### `async def async_learn_hvac_power(self, data: CoordinatorData, prev_climate_states: dict) -> None`
Learn power consumption when climate state changes by observing load delta.

Logic:
```python
# When AC turns from off to cooling:
# - Observe load before: 0.8 kW
# - Observe load after: 3.2 kW
# - Learned cooling_power = 3.2 - 0.8 = 2.4 kW
```

#### `def separate_load_samples(self, samples: list[dict], climate_states: dict) -> tuple[dict[int, list], dict[int, list]]`
Separate historical load samples into HVAC and non-HVAC buckets.

#### `def calculate_baseline_profile(self, non_hvac_samples: dict[int, list]) -> dict[int, float]`
Calculate baseline load profile using 25th percentile of non-HVAC samples.
This filters out discretionary load spikes (dishwasher, EV, etc.).

#### `def predict_hvac_load(self, hour: int, temperature: float, humidity: float) -> float`
Predict HVAC load for given hour based on weather and learned power.

#### `async def async_evaluate_preconditioning(self, data: CoordinatorData) -> bool`
Determine if pre-conditioning should be active.

Conditions:
- Within X hours before demand window
- Daily mode is HEAT or COOL
- Battery SOC sufficient or solar available

#### `async def async_evaluate_solar_taper(self, data: CoordinatorData) -> tuple[bool, float]`
Determine solar tapering status and setpoint offset.

Logic:
```python
# If excess_solar_available and load_shift_signal == INCREASE_LOAD:
# - Calculate how much setpoint to adjust
# - excess_kw = 2.0 kW -> adjust setpoint by -1.5°C (cooler)
# - This consumes excess by running AC harder
```

#### `async def async_apply_climate_control(self, data: CoordinatorData) -> None`
Apply setpoint adjustments to controlled climate entities.

## Modified Functions

### `history_fetcher.py: _calculate_profiles()`
**Current:** Calculates average load per hour from all samples.
**Change:** Add step to separate HVAC/non-HVAC samples before averaging. Use 25th percentile for baseline.

### `forecast_computer.py: _estimate_hourly_consumption_kw()`
**Current:** Uses historical_avg_kw directly for consumption prediction.
**Change:** Use baseline_load_kw + predicted_hvac_load. Only baseline is used for grid charging decisions.

### `forecast_computer.py: _should_grid_charge_at_slot()`
**Current:** Uses total predicted consumption.
**Change:** Use baseline consumption only for grid charging decisions. This solves the #137 feedback loop.

### `coordinator.py: async_start()`
**Current:** Initializes computation engine, state machine, etc.
**Change:** Also initialize ThermalManager, add climate entities to monitored list.

### `coordinator.py: _handle_periodic_tick()`
**Current:** Runs cost accumulation, weather learning, etc.
**Change:** Also call thermal manager periodic methods (learn, evaluate, apply).

[Classes]

## New Classes

### `ThermalManager`
**File:** `/custom_components/localshift/thermal_manager.py`
**Key Methods:**
- `async_initialize()` - Load persisted data
- `async_determine_daily_mode()` - Morning mode decision
- `read_climate_states()` - Read current states
- `async_learn_hvac_power()` - Learn from state changes
- `separate_load_samples()` - HVAC/baseline separation
- `calculate_baseline_profile()` - 25th percentile baseline
- `predict_hvac_load()` - Weather-based prediction
- `async_evaluate_preconditioning()` - Pre-conditioning logic
- `async_evaluate_solar_taper()` - Solar tapering logic
- `async_apply_climate_control()` - Apply setpoint changes

**Inheritance:** None
**Dependencies:**
- HomeAssistant instance
- ConfigEntry
- WeatherCorrelation (for forecast data)
- Storage (for learned power persistence)

## Modified Classes

### `CoordinatorData`
**File:** `/custom_components/localshift/coordinator_data.py`
**Changes:** Add thermal manager fields (see Types section)

### `LocalShiftCoordinator`
**File:** `/custom_components/localshift/coordinator.py`
**Changes:**
- Add `_thermal_manager: ThermalManager | None` attribute
- Initialize in `async_start()`
- Call methods in `_handle_periodic_tick()`
- Add daily mode determination timer

### `HistoryFetcher`
**File:** `/custom_components/localshift/computation_engine_lib/history_fetcher.py`
**Changes:**
- Add `_separate_hvac_load()` method
- Modify `_calculate_profiles()` to use HVAC-aware separation

### `ForecastComputer`
**File:** `/custom_components/localshift/computation_engine_lib/forecast_computer.py`
**Changes:**
- Add `predict_hvac_load()` method
- Modify `_estimate_hourly_consumption_kw()` to use baseline + hvac
- Modify `_should_grid_charge_at_slot()` to use baseline only

[Dependencies]

## No New External Dependencies
All functionality uses existing Home Assistant APIs:
- `climate` domain for entity control
- `weather` domain for forecast data
- `homeassistant.helpers.storage.Store` for persistence

## Internal Dependencies
- `WeatherCorrelation` - Extends for humidity data and HVAC prediction
- `CoordinatorData` - New fields for thermal state
- `ComputationEngine` - Integration for load forecasting

[Testing]

## Unit Tests Required

### `/tests/test_thermal_manager.py`

```python
class TestThermalManager:
    """Tests for ThermalManager class."""
    
    def test_determine_daily_mode_heat_priority(self):
        """HEAT mode takes priority over COOL and DRY."""
        # min_temp < 15°C -> HEAT, even if hot and humid
        
    def test_determine_daily_mode_cool(self):
        """COOL mode when max_temp > threshold, no heating needed."""
        # max_temp > 28°C, min_temp > 15°C -> COOL
        
    def test_determine_daily_mode_dry(self):
        """DRY mode when humid, no heating/cooling needed."""
        # humidity > 70%, mild temps -> DRY
        
    def test_determine_daily_mode_off(self):
        """OFF mode on mild days."""
        # All conditions within thresholds -> OFF
        
    def test_daily_mode_locked(self):
        """Mode cannot change during the day after decision time."""
        
    def test_learn_hvac_power_cooling(self):
        """Learning cooling power from state change."""
        # AC off -> cooling, observe load delta
        
    def test_learn_hvac_power_heating(self):
        """Learning heating power from state change."""
        
    def test_baseline_uses_25th_percentile(self):
        """Baseline filtering removes discretionary spikes."""
        # Samples: [0.5, 0.6, 0.8, 2.5, 3.0] -> baseline ~0.6
        
    def test_predict_hvac_load(self):
        """HVAC load prediction from weather."""
        
    def test_preconditioning_before_dw(self):
        """Pre-conditioning activates before demand window."""
        
    def test_solar_taper_excess(self):
        """Solar tapering adjusts setpoint with excess solar."""
```

### Modified Test Files

#### `/tests/test_forecast_computer.py`
Add tests for HVAC-aware load forecasting:
- `test_baseline_only_for_grid_charging()`
- `test_hvac_prediction_included_in_total()`

#### `/tests/test_history_fetcher.py`
Add tests for HVAC/non-HVAC separation:
- `test_separate_hvac_samples()`
- `test_baseline_percentile_filtering()`

[Implementation Order]

## Phase 1: Climate Monitoring Infrastructure (Issue #139)
**Goal:** Basic climate entity configuration and state monitoring.

1. Add configuration keys to `const.py`
2. Add climate entity multi-select to `config_flow.py`
3. Add `_read_climate_states()` to `state_reader.py`
4. Add thermal fields to `coordinator_data.py`
5. Wire up state reading in `coordinator.py`

**Deliverable:** Climate entities configured and states visible in CoordinatorData.

## Phase 2: Load Correlation (Issue #137)
**Goal:** Separate HVAC load from baseline for accurate forecasting.

1. Create `thermal_manager.py` with HVAC power learning
2. Modify `history_fetcher.py` for HVAC-aware separation
3. Use 25th percentile for baseline filtering
4. Modify `forecast_computer.py` to use separated loads
5. Grid charging decisions use baseline only

**Deliverable:** No feedback loop; grid charging based on baseline consumption only.

## Phase 3: Daily Mode Determination (Issue #140)
**Goal:** Determine daily thermal mode from weather forecast.

1. Add `async_determine_daily_mode()` to ThermalManager
2. Add decision timer (default 6am) to coordinator
3. Lock mode for the day after decision
4. Add `DailyThermalModeSensor` to sensor.py
5. Add threshold number entities to number.py

**Deliverable:** Daily mode visible in sensor, locked after decision time.

## Phase 4: Pre-conditioning (Issue #63)
**Goal:** Pre-heat/cool before demand window.

1. Add pre-conditioning config options
2. Implement `async_evaluate_preconditioning()` in ThermalManager
3. Implement `async_apply_climate_control()` for setpoint adjustment
4. Add `PreconditioningActiveSensor` binary sensor
5. Coordinate with battery SOC

**Deliverable:** Pre-conditioning runs before demand window, controlled entities adjust setpoints.

## Phase 5: Solar Tapering (Issue #141)
**Goal:** Match HVAC consumption to excess solar.

1. Add solar taper config options
2. Implement `async_evaluate_solar_taper()` in ThermalManager
3. Calculate setpoint offset from excess kW
4. Add `SolarTaperActiveSensor` binary sensor
5. Integrate with INCREASE_LOAD signal

**Deliverable:** HVAC setpoints adjust automatically to consume excess solar.