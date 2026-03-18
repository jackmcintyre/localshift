# LocalShift Integration - System Architecture

## Overview

The LocalShift integration optimizes Tesla Powerwall battery charging/discharging based on:
- Amber Electric spot prices (5-minute intervals)
- Solcast solar forecasts (30-minute intervals)  
- Tesla Powerwall state (via Teslemetry)
- Household consumption patterns
- Adaptive learning from past decisions

## System Design Goals

The architecture was designed to solve several problems from the original YAML-based automation:

1. **Eliminate "stuck state" bugs** — A state machine evaluates on every change to prevent edge cases where the battery could get stuck in a state.

2. **Single source of truth** — All mode decisions flow through one priority chain, not spread across 18 independent automations.

3. **Testable** — Python code is far easier to test than YAML automations.

4. **Configurable** — No more editing YAML for threshold changes. All options available via UI.

5. **Observable** — Extensive sensors and logging for debugging.

6. **Data-driven optimization** — Use dynamic programming (DP) to compute optimal charging schedules.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        HOME ASSISTANT CORE                                   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    LocalShift Integration                        │  │
│  │                                                                       │  │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────┐  │  │
│  │  │  Config    │    │   Entity   │    │   Coordinator            │  │  │
│  │  │  Flow      │───▶│   Platform │───▶│   (LocalShiftCoordinator)│  │  │
│  │  │            │    │   (sensor, │    │                         │  │  │
│  │  │            │    │    binary,  │    │   - Tiered tick       │  │  │
│  │  │            │    │    switch, │    │     scheduling        │  │  │
│  │  │            │    │    number, │    │   - Event evaluation   │  │  │
│  │  │            │    │    select, │    │   - Entity subscriptions│  │  │
│  │  │            │    │    button) │    │                         │  │  │
│  │  └─────────────┘    └─────────────┘    └───────────┬───────────┘  │  │
│  │                                                      │              │  │
│  │          ┌───────────────────────────────────────────┼──────────────┤  │
│  │          │                                           │              │  │
│  │          ▼                                           ▼              │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐│  │
│  │  │                      Core Services                            ││  │
│  │  │                                                                 ││  │
│  │  │  ┌─────────────────┐  ┌──────────────────┐  ┌───────────────┐ ││  │
│  │  │  │ Evaluation     │  │ Computation      │  │   State       │ ││  │
│  │  │  │ Dispatcher     │─▶│   Engine         │─▶│   Machine     │ ││  │
│  │  │  │                 │  │                  │  │               │ ││  │
│  │  │  │ - State change │  │ - Prepares       │  │ - Evaluates   │ ││  │
│  │  │  │   triggers     │  │   forecasts      │  │   desired     │ ││  │
│  │  │  │ - Stale price  │  │ - Runs optimizer │  │   mode        │ ││  │
│  │  │  │ - Load deviat. │  │ - Computes plan │  │ - Applies     │ ││  │
│  │  │  │ - Solar events │  │                  │  │   commands    │ ││  │
│  │  │  └─────────────────┘  └────────┬─────────┘  └───────────────┘ ││  │
│  │  │                                │                               ││  │
│  │  │                                ▼                               ││  │
│  │  │              ┌─────────────────────────────────────────┐     ││  │
│  │  │              │         Optimizer Engine (DP)           │     ││  │
│  │  │              │                                          │     ││  │
│  │  │              │  ┌─────────────────────────────────────┐ │     ││  │
│  │  │              │  │  OptimizerFacade                   │ │     ││  │
│  │  │              │  │  - Slot building                   │ │     ││  │
│  │  │              │  │  - Solar/cloud corrections        │ │     ││  │
│  │  │              │  │  - Planner execution               │ │     ││  │
│  │  │              │  │  - Runtime mode assignment         │ │     ││  │
│  │  │              │  └─────────────────────────────────────┘ │     ││  │
│  │  │              │                                          │     ││  │
│  │  │              │  ┌─────────────┐ ┌─────────────┐ ┌──────┐ │     ││  │
│  │  │              │  │ constraints │ │    cost     │ │types │ │     ││  │
│  │  │              │  │  (feasible) │ │ (penalties) │ │      │ │     ││  │
│  │  │              │  └─────────────┘ └─────────────┘ └──────┘ │     ││  │
│  │  │              │                                          │     ││  │
│  │  │              │  ┌─────────────────────────────────────┐ │     ││  │
│  │  │              │  │  optimizer_dp.py                   │ │     ││  │
│  │  │              │  │  - Dynamic programming solver       │ │     ││  │
│  │  │              │  │  - feasible_actions()               │ │     ││  │
│  │  │              │  │  - stage_cost()                      │ │     ││  │
│  │  │              │  │  - terminal_cost()                  │ │     ││  │
│  │  │              │  └─────────────────────────────────────┘ │     ││  │
│  │  │              └─────────────────────────────────────────┘     ││  │
│  │  └─────────────────────────────────────────────────────────────────┘│  │
│  │                                          │                          │  │
│  │     ┌────────────────────────────────────┼────────────────────────┐  │  │
│  │     │                                    ▼                        │  │  │
│  │     │  ┌─────────────────────────────────────────────────────────┐│  │  │
│  │     │  │                    Forecast System                     ││  │  │
│  │     │  │                                                          ││  │  │
│  │     │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ ││  │  │
│  │     │  │  │  load.py │  │ solar.py │  │ accuracy│  │ pipeline│ ││  │  │
│  │     │  │  │          │  │          │  │         │  │        │ ││  │  │
│  │     │  │  │ Load     │  │ Solar    │  │ Forecast │ │Orchestr.│ ││  │  │
│  │     │  │  │ forecast │  │ forecast │  │ accuracy │ │        │ ││  │  │
│  │     │  │  └──────────┘  └──────────┘  └──────────┘  └────────┘ ││  │  │
│  │     │  │                                                          ││  │  │
│  │     │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ││  │  │
│  │     │  │  │load_deviation│  │ solar_events │  │ corrections │  ││  │  │
│  │     │  │  │  (1-min tick)│  │ (mid-day     │  │ (cloud      │  ││  │  │
│  │     │  │  │              │  │  re-opt)     │  │  bias)      │  ││  │  │
│  │     │  │  └──────────────┘  └──────────────┘  └──────────────┘  ││  │  │
│  │     │  └─────────────────────────────────────────────────────────┘│  │  │
│  │     │                                                               │  │  │
│  │     │  ┌─────────────────────────────────────────────────────────┐│  │  │
│  │     │  │                    Learning System                      ││  │  │
│  │     │  │                                                          ││  │  │
│  │     │  │  ┌─────────────┐  ┌─────────────┐  ┌───────────────┐   ││  │  │
│  │     │  │  │ parameters  │  │  outcomes   │  │pattern_analyzer│  ││  │  │
│  │     │  │  │ (Thompson   │  │ (decision   │  │ (bias         │   ││  │  │
│  │     │  │  │  sampling)  │  │  tracking)  │  │  detection)   │   ││  │  │
│  │     │  │  └─────────────┘  └─────────────┘  └───────────────┘   ││  │  │
│  │     │  └─────────────────────────────────────────────────────────┘│  │  │
│  │     │                                                               │  │  │
│  │     └───────────────────────────────────────────────────────────────┘  │  │
│  │                                                                       │  │
│  │  External Integrations (read):                                       │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                        │  │
│  │  │  Teslemetry │  │   Amber     │  │   Solcast  │                        │  │
│  │  │             │  │   Electric  │  │            │                        │  │
│  │  │  Powerwall  │◀─│   Pricing  │◀─│   Solar    │                        │  │
│  │  │   control   │  │   forecasts │  │  forecasts │                        │  │
│  │  └──────┬──────┘  └──────┬──────┘  └─────┬──────┘                        │  │
│  │         │                 │                  │                               │  │
│  │         ▼                 ▼                  ▼                               │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  │                     TESLA POWERWALL HARDWARE                        │    │
│  │  │                                                                     │    │
│  │  │   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐    │    │
│  │  │   │   Solar  │  │   Grid   │  │ Battery  │  │    Home      │    │    │
│  │  │   │  Panels  │  │  Import/ │  │  (13.5  │  │   Load       │    │    │
│  │  │   │          │  │  Export  │  │   kWh)   │  │              │    │    │
│  │  │   └──────────┘  └──────────┘  └──────────┘  └───────────────┘    │    │
│  │  └─────────────────────────────────────────────────────────────────────┘    │
│  └──────────────────────────────────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Module Structure

