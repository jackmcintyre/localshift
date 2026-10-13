# Developer Guide

This guide covers the technical architecture of the LocalShift integration for developers who want to understand, extend, or contribute to the codebase.

## Project Structure

```
custom_components/localshift/
├── __init__.py               # Integration entry point, creates coordinator
├── const.py                  # Constants, enums, entity keys, defaults
├── computation_engine.py     # Orchestrates forecast + optimizer execution
│
├── coordinator/              # Data coordination
│   ├── coordinator.py        # LocalShiftCoordinator: tiered ticks, event handling
│   └── data.py              # CoordinatorData, OptimizerResult dataclasses
│
├── engine/                  # DP Optimization engine
│   ├── optimizer_dp.py      # Core DP solver (feasible_actions, stage_cost, terminal_cost)
│   ├── optimizer_facade.py  # Facade: slot building, corrections, planner, runtime mode
│   ├── optimizer_runner.py   # Coordinator integration
│   ├── constraints.py       # Hard constraint functions (feasible_actions)
│   ├── cost.py              # Cost function components (stage_cost, terminal_cost)
│   ├── types.py             # Type definitions (SlotContext, OptimizerConfig, etc.)
│   ├── core.py              # Core optimizer logic
│   ├── parameters.py       # Adaptive parameter management (Thompson sampling)
│   ├── outcomes.py          # Decision outcome tracking
│   ├── pattern_analyzer.py   # Bias detection (weekly patterns)
│   ├── counterfactual.py    # TOU baseline scoring
│   ├── optimization_controller.py # Real-time contextual adjustments
│   ├── slots.py             # Slot building (SlotBuilder, SlotBuildMetadata)
│   ├── slot_schedule.py     # Hybrid slot schedule (5-min + 15-min)
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
├── forecast/                # Forecasting modules
│   ├── pipeline.py          # Forecast orchestration
│   ├── load.py              # Load forecasting
│   ├── solar.py             # Solar calculations
│   ├── accuracy.py          # Forecast accuracy engine
│   ├── solar_accuracy.py    # Solar accuracy tracking
│   ├── history.py           # Historical data fetching
│   ├── history_store.py     # Forecast history storage
│   ├── bootstrapper.py      # Forecast initialization
│   ├── load_deviation.py    # Real-time load deviation detection
│   └── solar_events.py      # Mid-day solar re-optimization detection
│
├── integration/             # External integrations
│   ├── controller.py        # Battery controller (Teslemetry)
│   └── client.py            # Powerwall service client
│
├── learning/                # Adaptive learning system
│   ├── orchestrator.py      # Learning system coordinator
│   ├── correlation.py       # Weather correlation regression + storage facade
│   ├── temperature.py       # Weather forecast fetching/parsing/caching
│   └── anomaly.py           # Weather anomaly detection
│
├── services/                # Core services
│   ├── evaluation_dispatcher.py # Decides when to trigger re-evaluation
│   ├── notification_service.py  # Notification dispatch
│   └── subscription_manager.py # Entity subscriptions and timers
│
├── state/                   # State machine
│   ├── machine.py           # StateMachine: state evaluation and transitions
│   ├── mode_configs.py      # Per-mode configuration and executor mapping
│   ├── reader.py            # External entity reader
│   └── validator.py         # Transition validator
│
├── sensors/                 # Sensor implementations (organized by domain)
│   ├── base.py              # Base sensor class
│   ├── pricing.py           # Price-related sensors (3 sensors)
│   ├── forecast.py          # Forecast/optimizer sensors (9 sensors)
│   ├── status.py            # Status/health sensors (7 sensors)
│   ├── learning.py          # Learning-related sensors (4 sensors)
│   ├── optimizer.py         # Optimizer-specific sensors (3 sensors)
│   ├── misc.py              # Miscellaneous sensors (2 sensors)
│   ├── load_deviation.py    # Load deviation sensor (1 sensor)
│   └── cloud_event.py       # Cloud event sensor (1 sensor)
│
├── utils/                   # Shared utilities
│   ├── validation.py        # Entity validation
│   ├── costs.py             # Cost tracking
│   └── entity_configs.py    # Entity configuration helpers
│
├── config_flow/             # HA configuration flow
│   ├── __init__.py          # Config flow entry point
│   ├── schemas.py           # Config schemas
│   └── validators.py        # Config validators
│
├── *.py (HA entity platforms - root level per HA convention)
│   ├── sensor.py            # 30 sensor entities (delegates to sensors/ package)
│   ├── binary_sensor.py     # 10 binary sensor entities
│   ├── switch.py            # 8 switch entities
│   ├── number.py            # 4 number entities
│   ├── select.py            # 2 select entities
│   └── button.py            # 2 button entities
│
├── manifest.json            # HA manifest
├── strings.json             # Localization strings
├── dashboard.yaml           # Dashboard configuration
└── AGENTS.md                # Agent rules for this integration
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

Mode selection is driven by the DP optimizer with safety gates. See [PLANNING_MODEL.md](PLANNING_MODEL.md) for details.

### Optimization Modes

| Mode | Description |
|------|-------------|
| `auto` | Full automation with DP optimizer |
| `eco` | Economic optimization (cost-focused) |
| `self_consumption` | Maximize self-consumption only |

### Entity Architecture

The integration creates 56 entities:

| Platform | Count | Notes |
|----------|-------|-------|
| Sensors | 30 | Implementation in `sensors/` package |
| Binary Sensors | 10 | In `binary_sensor.py` |
| Switches | 8 | In `switch.py` |
| Numbers | 4 | In `number.py` |
| Selects | 2 | Battery mode, optimization mode |
| Buttons | 2 | Update forecast, reset learning |

Sensors are organized by domain in the `sensors/` package for maintainability.

## Extension Points

### Adding a New Sensor

1. Create sensor class in appropriate `sensors/*.py` file
2. Register in `sensor.py` platform `async_setup_entry`
3. Add entity key to `const.py` ENTITY_KEYS
4. Update [ENTITY_REFERENCE.md](ENTITY_REFERENCE.md)

Example for a pricing sensor:
```python
# sensors/pricing.py
class EffectiveCheapPriceSensor(LocalShiftSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:currency-usd"
    _attr_state_class = SensorStateClass.MEASUREMENT
    
    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("price_cheap_effective")
```

### Modifying the Optimizer

Any optimizer changes MUST consult [PLANNING_MODEL.md](PLANNING_MODEL.md) first:

| Question | Answer → |
|----------|----------|
| Impossible/forbidden? | Add to `feasible_actions()` |
| Required by deadline? | Add to `terminal_cost()` |
| Discouraged/preferred? | Add penalty to `stage_cost()` |

Key files:
- `engine/optimizer_dp.py` — DP solver
- `engine/constraints.py` — Hard constraints
- `engine/cost.py` — Cost functions
- `engine/types.py` — Type definitions

### Changing Evaluation Triggers

Evaluation triggers are defined in `services/evaluation_dispatcher.py`:
- State change triggers
- Stale price detection
- Load deviation detection (>1kW for 10min, >3kW for 5min)
- Solar event detection

### Config Flow Changes

Config flow is in `config_flow/__init__.py`:
- Initial setup: 3 steps (user, entity mapping, settings)
- Options flow: 2 steps (entity_mappings, settings)

## Key Sensors

Important sensors for debugging:

| Sensor | Purpose |
|--------|---------|
| `sensor.localshift_optimizer_plan` | Current optimization plan |
| `sensor.localshift_optimizer_plan_grid` | Grid view of plan |
| `sensor.localshift_optimizer_plan_detailed` | Detailed per-slot decisions |
| `sensor.localshift_forecast_battery` | Battery SOC forecast |
| `sensor.localshift_cost_electricity_net` | Net electricity cost |
| `sensor.localshift_decision_log` | Recent optimizer decisions |
| `sensor.localshift_learning_decision_history` | Recorded decisions and outcomes (telemetry) |
| `sensor.localshift_automation_ready` | System ready for automation |

## Code Style

The project follows these conventions:

- **Python**: 3.13+ with type hints
- **Type hints**: All functions have type annotations
- **Docstrings**: Google-style docstrings for public methods
- **Logging**: Use `_LOGGER.info/debug/warning/error`. INFO is for a state
  change or something actionable (a pre-charge decision, a backstop firing,
  a mode transition); DEBUG is for per-cycle or intermediate state that
  fires every optimizer tick regardless of whether anything changed.
  Issue-tagged `ISSUE_<n>` diagnostics added while chasing a bug must never
  ship at INFO/WARNING — `tests/test_logging_patterns.py` statically blocks
  this, and demoting a message means updating any test that asserts on it
  at the old level (issue #976).
- **Constants**: All magic numbers in `const.py`
- **Async**: Use `async/await` throughout, no blocking calls

## Prerequisites for Development

1. **Home Assistant**: A running HA instance (dev or production)
2. **Python**: 3.13+ (required for type hints)
3. **Tools**: `uv` for package management

### Local Development

```bash
# Clone the repository
git clone https://github.com/jackmcintyre/localshift.git

# Set up development environment
cd localshift
uv sync

# Run tests
uv run pytest

# Lint
uv run ruff check custom_components/localshift
```

### Deployment

When running locally with Home Assistant:

```bash
# Deploy to HA custom_components
./deploy.sh --reserve && ./deploy.sh

# Dry run
./deploy.sh --dry-run
```

The `deploy.sh` script copies files to HA and reloads the integration.

## Testing

Tests are in `tests/` directory:

```bash
# Run all tests with coverage
uv run pytest --cov=custom_components/localshift --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_optimizer_dp_solve.py -v

# Run with verbose output
uv run pytest -vvs
```

See [tests/AGENTS.md](tests/AGENTS.md) for test patterns.

### Proving a change is behaviour-neutral

For dead-code removal, refactors and renames: changes that are meant to leave
every plan and every command exactly as they were. Passing tests are not
enough evidence here, for two reasons this repo has already paid for.

- **The plan and command paths catch broadly and carry on.** A removed
  constructor argument once made the inline optimizer raise on every cycle;
  `OptimizerFacade.run_inline` logged a warning and the suite stayed green.
- **Name-based "is it used?" analysis is unsound.** `SlotBuilder` reaches the
  facade as an injected class reference, and the state machine dispatches
  through string tables (`MODE_CONFIG_BUILDERS`, `MODE_EXECUTORS`) and
  `getattr`. A `grep` for the call site finds nothing and proves nothing.

Two gates cover it. Both run in CI; a deletion PR also runs the second one in
full, locally.

**1. The swallowed-failure tripwire** (`tests/swallow_tripwire.py`, wired in
`tests/conftest.py`). Any test during which a guarded `except` block catches
and logs a failure fails, naming the block and what was swallowed. It needs
nothing from you unless a test is *about* that failure path, in which case it
says so:

```python
@pytest.mark.allow_swallowed_failure("inline_optimizer")
def test_run_inline_logs_and_swallows_unexpected_exception(): ...
```

An opt-in that the test never reaches fails too. If the tripwire fires on a
test you did not expect, do not add the marker to make it pass: either the
change broke something the production code hid, or the test's fixture was
already broken and nobody could tell. The guarded blocks are listed in
`TRIPWIRES`; add one when you add a swallow-and-continue `except` on the plan
or command path.

**2. The planner replay baseline** (`scripts/replay_plan_baseline.py`,
baseline in `simulations/plan-baseline/`). Every tracked capture under
`simulations/` is replayed through `ComputationEngine.compute_derived_values`
under two configurations, and the plan (every slot's action, SOC and inputs,
the commanded mode, the published summary) is compared with the checked-in
one.

```bash
uv run scripts/replay_plan_baseline.py           # compare; exit 1 and a diff on any change (about 45 s)
uv run scripts/replay_plan_baseline.py --write   # regenerate the baseline
```

How a deletion PR uses it:

1. Before touching anything, run the comparison on a clean checkout of
   `main`. It must report `plans identical`. If it does not, stop: the
   baseline is stale and that is somebody else's change to explain.
2. Make the deletion. Run the comparison again. It must still report
   `plans identical`, with no baseline file modified. Quote that line in the
   PR.
3. If it reports a difference, the deletion was not dead code. Do not
   regenerate the baseline to make it pass.

A PR that *means* to change plans runs `--write` and commits the result; the
baseline diff is then the reviewable statement of what changed, slot by slot.
`tests/test_replay_plan_baseline.py` runs three captures of the comparison on
every test run (about two seconds) and fails CI if any of the three plans
moves. That is a smoke check, not the proof: only the full script covers all
84 captures, and CI does not run it.

The replay pins every clock and Home Assistant's default time zone, and is
byte-identical from run to run on the same code. Add a capture to one of the
tracked directories and run `--write` to bring it under the gate.

## Architecture Overview

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed system architecture.

## Related Documentation

- [PLANNING_MODEL.md](PLANNING_MODEL.md) - Optimizer design (MUST READ for engine changes)
- [ENTITY_REFERENCE.md](ENTITY_REFERENCE.md) - Complete entity catalog
- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture diagrams
- [INDEX.md](INDEX.md) - Documentation index
