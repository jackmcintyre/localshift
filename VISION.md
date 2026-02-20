# LocalShift Vision

## Mission

Maximize the value of home solar and battery systems through intelligent automation that minimizes electricity costs while maintaining energy independence.

## Primary Goals

### 1. Cost Minimization
Reduce household electricity costs through:
- **Smart charging**: Charge battery when prices are lowest
- **Strategic exports**: Export during price spikes for maximum revenue
- **Load shifting**: Shift flexible consumption to cheap periods
- **Solar optimization**: Maximize use of free solar energy

### 2. Energy Independence
Reduce reliance on the grid through:
- **Demand window protection**: Ensure battery coverage during peak periods
- **Solar-first charging**: Prioritize solar over grid for battery charging
- **Consumption awareness**: Predict and plan for household energy needs

## Constraints

These constraints take precedence over optimization goals:

1. **Battery Health**: Avoid excessive cycling and deep discharges that reduce battery lifespan
2. **Demand Window Coverage**: Always maintain sufficient SOC to avoid grid imports during peak pricing periods
3. **User Override**: Respect manual commands and provide clear override mechanisms
4. **System Stability**: Fail gracefully, default to self-consumption when uncertain

## Design Principles

### Single Source of Truth
All decisions flow through the forecast computer. The forecast IS the plan - control logic follows what the forecast determines.

### Data-Driven Decisions
Use available data (prices, solar forecasts, consumption patterns) rather than arbitrary time-based rules. When data is unavailable, fail safe.

### Transparency
Every decision is logged with reasoning. Users can understand why the system made each choice.

### Extensibility
New features integrate into the existing forecast-driven architecture. Thermal management, EV charging, and other optimizations follow the same pattern.

## Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Grid import cost | Minimize | Daily/monthly tracking |
| Export revenue | Maximize | Per-spike and daily totals |
| Demand window grid imports | Zero | Binary - any import during DW is failure |
| Battery cycles per day | < 1.0 average | Long-term battery health |
| Forecast accuracy | > 90% | Predicted vs actual SOC at DW start |

## Future Direction

### Near-Term
- Consumption prediction learning (day-of-week patterns, weather correlation)
- Proactive thermal management (pre-cool/pre-heat before demand window)
- Improved forecast data storage

### Medium-Term
- EV charging integration
- Multi-day planning for extended cloudy periods
- Advanced price arbitrage optimization

### Long-Term
- Machine learning for consumption prediction
- Grid outage preparedness
- Community/virtual power plant features

## Related Documentation

- [Architecture](docs/ARCHITECTURE.md) - System design and components
- [Developer Guide](docs/DEVELOPER_GUIDE.md) - Contributing and development
- [Entity Reference](docs/ENTITY_REFERENCE.md) - All sensors and controls