---
name: learning-analyzer-agent
description: Analyze LocalShift learning system performance and suggest optimizations
license: MIT
compatibility: opencode
metadata:
  audience: developers
  workflow: analysis
  triggers:
    - weekly
    - manual
    - post_learning_update
---

## What I Do

Analyze the LocalShift learning system's performance, review optimizer parameters, detect anomalies in decision quality, and suggest improvements to the adaptive algorithms. I help ensure the battery optimization gets better over time.

## When to Use Me

- "Analyze learning system performance"
- "Are decisions getting better?"
- "Review optimizer parameters"
- "Check decision quality trends"
- "What should we learn from this week?"
- "Suggest parameter adjustments"
- "Is the weather correlation working?"
- "Analyze cost savings trends"

## Learning System Overview

LocalShift includes adaptive learning for:

1. **Consumption Prediction** - Weather-based load forecasting
2. **Price Thresholds** - Dynamic cheap/expensive price boundaries
3. **Optimizer Parameters** - DP solver configuration
4. **Decision Quality** - Track and improve choices over time

Key entities to monitor:
- `sensor.localshift_learning_progress` - Overall progress
- `sensor.localshift_decision_quality` - Decision accuracy
- `sensor.localshift_weather_correlation` - Weather correlation strength
- `sensor.localshift_cost_saved_today` - Daily savings
- `sensor.localshift_total_saved` - Cumulative savings

## Analysis Capabilities

### 1. Decision Quality Analysis

```python
# Get decision quality history
quality_history = ha_get_statistics(
    entity_ids="sensor.localshift_decision_quality",
    start_time="30d",
    period="day",
    statistic_types=["mean", "min", "max"]
)

# Analyze trends
for stat in quality_history[0]['statistics']:
    print(f"{stat['start']}: {stat['mean']:.1f}%")

# Detect anomalies
mean_quality = np.mean([s['mean'] for s in quality_history[0]['statistics']])
std_quality = np.std([s['mean'] for s in quality_history[0]['statistics']])

for stat in quality_history[0]['statistics']:
    if stat['mean'] < mean_quality - 2*std_quality:
        print(f"⚠️  Poor decision quality on {stat['start']}: {stat['mean']:.1f}%")
```

### 2. Cost Savings Analysis

```python
# Analyze cost trends
savings_data = ha_get_statistics(
    entity_ids=[
        "sensor.localshift_cost_saved_today",
        "sensor.localshift_grid_cost_today",
        "sensor.localshift_export_revenue_today"
    ],
    start_time="30d",
    period="day",
    statistic_types=["sum"]
)

# Calculate weekly savings
total_saved = sum(s['sum'] for s in savings_data[0]['statistics'])
total_grid_cost = sum(s['sum'] for s in savings_data[1]['statistics'])
total_revenue = sum(s['sum'] for s in savings_data[2]['statistics'])

print(f"30-day summary:")
print(f"  Battery savings: ${total_saved:.2f}")
print(f"  Grid costs: ${total_grid_cost:.2f}")
print(f"  Export revenue: ${total_revenue:.2f}")
print(f"  Net benefit: ${total_saved + total_revenue - total_grid_cost:.2f}")
```

### 3. Weather Correlation Validation

```python
# Check if weather correlation is improving
correlation_data = ha_get_history(
    entity_ids="sensor.localshift_weather_correlation",
    start_time="14d",
    limit=100
)

# Calculate trend
values = [float(state['state']) for state in correlation_data[0]['states'] if state['state'] not in ['unknown', 'unavailable']]
if len(values) > 1:
    trend = np.polyfit(range(len(values)), values, 1)[0]
    if trend > 0.01:
        print(f"📈 Weather correlation improving: +{trend:.3f}/day")
    elif trend < -0.01:
        print(f"📉 Weather correlation declining: {trend:.3f}/day")
    else:
        print(f"➡️  Weather correlation stable")
```

### 4. Mode Transition Analysis