```
custom_components/localshift/
├── __init__.py               # Integration entry point, creates coordinator
├── const.py                  # Constants, enums, entity keys, defaults
├── computation_engine.py     # Orchestrates forecast + optimizer execution
│
├── coordinator/              # Data coordination
│   ├── coordinator.py        # LocalShiftCoordinator: tiered ticks, event handling
│   └── data.py               # CoordinatorData, OptimizerResult dataclasses
│
├── engine/                   # DP Optimization engine
│   ├── optimizer_dp.py       # Core DP solver (feasible_actions, stage_cost, terminal_cost)
│   ├── optimizer_facade.py   # Facade: slot building, corrections, planner, runtime mode
│   ├── optimizer_runner.py   # Coordinator integration
│   ├── constraints.py        # Hard constraint functions (feasible_actions)
│   ├── cost.py               # Cost function components (stage_cost, terminal_cost)
│   ├── types.py              # Type definitions (SlotContext, OptimizerConfig, etc.)
│   ├── core.py               # Core optimizer logic
│   ├── parameters.py          # Adaptive parameter management (Thompson sampling)
│   ├── outcomes.py           # Decision outcome tracking
│   ├── pattern_analyzer.py   # Bias detection (weekly patterns)
│   ├── counterfactual.py     # TOU baseline scoring
│   ├── optimization_controller.py # Real-time contextual adjustments
│   ├── slots.py              # Slot building (SlotBuilder, SlotBuildMetadata)
│   ├── slot_schedule.py      # Hybrid slot schedule (5-min + 15-min)
│   ├── price_calculator.py  # Price calculations
│   ├── price_signal_engine.py # Price signal orchestration
│   ├── excess_solar.py      # Excess solar detection
│   ├── excess_solar_signals.py # Load shift signals
│   ├── soc_simulator.py     # SOC simulation
│   ├── spike_analyzer.py    # Price spike detection
│   ├── dp_math.py           # DP math utilities
│   ├── weather_diagnostics.py # Weather diagnostics
│   └── utils.py             # Engine utilities
│
├── forecast/                 # Forecasting modules
│   ├── pipeline.py           # Forecast orchestration
│   ├── load.py               # Load forecasting
│   ├── solar.py              # Solar calculations
│   ├── accuracy.py           # Forecast accuracy engine
│   ├── solar_accuracy.py     # Solar accuracy tracking
│   ├── history.py            # Historical data fetching
│   ├── history_store.py      # Forecast history storage
│   ├── bootstrapper.py       # Forecast initialization
│   ├── load_deviation.py     # Real-time load deviation detection (1-min tick)
│   ├── solar_events.py       # Mid-day solar re-optimization detection
│   └── corrections.py        # Cloud bias corrections
│
├── integration/              # External integrations
│   ├── controller.py         # Battery controller (Teslemetry)
│   └── client.py             # Powerwall service client
│
├── learning/                 # Adaptive learning system
│   ├── orchestrator.py       # Learning system coordinator
│   └── correlation.py        # Weather correlation engine
│
├── services/                 # Core services
│   ├── evaluation_dispatcher.py # Decides when to trigger re-evaluation
│   ├── notification_service.py  # Notification dispatch
│   └── subscription_manager.py  # Entity subscriptions and timers
│
├── state/                    # State machine
│   ├── machine.py            # StateMachine: state evaluation and transitions
│   ├── mode_configs.py       # Per-mode configuration and executor mapping
│   ├── reader.py             # External entity reader
│   └── validator.py          # Transition validator
│
├── sensors/                  # Sensor implementations (organized by domain)
│   ├── base.py               # Base sensor class
│   ├── pricing.py            # Price-related sensors (3 sensors)
│   ├── forecast.py           # Forecast/optimizer sensors (9 sensors)
│   ├── status.py             # Status/health sensors (7 sensors)
│   ├── learning.py           # Learning-related sensors (4 sensors)
│   ├── optimizer.py          # Optimizer-specific sensors (3 sensors)
│   ├── misc.py               # Miscellaneous sensors (2 sensors)
│   ├── load_deviation.py     # Load deviation sensor (1 sensor)
│   └── cloud_event.py        # Cloud event sensor (1 sensor)
│
├── utils/                    # Shared utilities
│   ├── validation.py         # Entity validation
│   ├── costs.py              # Cost tracking
│   └── entity_configs.py     # Entity configuration helpers
│
├── config_flow/              # HA configuration flow
│   ├── __init__.py           # Config flow entry point
│   ├── schemas.py            # Config schemas
│   └── validators.py        # Config validators
│
├── *.py (HA entity platforms - root level per HA convention)
│   ├── sensor.py             # 30 sensor entities (delegates to sensors/ package)
│   ├── binary_sensor.py      # 10 binary sensor entities
│   ├── switch.py             # 8 switch entities
│   ├── number.py             # 4 number entities
│   ├── select.py             # 2 select entities (Battery Mode, Optimization Mode)
│   └── button.py             # 2 button entities
│
├── manifest.json             # HA manifest
├── strings.json              # Localization strings
└── dashboard.yaml            # Dashboard configuration
```

