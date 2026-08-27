"""Unit tests for ha_data_sweep module.

Tests the pure analysis functions of the recorder-DB sweep: series parsing,
step lookup, forecast bucketing, price capture, daily meter deltas, mode
churn stats, charge timing, and decision-outcome distributions.
"""

from datetime import UTC, datetime
from pathlib import Path

from scripts.ha_data_sweep import (
    AEST,
    bucket_pairs,
    charge_timing,
    daily_deltas,
    decision_stats,
    gap_windows,
    merge_buckets,
    mode_stats,
    parse_series,
    pctl,
    price_capture,
    render_markdown,
    step_value,
)

UTC = UTC


class TestParseSeries:
    """Numeric filtering of raw recorder rows."""

    def test_keeps_floats_and_drops_non_numeric(self):
        rows = [(100, "1.5"), (200, "unavailable"), (300, "2"), (400, None)]
        assert parse_series(rows) == [(100, 1.5), (300, 2.0)]

    def test_empty(self):
        assert parse_series([]) == []


class TestStepValue:
    """State-machine value lookup at time t."""

    def test_returns_latest_at_or_before(self):
        s = [(10, 1.0), (20, 2.0), (30, 3.0)]
        assert step_value(s, 25) == 2.0

    def test_exact_timestamp(self):
        s = [(10, 1.0), (20, 2.0)]
        assert step_value(s, 20) == 2.0

    def test_before_first_is_none(self):
        assert step_value([(10, 1.0)], 5) is None

    def test_after_last_holds(self):
        assert step_value([(10, 1.0)], 999) == 1.0

    def test_empty_series(self):
        assert step_value([], 100) is None


class TestMergeBuckets:
    """15-min forecast/actual bucket merge with unit correction."""

    def test_buckets_and_scales_actual_kw_to_w(self):
        # 900s buckets; fc already W, ac in kW.
        fc = [(0, 500.0), (900, 0.0)]
        ac = [(10, 0.4), (910, 0.0)]  # 0.4 kW = 400 W
        out = merge_buckets(fc, ac, bucket_secs=900, actual_scale=1000.0)
        assert out[0] == {"f": 500.0, "a": 400.0}
        assert out[1] == {"f": 0.0, "a": 0.0}

    def test_last_write_wins_within_bucket(self):
        fc = [(0, 100.0), (300, 200.0)]
        out = merge_buckets(fc, [], bucket_secs=900)
        assert out[0]["f"] == 200.0

    def test_unmatched_sides_omitted(self):
        out = merge_buckets([(0, 100.0)], [(2000, 1.0)], bucket_secs=900)
        assert out == {0: {"f": 100.0}, 2: {"a": 1000.0}}