```python
# Analyze which modes are used most
mode_history = ha_get_history(
    entity_ids="sensor.localshift_current_mode",
    start_time="7d",
    limit=1000
)

# Count mode occurrences
from collections import Counter
modes = [state['state'] for state in mode_history[0]['states']]
mode_counts = Counter(modes)

print("Mode distribution (last 7 days):")
for mode, count in mode_counts.most_common():
    pct = count / len(modes) * 100
    print(f"  {mode}: {count} times ({pct:.1f}%)")
```

## Performance Metrics

### Key Performance Indicators (KPIs)

1. **Decision Quality Score**
   - Target: >85%
   - Good: >75%
   - Needs attention: <75%

2. **Daily Cost Savings**
   - Track cumulative trend
   - Compare to baseline (no optimization)

3. **Weather Prediction Accuracy**
   - Correlation coefficient
   - Mean absolute error

4. **Mode Stability**
   - Avoid rapid mode switching
   - Target: <5 changes per hour

### Weekly Analysis Report

```python
def generate_weekly_report():
    """Generate weekly learning system report."""
    
    report = []
    report.append("# LocalShift Learning System Report")
    report.append(f"Week of: {datetime.now().strftime('%Y-%m-%d')}\n")
    
    # Decision quality
    quality_stats = ha_get_statistics(
        entity_ids="sensor.localshift_decision_quality",
        start_time="7d",
        period="day"
    )
    
    report.append("## Decision Quality")
    avg_quality = np.mean([s['mean'] for s in quality_stats[0]['statistics']])
    report.append(f"- Average: {avg_quality:.1f}%")
    report.append(f"- Status: {'✅ Good' if avg_quality > 75 else '⚠️  Needs improvement'}\n")
    
    # Cost analysis
    savings_stats = ha_get_statistics(
        entity_ids="sensor.localshift_total_saved",
        start_time="7d",
        period="week",
        statistic_types=["change"]
    )
    
    if savings_stats[0]['statistics']:
        weekly_savings = savings_stats[0]['statistics'][0]['change']
        report.append("## Cost Savings")
        report.append(f"- This week: ${weekly_savings:.2f}")
        report.append(f"- Daily average: ${weekly_savings/7:.2f}\n")
    
    # Recommendations
    report.append("## Recommendations")
    if avg_quality < 75:
        report.append("- Review optimizer parameters")
        report.append("- Check price threshold calculations")
    
    return "\n".join(report)
```

## Optimization Suggestions

### 1. Threshold Tuning

If decision quality is low:

```python
# Suggest price threshold adjustments
# Based on historical data analysis

def suggest_threshold_adjustments():
    """Suggest price threshold adjustments."""
    
    # Get price history
    price_data = ha_get_statistics(
        entity_ids="sensor.localshift_grid_price",
        start_time="30d",
        period="day",
        statistic_types=["mean", "min", "max"]
    )
    
    # Calculate percentiles
    prices = [s['mean'] for s in price_data[0]['statistics']]
    p10 = np.percentile(prices, 10)
    p90 = np.percentile(prices, 90)
    
    print(f"Price analysis (30 days):")
    print(f"  10th percentile (cheap): ${p10:.2f}")
    print(f"  90th percentile (expensive): ${p90:.2f}")
    print(f"\nSuggested thresholds:")
    print(f"  Cheap price: < ${p10:.2f}")
    print(f"  Expensive price: > ${p90:.2f}")
```

### 2. Mode Timing Analysis

```python
def analyze_mode_effectiveness():
    """Analyze which modes work best in which conditions."""
    
    # Get mode and cost data
    mode_data = ha_get_history(
        entity_ids=["sensor.localshift_current_mode", "sensor.localshift_grid_cost_today"],
        start_time="14d"
    )
    
    # Correlate modes with costs
    mode_costs = defaultdict(list)
    
    # Process data to find which modes had lowest costs
    # Implementation depends on data structure
    
    print("Mode effectiveness analysis:")
    for mode, costs in mode_costs.items():
        avg_cost = np.mean(costs)
        print(f"  {mode}: ${avg_cost:.2f} average daily cost")
```

### 3. Anomaly Detection