## Core Components

### LocalShiftCoordinator (`coordinator/coordinator.py`)

The main coordinator that ties all modules together:

- **Tiered tick scheduling**: Fast (1-min), Medium (5-min), Slow (30-min), Daily summary
- **Event handling**: Listens for state changes, prices, forecasts
- **Entity subscriptions**: Manages all HA entity subscriptions
- **Startup bootstrap**: Ensures forecasts are ready before first optimization
- **Listener notification**: Notifies HA of state changes

### EvaluationDispatcher (`services/evaluation_dispatcher.py`)

Decides when to trigger re-evaluation/re-optimization:

- **State change triggers**: When relevant entities change
- **Stale price detection**: When price data becomes stale
- **Load deviation detection**: When load deviates >1kW for 10min or >3kW for 5min
- **Solar event detection**: When significant solar changes occur

### ComputationEngine (`computation_engine.py`)

Orchestrates forecast computation and optimizer execution:

- Prepares forecasts (load, solar, prices)
- Runs the optimizer to compute optimal plan
- Handles errors gracefully with fallback modes

### OptimizerFacade (`engine/optimizer_facade.py`)

Facade for optimizer access:

- **Slot building**: Constructs time slots for optimization
- **Solar/cloud corrections**: Applies bias corrections
- **Planner execution**: Runs the DP planner
- **Runtime mode assignment**: Maps optimizer actions to battery modes with safety gate

