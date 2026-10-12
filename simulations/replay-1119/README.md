# Slot-0 price replay — 2026-10-12 (issue #1119)

Real optimizer inputs captured from Home Assistant history at seven instants
during the 2026-10-12 price spike, replayed through `ComputationEngine` twice:
`before` prices slot 0 the way main did, `after` is this branch. Inputs are
byte-identical across the two.

```
uv run scripts/replay_slot0_price.py replay
uv run scripts/replay_slot0_price.py replay --optimization-mode self_consumption
```

## What the trace showed

Slot 0's buy price was the general-forecast entry covering "now". Its sell
price came from `get_price_for_slot_or_none`, which scans a fixed 15-minute
window — a leftover from when every slot was 15 minutes wide. For a 5-minute
slot that is the mean of the current interval and the next two forecasts:

| Instant | Feed-in entries in the window | Mean = plan slot-0 sell | Confirmed |
|---|---|---|---|
| 15:15 | 24.2284, 2.2574, 1.9932 | 9.49 | 24.23 |
| 16:10 | 0.6762, 2.6846, 2.2168 | 1.86 | 0.68 |
| 16:25 | 0.3572, 4.1873, 3.5148 | 2.69 | 0.36 |
| 16:30 | 10.5225, 1.4503, 1.5472 | 4.51 | 10.52 |

The forecast entry covering the interval was right every time; it was diluted.

Two corrections to the issue as filed. The confirmed 16:10 price was $0.68, not
about $10.50 — $10.52 was the 16:05 interval, and the sensor still showed it
for the 22 seconds before 16:10 was confirmed. And confirmed prices really did
alternate between about $10.50 and under $1 from one interval to the next
(16:05 $10.52, 16:10 $0.68, 16:15 $10.41, 16:20 $0.21, 16:25 $0.36, 16:30
$10.52), so leaving export at 16:10 and 16:20 was the right call on price.

The operator had switched optimization mode to `arbitrage` at 15:09 (back to
`self_consumption` at 18:48). The captures record the mode in force.

## Slot-0 decisions, mode as captured (arbitrage)

| Instant | Committed mode | Sell confirmed? | Confirmed sell | Before: sell / action | After: sell / action |
|---|---|---|---|---|---|
| 15:15:30 | self_consumption | yes | 24.2284 | 9.4930 / export | 24.2284 / export |
| 15:20:10 | spike_discharge | no (still 15:15's price) | 0.7052 at 15:20:23 | 9.3930 / export | 24.2284 / hold |
| 15:30:40 | proactive_export | yes | 10.4839 | 5.1441 / export | 10.4839 / export |
| 16:10:40 | self_consumption | yes | 0.6762 | 1.8592 / hold | 0.6762 / hold |
| 16:25:50 | self_consumption | yes | 0.3572 | 2.6864 / export | 0.3572 / hold |
| 16:30:10 | proactive_export | no (still 16:25's price) | 10.5225 at 16:30:26 | 2.3507 / hold | 0.3572 / hold |
| 16:30:40 | proactive_export | yes | 10.5225 | 4.5067 / export | 10.5225 / export |

16:25:50 is the live failure: main planned an export into a 36c interval and
the system entered PROACTIVE_EXPORT at 16:25:56. 15:20:10 is the unconfirmed
case: the sensor still carried the previous interval's $24.23.

## Same inputs under self_consumption

| Instant | Confirmed sell | Before: sell / action | After: sell / action |
|---|---|---|---|
| 15:15:30 | 24.2284 | 9.4930 / hold | 24.2284 / hold |
| 15:20:10 | (unconfirmed) | 9.3930 / hold | 24.2284 / hold |
| 15:30:40 | 10.4839 | 5.1441 / hold | 10.4839 / hold |
| 16:10:40 | 0.6762 | 1.8592 / export | 0.6762 / hold |
| 16:25:50 | 0.3572 | 2.6864 / export | 0.3572 / hold |
| 16:30:10 | (unconfirmed) | 2.3507 / export | 0.3572 / hold |
| 16:30:40 | 10.5225 | 4.5067 / hold | 10.5225 / hold |

In this mode export is admitted only when `sell >= buy + export_price_margin`
within the same slot. Amber's feed-in is always below its general price for the
same interval, so with slot 0 priced honestly that test cannot pass. On main it
passed only through the averaging: when the current interval was cheap and the
next two were forecast dear — the three "before" exports above, all into
intervals under 70c. This branch removes those. It does not make
self_consumption export into a real spike; main did not either.

## Caveats

- Solar is measured, not forecast (see `scripts/export_replay_days.py`).
- Prices come from the sensors' recorded `forecast` attribute, rounded to four
  decimals. `detailedForecast`, which the integration reads live, is not
  retained by the recorder.
- `price_spike` is not captured; the replay runs with it false.