```python
def detect_anomalies():
    """Detect unusual patterns in the learning system."""
    
    # Check for stuck states
    mode_data = ha_get_history(
        entity_ids="sensor.localshift_current_mode",
        start_time="24h"
    )
    
    # Check if stuck in one mode too long
    current_mode = mode_data[0]['states'][0]['state']
    hours_in_mode = 0
    
    for state in mode_data[0]['states']:
        if state['state'] == current_mode:
            hours_in_mode += 1
        else:
            break
    
    if hours_in_mode > 6:
        print(f"⚠️  Stuck in {current_mode} mode for {hours_in_mode} hours")
        print("   Consider checking automation_enabled switch")
    
    # Check for unusual cost spikes
    cost_data = ha_get_history(
        entity_ids="sensor.localshift_grid_cost_today",
        start_time="7d"
    )
    
    costs = [float(s['state']) for s in cost_data[0]['states'] if s['state'] not in ['unknown', 'unavailable']]
    mean_cost = np.mean(costs)
    std_cost = np.std(costs)
    
    for i, cost in enumerate(costs):
        if cost > mean_cost + 2*std_cost:
            print(f"⚠️  Unusual cost spike on day {i}: ${cost:.2f} (avg: ${mean_cost:.2f})")
```

## Automated Analysis Script

Save as `scripts/analyze-learning.py`:

```python
#!/usr/bin/env python3
"""Analyze LocalShift learning system performance."""

import numpy as np
from datetime import datetime, timedelta

def main():
    print("🔍 Analyzing LocalShift learning system...\n")
    
    # Get current states
    states = ha_get_states([
        "sensor.localshift_learning_progress",
        "sensor.localshift_decision_quality",
        "sensor.localshift_weather_correlation",
        "sensor.localshift_total_saved"
    ])
    
    print("Current Status:")
    for state in states:
        print(f"  {state['entity_id'].split('.')[-1]}: {state['state']}")
    
    print("\n📊 Generating recommendations...")
    
    # Decision quality trend
    quality_data = ha_get_statistics(
        entity_ids="sensor.localshift_decision_quality",
        start_time="14d",
        period="day"
    )
    
    if quality_data[0]['statistics']:
        qualities = [s['mean'] for s in quality_data[0]['statistics']]
        trend = np.polyfit(range(len(qualities)), qualities, 1)[0]
        
        if trend > 0.1:
            print("✅ Decision quality improving")
        elif trend < -0.1:
            print("⚠️  Decision quality declining - review parameters")
        else:
            print("➡️  Decision quality stable")
    
    print("\nAnalysis complete!")

if __name__ == "__main__":
    main()
```

## Weekly Monitoring Schedule

Run every Sunday:

```bash
#!/bin/bash
# weekly-learning-analysis.sh

echo "📅 Weekly Learning System Analysis"
echo "=================================="

# Check decision quality trend
python scripts/analyze-learning.py

# Generate report
python scripts/generate-weekly-report.py > reports/learning-weekly-$(date +%Y-%m-%d).md

# If issues detected, alert
if grep -q "⚠️" reports/learning-weekly-*.md; then
    echo "Issues detected - review recommended"
    # Could send notification here
fi
```

## Tips

1. **Weekly reviews** - Run analysis weekly to catch issues early
2. **Compare to baseline** - Track improvements over time
3. **Seasonal adjustments** - Consumption patterns change with seasons
4. **Validate assumptions** - Don't blindly trust the learning system
5. **Monitor stability** - Avoid over-fitting to recent data
6. **Check anomalies** - Investigate unusual patterns
7. **Document changes** - Note when you adjust parameters
8. **Gradual changes** - Adjust parameters slowly

## Quick Commands

```bash
# Run full analysis
python scripts/analyze-learning.py

# Check decision quality
ha_get_state("sensor.localshift_decision_quality")

# View savings
ha_get_state("sensor.localshift_total_saved")

# Check weather correlation
ha_get_state("sensor.localshift_weather_correlation")

# Get recent decisions
ha_get_history(entity_ids="sensor.localshift_current_mode", start_time="24h")
```
