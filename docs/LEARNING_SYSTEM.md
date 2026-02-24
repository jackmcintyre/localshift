# Learning System

The LocalShift integration includes an adaptive learning system that continuously optimizes battery decisions to minimize your electricity costs while avoiding common pitfalls like over-charging or unnecessary exports.

## Overview

The learning system operates in the background, observing your battery's behavior and the outcomes of charging/discharging decisions. Over time, it adjusts internal parameters to improve decision quality.

### Key Features

- **Decision Tracking**: Records every mode transition with full context
- **Outcome Scoring**: Measures the financial impact of each decision
- **Parameter Optimization**: Adjusts decision thresholds based on outcomes
- **Pattern Recognition**: Identifies systematic issues (e.g., over-charging on cloudy days)
- **Multi-Objective Balance**: Balances cost minimization, export avoidance, and target achievement

## How It Works

### The Feedback Loop

```
┌─────────────────────────────────────────────────────────────────┐
│                    LEARNING SYSTEM LOOP                          │
│                                                                  │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐     │
│   │   Decision   │───▶│   Outcome    │───▶│  Parameter   │     │
│   │   Made       │    │   Tracking   │    │  Optimization│     │
│   └──────────────┘    └──────────────┘    └──────────────┘     │
│          ▲                                        │              │
│          └────────────────────────────────────────┘              │
│                     (Improved Decisions)                         │
└─────────────────────────────────────────────────────────────────┘
```

1. **Decision Made**: When the battery mode changes, the system records the context (SOC, prices, forecasts, weather)
2. **Outcome Tracking**: After the decision period ends, actual costs and results are measured
3. **Parameter Optimization**: Parameters are adjusted to improve future decisions
4. **Improved Decisions**: Next time, the system uses learned parameters

### Learning Phases

The system progresses through three phases:

| Phase | Status | Description | Duration |
|-------|--------|-------------|----------|
| **Observing** | Default | Collecting data, no parameter changes | 2-3 days |
| **Tuning** | After warm-up | Making small parameter adjustments | Ongoing |
| **Optimizing** | Active | Full optimization with pattern recognition | After 1+ week |

**Warm-up Period**: The system requires ~50 decision records before making any parameter adjustments. This typically takes 2-3 days of normal operation.

## Decision Quality Score

The Decision Quality Score (0-100%) measures how well each decision performed:

### Score Components

| Component | Weight | Description |
|-----------|--------|-------------|
| Cost Score | 50% | How much money was saved/lost vs baseline |
| Export Avoidance | 20% | Did we avoid exporting grid-purchased energy? |
| Target Achievement | 20% | Did we reach SOC target by demand window? |
| Cycle Reduction | 10% | Did we avoid rapid mode changes? |

### Interpreting the Score

| Score Range | Interpretation |
|-------------|----------------|
| 80-100% | Excellent decision — optimal outcome |
| 60-80% | Good decision — near-optimal |
| 40-60% | Acceptable — room for improvement |
| 20-40% | Sub-optimal — learning opportunity |
| 0-20% | Poor decision — will trigger parameter adjustment |

## Adaptive Parameters

The learning system adjusts these internal parameters:

### Cheap Price Bias

| Parameter | Range | Effect |
|-----------|-------|--------|
| `cheap_price_bias` | -5.0 to +5.0 c/kWh | Adjusts the cheap price threshold |

- **Positive values**: More willing to grid charge (charge at higher prices)
- **Negative values**: More conservative (only charge at lower prices)

### Solar Confidence Factor

| Parameter | Range | Effect |
|-----------|-------|--------|
| `solar_confidence_factor` | 0.5 to 1.5 | Multiplier on solar forecasts |

- **< 1.0**: Pessimistic — trust solar less, charge more
- **> 1.0**: Optimistic — trust solar more, charge less

### Overnight Drain Safety Margin

| Parameter | Range | Effect |
|-----------|-------|--------|
| `overnight_drain_safety_margin` | -5.0 to +10.0 % | Extra SOC buffer for overnight |

- **Positive values**: Keep more reserve for overnight drain
- **Negative values**: Accept lower overnight SOC

### Grid Charge SOC Headroom

| Parameter | Range | Effect |
|-----------|-------|--------|
| `grid_charge_soc_headroom` | -5.0 to +10.0 % | Extra SOC above target |

- **Positive values**: Charge slightly above target (safety buffer)
- **Negative values**: Charge to exact target

### Export Threshold Adjustment

| Parameter | Range | Effect |
|-----------|-------|--------|
| `export_threshold_adjustment` | -3.0 to +3.0 c/kWh | Adjusts export profitability threshold |

- **Positive values**: More conservative about exporting
- **Negative values**: More aggressive about exporting

### Consumption Forecast Bias

| Parameter | Range | Effect |
|-----------|-------|--------|
| `consumption_forecast_bias` | -0.5 to +0.5 kW | Adjusts consumption predictions |

