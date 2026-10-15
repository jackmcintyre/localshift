# 11 Oct morning export at 1-7c: replay (issue #1200)

On Sunday 2026-10-11 (`self_consumption` mode) the planner committed
PROACTIVE_EXPORT nine times between 07:35 and 11:20 at feed-in prices of 1-7c,
taking the battery to the 5% floor. These are the optimizer inputs at seven of
the instants the battery mode was committed, replayed through the planning path
(`scripts/replay_plan_baseline.replay`, arms `captured` and `live_like`).

```
uv run scripts/replay_1200.py replay                    # Solcast forecast rebuilt from history
uv run scripts/replay_1200.py replay --solar measured   # measured solar instead
uv run scripts/replay_1200.py replay --neg-scale 2      # negative feed-in prices doubled
uv run scripts/replay_1200.py slots 2026-10-11T073535 --arm captured
```

Capture instants are the `select.localshift_battery_mode` commits, each about
15 s after Amber confirmed the interval's price. The decision-outcomes store
records the same decisions about 3 minutes later (07:38:30, 10:53:09, ...).

## Result

| Instant | SOC | Buy | Sell | Live on 11 Oct | 11 Oct code (5d875412), captured / live_like | Current main (1ceb3447), captured / live_like |
|---|---|---|---|---|---|---|
| 07:35:35 | 10.1 | 13.73 | 6.71 | export | export / export | hold / hold |
| 07:45:18 | 9.7 | 7.72 | 1.25 | hold | hold / hold | hold / hold |
| 08:30:51 | 7.8 | 9.69 | 3.04 | export | hold / hold | hold / hold |
| 09:25:51 | 6.0 | 9.40 | 2.77 | hold | export / export | hold / hold |
| 09:30:39 | 7.1 | 11.30 | 4.50 | export | export / export | hold / hold |
| 10:05:41 | 6.7 | 12.60 | 4.45 | export | export / export | hold / hold |
| 10:50:42 | 18.3 | 11.30 | 3.27 | export | export / export | hold / hold |

Prices in c/kWh. On the 11 Oct code every export is reason
`NEGATIVE_FIT_AVOIDANCE`, slot 0 exports 0.25-0.72 kWh and the plan bottoms out
at 5%. On main slot 0 holds at every instant in both arms and with either solar
source, the plan has no export action anywhere, and its lowest SOC is 6.7-20.7%.
The export kWh main still plans is solar spill in hold slots once the battery
is full.

## Which change

Bisected on the same captures: 5d875412 (live on 11 Oct; its subpackages are
byte-identical to the deploy backup `localshift.backup.20261012_091447`) and
1d3074de (after #1116, #1122 and #1124) produce identical plans. 73b88260
(#1121, the negative-FIT materiality threshold) is the first commit that holds.

The feed-in forecast that morning went negative from 11:30 to 14:30 at -0.5 to
-1.7c. Before #1121 any non-positive slot opened the negative-FIT window, and an
open window drops the ordinary export value test, so the planner pre-discharged
at whatever positive price was on offer. #1121 opens the window only when the
spill it would cost reaches $0.25 for the whole window. Here it was $0.03-0.16
(captured) and $0.003-0.06 (live_like) with the rebuilt Solcast forecast, and at
most $0.195 with measured solar.

## Sensitivity

With every negative feed-in price doubled (-1 to -3.5c), the `captured` arm
reopens the window at 07:35 (spill $0.32) and 09:30 ($0.26) and exports at 6.7c
and 4.5c to the 5% floor. Tripled, it exports at 07:35, 09:25 and 09:30, with
plans identical to the 11 Oct code. The `live_like` arm stays shut at both
scales (spill at most $0.16), because its load history absorbs more of the
surplus. So the fix is a size threshold, not a price test: on a morning whose
negative window is worth $0.25 or more, export at a few cents down to the floor
is still what the planner does, by design of #1103.

## Caveats

- Solar: the recorder drops Solcast's `detailedForecast`, so `solcast_today` is
  rebuilt from `sensor.solcast_pv_forecast_power_now` history (P50/P10/P90 every
  5 minutes, averaged per 30 minutes). Points after the 09:36 Solcast poll carry
  that poll's forecast; the day total moved from 44.18 to 43.99 kWh. The rebuilt
  series totals 44.0 kWh; measured was 41.7 kWh.
- Prices: Amber's `forecast` attribute (four decimals); `detailedForecast` is not
  retained.
- Load: neither arm has the house's real 28-day profile. `captured` forecasts
  from the recent-load figure (about 0.6 kW flat), which overstates midday
  surplus; `live_like` uses the harness's fixed weekday/weekend tables.
- Configuration is the LocalShift number, switch and select entities as recorded
  at each instant. `export_price_margin` (0.10) is an option with no entity and
  is assumed.
- Each replay is a fresh cycle: the negative-FIT window's open/held state from
  the previous cycle is not carried over.