class TestBucketPairs:
    """Solar bias and daily energy from merged buckets."""

    def test_bias_and_day_energy(self):
        # Two 15-min buckets at 06:00/06:15 AEST 20 Aug, one sub-threshold.
        t0 = datetime(2026, 8, 20, 6, tzinfo=AEST).timestamp()
        buckets = {
            int(t0 // 900): {"f": 800.0, "a": 600.0},
            int((t0 + 900) // 900): {"f": 600.0, "a": 600.0},
            int((t0 + 1800) // 900): {"f": 50.0, "a": 50.0},  # below min_w
        }
        rep = bucket_pairs(buckets, AEST, min_w=100.0)
        assert rep["n_buckets"] == 2
        assert rep["bias_mean_w"] == 100.0  # (200 + 0) / 2
        hour = rep["bias_by_hour"][6]
        assert hour["n"] == 2
        assert hour["mean_w"] == 100.0
        fc_kwh, ac_kwh = rep["day_energy"]["08-20"]
        # day energy integrates ALL buckets (incl. below-threshold): 3 x 0.25h
        assert fc_kwh == (800 + 600 + 50) / 4 / 1000 * 1.0
        assert ac_kwh == (600 + 600 + 50) / 4 / 1000 * 1.0

    def test_energy_ratio_guard_zero_actual(self):
        rep = bucket_pairs({}, AEST)
        assert rep["energy_ratio"] == 0.0


class TestPriceCapture:
    """Energy-weighted import price vs import-window average."""

    def _gp(self):
        # price 0.10 at t=0, 0.20 at t=300
        return [(0, 0.10), (300, 0.20)]

    def test_import_export_split_and_capture(self):
        gpow = [(0, 2.0), (300, -1.0), (600, 0.0)]
        cap = price_capture(gpow, self._gp(), [(0, 0.02)], step=300)
        # import 2kW for 5min = 1/6 kWh at 0.10; export 1kW for 5min at FiT 0.02
        assert abs(cap["import_kwh"] - 2.0 / 12) < 1e-9
        assert abs(cap["export_kwh"] - 1.0 / 12) < 1e-9
        assert abs(cap["avg_paid"] - 0.10) < 1e-9
        assert abs(cap["import_window_avg_price"] - 0.10) < 1e-9
        assert abs(cap["capture_ratio"] - 1.0) < 1e-9
        assert abs(cap["export_revenue"] - (1.0 / 12) * 0.02) < 1e-9

    def test_steps_without_price_skipped(self):
        gpow = [(0, 2.0)]
        cap = price_capture(gpow, [], [], step=300)
        assert cap["import_kwh"] == 0.0
        assert cap["capture_ratio"] == 0.0


class TestDailyDeltas:
    """Per-local-day max-min on cumulative meters."""

    def test_max_minus_min_per_day(self):
        # Day 1 (AEST): 0->5->3 ; Day 2: 0->2  (daily reset)
        d1 = datetime(2026, 8, 20, 6, tzinfo=AEST).timestamp()
        s = [
            (d1, 0.0),
            (d1 + 3600, 5.0),
            (d1 + 7200, 3.0),
            (d1 + 86400, 0.0),
            (d1 + 90000, 2.0),
        ]
        assert daily_deltas(s, AEST) == {"08-20": 5.0, "08-21": 2.0}

    def test_single_observation_day(self):
        s = [(datetime(2026, 8, 20, 6, tzinfo=AEST).timestamp(), 4.0)]
        assert daily_deltas(s, AEST) == {"08-20": 0.0}


class TestModeStats:
    """Transition counts, dwell, destinations, hour histogram."""

    def test_transitions_and_dwell(self):
        base = datetime(2026, 8, 20, 12, tzinfo=AEST).timestamp()
        sm = [
            (base, "self_consumption"),
            (base + 600, "boost_charging"),
            (base + 600 + 300, "self_consumption"),
            (base + 600 + 300 + 1200, "grid_charging"),
        ]
        st = mode_stats(sm, AEST)
        assert st["n_changes"] == 3
        assert st["dest_counts"] == {
            "boost_charging": 1,
            "self_consumption": 1,
            "grid_charging": 1,
        }
        dwells = sorted(st["dwells_min"])
        assert dwells == [5.0, 10.0, 20.0]
        assert st["dwell_median_min"] == 10.0
        assert st["dwell_p10_min"] == 5.0
        assert st["pairs"][("self_consumption", "boost_charging")] == 1
        assert st["pairs"][("boost_charging", "self_consumption")] == 1
        assert st["pairs"][("self_consumption", "grid_charging")] == 1
        assert st["hour_hist"][12] == 3

    def test_no_changes(self):
        sm = [(100, "hold"), (200, "hold")]
        st = mode_stats(sm, AEST)
        assert st["n_changes"] == 0
        assert st["dwell_median_min"] is None


class TestChargeTiming:
    """Charging energy, price paid, bulk hours, cheapest hours for a day."""

    def _run(self):
        d0 = datetime(2026, 8, 20, tzinfo=AEST).timestamp()  # midnight AEST
        # battery: -5kW (charging) for first two 5-min steps, then 0
        batt = [(d0, -5.0), (d0 + 600, 0.0)]
        # price: 0.15 at 00:00, 0.10 at 01:00, 0.20 at 02:00
        gp = [(d0, 0.15), (d0 + 3600, 0.10), (d0 + 7200, 0.20)]
        return charge_timing(batt, gp, d0, AEST, step=300), d0

    def test_energy_and_avg_paid(self):
        ct, _ = self._run()
        # 5 kW for 10 min = 5/6 kWh at 0.15
        assert abs(ct["charged_kwh"] - 10.0 / 12) < 1e-9
        assert abs(ct["avg_paid"] - 0.15) < 1e-9

    def test_bulk_hours_and_cheapest(self):
        ct, _ = self._run()
        assert ct["bulk_hours"] == {0: 10.0 / 12}
        # up to 3 cheapest known hours, ordered by price: 1 (0.10), 0 (0.15), 2 (0.20)
        assert ct["cheapest_hours"] == [1, 0, 2]
        assert ct["day_min_price"] == 0.10
        assert ct["day_max_price"] == 0.20

    def test_no_charging(self):
        d0 = datetime(2026, 8, 20, tzinfo=AEST).timestamp()
        ct = charge_timing([(d0, 1.0)], [(d0, 0.1)], d0, AEST, step=300)
        assert ct["charged_kwh"] == 0.0
        assert ct["avg_paid"] is None


class TestDecisionStats:
    """Learning store: percentiles by mode, worst-5, completeness."""

    def _data(self):
        completed = [
            {
                "timestamp": "2026-08-20T06:00:00+10:00",
                "mode_chosen": "hold",
                "outcome_score": 0.5,
                "actual_cost_during_period": 0.10,
                "actual_import_kwh": 1.0,
                "actual_soc_change": 2.0,
            },
            {
                "timestamp": "2026-08-20T12:00:00+10:00",
                "mode_chosen": "hold",
                "outcome_score": 0.7,
                "actual_cost_during_period": 0.20,
                "actual_import_kwh": 2.0,
                "actual_soc_change": 1.0,
            },
            {
                "timestamp": "2026-08-21T06:00:00+10:00",
                "mode_chosen": "charge_grid_normal",
                "outcome_score": 0.3,
                "actual_cost_during_period": None,  # missing cost
                "actual_import_kwh": 0.0,
                "actual_soc_change": 8.0,
            },
            {  # pending-shaped record (no score) inside completed list
                "timestamp": "2026-08-21T12:00:00+10:00",
                "mode_chosen": "hold",
            },
        ]
        pending = [{"timestamp": "2026-08-27T12:00:00+10:00", "mode_chosen": "hold"}]
        return {"completed_decisions": completed, "pending_decisions": pending}

    def test_counts_and_percentiles(self):
        st = decision_stats(self._data())
        assert st["completed"] == 4
        assert st["scored"] == 3
        assert st["pending"] == 1
        hold = st["by_mode"]["hold"]
        assert hold["n"] == 2
        assert hold["median"] == 0.7
        assert hold["p10"] == 0.5
        assert hold["p90"] == 0.7
        assert st["missing_cost"] == 1

    def test_worst5(self):
        st = decision_stats(self._data())
        assert len(st["worst5"]) == 3
        assert st["worst5"][0]["mode_chosen"] == "charge_grid_normal"
        assert st["worst5"][0]["outcome_score"] == 0.3

    def test_span(self):
        st = decision_stats(self._data())
        assert st["span_first"].startswith("2026-08-20T06")
        assert st["span_last"].startswith("2026-08-21T12")


class TestPctl:
    def test_median_even(self):
        assert pctl([1.0, 2.0, 3.0, 4.0], 0.5) == 3.0  # len//2 index

    def test_median_odd(self):
        assert pctl([1.0, 2.0, 3.0], 0.5) == 2.0

    def test_bounds(self):
        assert pctl([5.0], 0.1) == 5.0
        assert pctl([5.0], 0.9) == 5.0


class TestGapWindows:
    def test_reports_long_gaps_only(self):
        s = [(0, 1.0), (100, 1.0), (7200, 1.0)]
        assert gap_windows(s, min_gap_s=3600) == [(100, 7200)]

    def test_none(self):
        assert gap_windows([(0, 1.0), (60, 1.0)], min_gap_s=3600) == []


class TestRenderMarkdown:
    def test_renders_sections(self, tmp_path: Path):
        md = render_markdown(
            days=7,
            config_dir="/tmp/ha",
            coverage={"sensor.my_home_grid_power": {"n": 100, "max_gap_min": 5.0}},
            solar={
                "n_buckets": 10,
                "bias_mean_w": 250.0,
                "energy_ratio": 1.45,
                "day_energy": {"08-26": (22.9, 12.2)},
                "bias_by_hour": {12: {"n": 4, "mean_w": 620.0}},
            },
            price={
                "import_kwh": 50.0,
                "avg_paid": 0.146,
                "import_window_avg_price": 0.140,
                "capture_ratio": 1.04,
                "export_kwh": 14.1,
                "export_revenue": 0.29,
            },
            daily_meters={
                "grid_import_kWh": {"08-25": 21.77},
                "batt_from_grid_kWh": {"08-25": 11.11},
            },
            mode={"n_changes": 105, "dwell_median_min": 10.0,
                  "dwell_p10_min": 4.0, "dest_counts": {"boost_charging": 44},
                  "hour_hist": {12: 22}},
            charge_days={"08-25": {"charged_kwh": 14.0, "avg_paid": 0.172,
                                   "cheapest_hours": [8, 9, 10]}},
            decisions={"completed": 500, "scored": 500, "pending": 1,
                       "missing_cost": 2, "by_mode": {},
                       "span_first": "2026-07-29T06", "span_last": "2026-08-27T11",
                       "worst5": []},
            gaps=[("sensor.my_home_grid_power", 100.0, 7200.0)],
        )
        assert "# HA data sweep" in md
        assert "- days: 7" in md
        assert "### Gap windows" in md
        assert "- sensor.my_home_grid_power:" in md
        assert "| 08-26 | 22.9 | 12.2 |" in md
        assert "capture ratio: **1.04**" in md
        assert "| 08-25 | 21.8 | 11.1 |" in md
        assert "105 changes" in md
        assert "| 08-25 | 14.0 | 0.172 | 8, 9, 10 |" in md
        assert "missing cost: 2/500" in md