### StateMachine (`state/machine.py`)

Evaluates desired operating mode:

- Processes mode decisions
- Applies battery commands via integration controller
- Handles manual overrides

### Integration Controller (`integration/controller.py`)

Interfaces with Tesla Powerwall via Teslemetry:

- Sends charge/discharge commands
- Reads Powerwall state
- Handles rate limiting and retries

## Data Flow

```
1. External entities update (prices, solar, load, battery state)
                      │
                      ▼
2. Coordinator receives state change notification
                      │
                      ▼
3. EvaluationDispatcher checks if re-evaluation needed
   - State change? Stale price? Load deviation? Solar event?
                      │
              ┌───────┴───────┐
              │               │
         No change       Re-evaluate needed
              │               │
              ▼               ▼
         Wait for        ComputationEngine.run()
         next tick              │
                               ▼
                        Forecast pipeline
                        (load, solar, prices)
                               │
                               ▼
                        OptimizerFacade.compute()
                               │
                    ┌──────────┴──────────┐
                    │                     │
              DP solver run          Fallback (error)
                    │                     │
                    ▼                     ▼
              OptimizerResult       SELF_CONSUMPTION mode
                    │                     │
                    └──────────┬──────────┘
                               │
                               ▼
                        StateMachine.apply()
                               │
                               ▼
                        IntegrationController
                        (Tesla Powerwall commands)
```

## Entity Summary

| Platform | Count | Examples |
|----------|-------|----------|
| Sensors | 30 | `sensor.localshift_optimizer_plan`, `sensor.localshift_forecast_battery` |
| Binary Sensors | 10 | `binary_sensor.localshift_charge_boost`, `binary_sensor.localshift_excess_solar_available` |
| Switches | 8 | `switch.localshift_automation_enabled`, `switch.localshift_spike_discharge_enabled` |
| Numbers | 4 | `number.localshift_cheap_price_percentile`, `number.localshift_battery_target` |
| Selects | 2 | `select.localshift_battery_mode`, `select.localshift_optimization_mode` |
| Buttons | 2 | `button.localshift_update_forecast`, `button.localshift_reset_learning` |

**Total: 56 entities**

See [ENTITY_REFERENCE.md](ENTITY_REFERENCE.md) for complete entity details.

## State Machine

The state machine evaluates desired operating mode based on:

1. **Optimizer result**: If DP optimizer produces valid plan
2. **Manual override**: User-selected mode via select entity
3. **Price conditions**: Cheap/expensive periods
4. **Solar availability**: Excess solar detection
5. **Battery constraints**: SOC, target, limits

See [PLANNING_MODEL.md](PLANNING_MODEL.md) for optimizer constraint design.

## Learning System

The adaptive learning system adjusts optimizer parameters:

- **Parameters**: Thompson sampling for price bias, solar confidence, etc.
- **Outcomes**: Tracks decisions and backfills results
- **Pattern Analysis**: Detects weekly systematic biases
- **Safety Rails**: Warm-up period, step limits, bounds, rollback

See [LEARNING_SYSTEM.md](LEARNING_SYSTEM.md) for details.

## Related Documentation

- [PLANNING_MODEL.md](PLANNING_MODEL.md) - Optimizer constraint design (MUST READ for engine changes)
- [ENTITY_REFERENCE.md](ENTITY_REFERENCE.md) - Complete entity catalog
- [INDEX.md](INDEX.md) - Documentation index with domain-specific guides
- [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) - Development patterns and conventions