- **Positive values**: Assume higher consumption
- **Negative values**: Assume lower consumption

## Sensors and Entities

### Learning Status Sensor

**Entity:** `sensor.localshift_learning_status`

Shows the current learning phase and parameter values.

| Attribute | Description |
|-----------|-------------|
| `phase` | Current learning phase (observing/tuning/optimizing) |
| `parameters` | Current parameter values with confidence scores |
| `update_count` | Number of parameter updates made |
| `last_updated` | When parameters were last updated |

### Decision Quality Sensor

**Entity:** `sensor.localshift_decision_quality`

Shows the rolling decision quality score.

| Attribute | Description |
|-----------|-------------|
| `score_today` | Average decision quality today (%) |
| `score_7d` | 7-day rolling average (%) |
| `total_decisions_today` | Count of decisions made today |
| `cost_trend` | improving/stable/degrading |

### Decision History Sensor

**Entity:** `sensor.localshift_decision_history`

Shows recent decision history with outcomes.

| Attribute | Description |
|-----------|-------------|
| `decisions` | Last 20 decisions with context and outcomes |
| `pattern_report` | Latest pattern analysis summary |

## Switches

### Enable Learning

**Entity:** `switch.localshift_enable_learning`

- **ON**: Learning system can adjust parameters
- **OFF**: Learning system observes only, no parameter changes

**Default:** OFF — You must explicitly enable learning

When disabled, the system still tracks decisions but uses default (zero-offset) parameters.

## Buttons

### Reset Learning Data

**Entity:** `button.localshift_reset_learning`

Clears all learning data and starts fresh.

**Warning:** This erases all learned parameters and decision history. The system will return to the "observing" phase.

## FAQ

### How long before the system starts optimizing?

- **Observing phase**: 2-3 days (50 decisions needed)
- **Tuning phase**: Begins after warm-up
- **Full optimization**: After 1+ week of data

### Why is my learning status stuck on "observing"?

The system needs approximately 50 decision records before entering the tuning phase. This typically takes 2-3 days of normal operation. During this time:

- Decisions are tracked but not influenced by learning
- Parameters remain at default (zero-offset) values
- The system builds a baseline dataset

### How do I reset learning data?

1. Go to **Settings → Devices & Services → LocalShift**
2. Find the **Reset Learning Data** button
3. Press it to clear all learning data

This is useful if:
- You've made significant changes to your household patterns
- You want to start fresh after testing
- The system learned sub-optimal parameters

### Can I disable the learning system?

Yes. Use the **Enable Learning** switch to disable parameter optimization. When disabled:

- Decisions are still tracked for observability
- Parameters remain at default values
- No behavioral changes occur

### What happens when I reset learning data?

1. All decision records are cleared
2. All learned parameters are reset to defaults
3. The system returns to "observing" phase
4. Parameter optimization history is erased
5. Pattern analysis data is cleared

### Is my learning data persisted?

Yes. Learning data is stored in Home Assistant's storage system and survives restarts. Data includes:

- Decision records (last 500)
- Parameter values and confidence scores
- Pattern analysis results
- Optimization weights

### How do I know if learning is improving my costs?

Monitor these indicators:

1. **Decision Quality Score**: Should trend upward over time
2. **Cost Trend Attribute**: Shows "improving" when costs are decreasing
3. **Grid Charge Efficiency**: Should improve (less wasted charging)
4. **Export Loss Ratio**: Should decrease (fewer unnecessary exports)

### What's the difference between "tuning" and "optimizing"?

| Phase | Behavior |
|-------|----------|
| **Tuning** | Small parameter adjustments, no pattern-based corrections |
| **Optimizing** | Full optimization including pattern recognition and contextual adjustments |

## Technical Details

### Storage Keys

Learning data is stored under these keys (scoped to entry ID):

| Key | Content |
|-----|---------|
| `localshift.decision_outcomes.{entry_id}` | Decision records |
| `localshift.param_optimizer.{entry_id}` | Optimizer state |
| `localshift.pattern_analysis.{entry_id}` | Pattern data |
| `localshift.opt_controller.{entry_id}` | Controller weights |

### Optimization Safety Rails

The learning system includes several safety mechanisms:

1. **Step Limits**: Parameters can only move one step per daily update
2. **Bounds Clamping**: All parameters stay within defined min/max
3. **Rollback**: If 7-day score decreases for 3 consecutive days, parameters revert
4. **Warm-up**: No adjustments until 50+ decisions collected

### Pattern Detection Dimensions

The pattern analyzer looks for biases across:

- Day of week (Monday-Sunday)
- Hour of day (0-23)
- Weather condition (sunny, cloudy, rainy)
- Season (summer, autumn, winter, spring)
- Price regime (low, medium, high)
- Solar availability (high, medium, low)

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues and solutions.