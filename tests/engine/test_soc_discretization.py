"""SOC discretisation must be fine enough to resolve one pre-charge slot.

Live 2026-08-25. The planner entered the demand window at 91.41% against a 95% target
and deferred the head of its own pre-charge (first charge at 12:45 rather than 12:30),
while its own terminal table priced a 90.8% DW entry at $677 and a 96.3% entry at $0.

It was not paying through a penalty. At the shipped ``soc_bins = 50`` each bin spans
1.84 SOC points while a single 5-minute boost slot moves ~2.84 points — 1.5 bins — so the
value function could not distinguish "charge now" from "charge in 15 minutes". Backward
induction transitions from BIN CENTRES and ``_forward_reconstruct`` transitions from the
CONTINUOUS soc, so the residual accumulated downward across the charging run and the
realised plan was never the trajectory whose value justified it.

Every economic explanation was falsified first (see the fix commit): the plan is
byte-identical with futile cycling off, min_cycle_saving 0, switching_penalty 0 and the
shortfall penalty at 50x. Only the grid resolution moves it.
"""

from __future__ import annotations

from custom_components.localshift.engine.core import DPPlanner
from custom_components.localshift.engine.types import (
    OptimizerConfig,
    OptimizerInputs,
    SlotContext,
)

# The live 12:31 horizon, from sensor.localshift_optimizer_plan_detailed:
# (slot, hh:mm, interval_min, buy, sell, solar_kwh, consumption_kwh)
LIVE_HORIZON = [
    (0, "12:30", 5, 0.16, 0.07, 0.035, 0.127),
    (1, "12:35", 5, 0.16, 0.07, 0.036, 0.127),
    (2, "12:40", 5, 0.16, 0.07, 0.036, 0.127),
    (3, "12:45", 5, 0.16, 0.07, 0.036, 0.127),
    (4, "12:50", 5, 0.16, 0.07, 0.036, 0.127),
    (5, "12:55", 5, 0.16, 0.07, 0.037, 0.127),
    (6, "13:00", 5, 0.15, 0.07, 0.043, 0.127),
    (7, "13:05", 5, 0.15, 0.07, 0.114, 0.127),
    (8, "13:10", 5, 0.15, 0.07, 0.114, 0.127),
    (9, "13:15", 5, 0.15, 0.07, 0.114, 0.127),
    (10, "13:20", 5, 0.15, 0.0667, 0.114, 0.127),
    (11, "13:25", 5, 0.15, 0.065, 0.116, 0.127),
    (12, "13:30", 30, 0.15, 0.06, 0.638, 0.766),
    (13, "14:00", 30, 0.16, 0.07, 0.552, 0.740),
    (14, "14:30", 30, 0.16, 0.07, 0.501, 0.721),
    (15, "15:00", 30, 0.15, 0.08, 0.428, 0.609),
    (16, "15:30", 30, 0.16, 0.09, 0.295, 0.570),
    (17, "16:00", 30, 0.16, 0.12, 0.132, 0.590),
    (18, "16:30", 30, 0.16, 0.12, 0.041, 0.449),
    (19, "17:00", 30, 0.17, 0.14, 0.008, 0.749),
    (20, "17:30", 30, 0.18, 0.15, 0.0, 0.749),
    (21, "18:00", 30, 0.18, 0.15, 0.0, 0.751),
    (22, "18:30", 30, 0.19, 0.16, 0.0, 0.751),
    (23, "19:00", 30, 0.19, 0.15, 0.0, 0.650),
    (24, "19:30", 30, 0.18, 0.14, 0.0, 0.650),
    (25, "20:00", 30, 0.19, 0.16, 0.0, 0.649),
    (26, "20:30", 30, 0.20, 0.16, 0.0, 0.649),
    (27, "21:00", 30, 0.19, 0.12, 0.0, 0.732),
    (28, "21:30", 30, 0.19, 0.11, 0.0, 0.732),
    (29, "22:00", 30, 0.18, 0.10, 0.0, 0.497),
    (30, "22:30", 30, 0.17, 0.09, 0.0, 0.497),
    (31, "23:00", 30, 0.16, 0.09, 0.0, 0.343),
    (32, "23:30", 30, 0.15, 0.08, 0.0, 0.343),
    (33, "00:00", 30, 0.15, 0.08, 0.0, 0.253),
    (34, "00:30", 30, 0.15, 0.08, 0.0, 0.253),
    (35, "01:00", 30, 0.16, 0.09, 0.0, 0.242),
    (36, "01:30", 30, 0.16, 0.09, 0.0, 0.242),
    (37, "02:00", 30, 0.16, 0.09, 0.0, 0.241),
    (38, "02:30", 30, 0.17, 0.09, 0.0, 0.242),
    (39, "03:00", 30, 0.17, 0.09, 0.0, 0.439),
    (40, "03:30", 30, 0.17, 0.09, 0.0, 0.439),
    (41, "04:00", 30, 0.20, 0.20, 0.0, 0.381),
    (42, "04:30", 30, 0.20, 0.20, 0.0, 0.381),
    (43, "05:00", 30, 0.20, 0.20, 0.0, 0.376),
    (44, "05:30", 30, 0.20, 0.20, 0.0, 0.375),
    (45, "06:00", 30, 0.20, 0.20, 0.0, 0.339),
    (46, "06:30", 30, 0.20, 0.20, 0.022, 0.339),
    (47, "07:00", 30, 0.20, 0.20, 0.086, 0.733),
    (48, "07:30", 30, 0.20, 0.20, 0.233, 0.732),
    (49, "08:00", 30, 0.20, 0.20, 0.417, 0.574),
    (50, "08:30", 30, 0.20, 0.20, 0.561, 0.574),
    (51, "09:00", 30, 0.20, 0.20, 0.619, 0.654),
    (52, "09:30", 30, 0.20, 0.20, 0.673, 0.655),
    (53, "10:00", 30, 0.20, 0.20, 0.668, 0.884),
    (54, "10:30", 30, 0.20, 0.20, 0.544, 0.885),
    (55, "11:00", 30, 0.20, 0.20, 0.489, 1.048),
    (56, "11:30", 30, 0.20, 0.20, 0.515, 1.047),
    (57, "12:00", 30, 0.20, 0.20, 0.525, 1.015),
    (58, "12:30", 30, 0.20, 0.20, 0.523, 0.0),
]

DW_ENTRY_IDX = 15
DW_END_IDX = 27
LIVE_INITIAL_SOC = 21.986
LIVE_TARGET_PCT = 95.0

# One 5-minute boost slot moves the battery about this far. The bin width must be
# comfortably under it or the DP cannot price a single slot of pre-charge.
BOOST_SOC_GAIN_PER_5MIN_SLOT = 2.84


def _slots(buy_override: float | None = None) -> list[SlotContext]:
    out = []
    for idx, hhmm, interval, buy, sell, solar, cons in LIVE_HORIZON:
        day = "2026-08-25" if idx <= 32 else "2026-08-26"
        out.append(
            SlotContext(
                slot_index=idx,
                timestamp_iso=f"{day}T{hhmm}:01+10:00",
                slot_interval_minutes=interval,
                buy_price=buy if buy_override is None else buy_override,
                sell_price=sell,
                solar_kwh=solar,
                consumption_kwh=cons,
                is_demand_window_entry=(idx == DW_ENTRY_IDX),
                is_demand_window_slot=(DW_ENTRY_IDX <= idx < DW_END_IDX),
                price_source="5min" if interval == 5 else "30min",
            )
        )
    return out


def _config(**overrides) -> OptimizerConfig:
    config = OptimizerConfig(
        demand_window_target_soc_pct=LIVE_TARGET_PCT,
        min_soc_pct=10.0,
        switching_penalty=0.08,
        min_cycle_saving=0.25,
        max_precharge_price=0.20,
        optimization_mode="self_consumption",
        forecast_horizon_hours=24.5,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _plan(config: OptimizerConfig, soc: float = LIVE_INITIAL_SOC, slots=None):
    return DPPlanner().plan(
        OptimizerInputs(
            cycle_id="disc",
            initial_soc_pct=soc,
            slots=slots if slots is not None else _slots(),
            config=config,
        )
    )


def _first_charge_idx(result) -> int | None:
    for d in result.decisions:
        if "charge_grid" in str(getattr(d, "action", "")):
            return getattr(d, "slot_index", None)
    return None


def _overnight_grid_charge_kwh(result) -> float:
    """Grid charge in the post-DW overnight block — the #800 sawtooth signature."""
    return sum(
        float(getattr(d, "grid_import_kwh", 0.0) or 0.0)
        for d in result.decisions
        if 27 <= getattr(d, "slot_index", -1) <= 45
        and "charge_grid" in str(getattr(d, "action", ""))
    )


class TestSocDiscretization:
    """The grid must resolve a single pre-charge slot, or the DP cannot plan one."""

    def test_bin_width_is_finer_than_one_boost_slot(self) -> None:
        """The invariant, stated directly rather than via an outcome.

        This is what actually broke: at 50 bins one boost slot spanned 1.5 bins, so
        "charge now" and "charge next slot" landed on indistinguishable states.
        """
        config = OptimizerConfig()
        bin_width = (100.0 - config.min_soc_pct) / (config.soc_bins - 1)

        assert bin_width < BOOST_SOC_GAIN_PER_5MIN_SLOT, (
            f"bin width {bin_width:.2f}pp exceeds one 5-minute boost slot "
            f"({BOOST_SOC_GAIN_PER_5MIN_SLOT}pp) — the DP cannot price a single slot "
            "of pre-charge and will defer it"
        )

    def test_live_horizon_reaches_target_and_starts_immediately(self) -> None:
        """The incident, replayed. At the shipped default it must now come out right.

        Issue #905 raised the taper knee from 80% to 90% (hardware holds 5 kW flat
        through 88% SOC). With the corrected knee the DP reaches ~94.7% at DW entry —
        within one bin of target. The prior assert ``== 0.0`` was brittle against the
        discretisation noise that is now smaller but non-zero.
        """
        result = _plan(_config())

        assert result.terminal_shortfall_pct < 0.5
        assert result.dw_entry_soc_pct >= LIVE_TARGET_PCT - 0.5
        # 12:30, not the 12:45 the coarse grid deferred to.
        assert _first_charge_idx(result) == 0

    def test_the_coarse_grid_still_reproduces_the_failure(self) -> None:
        """Pins the cause, so a future reader can see this was the grid and nothing else.

        If this ever stops failing at 50 bins, the mechanism has changed and the default
        above should be re-derived rather than trusted.

        Issue #905's raised knee narrows the fine-grid shortfall but the coarse grid
        still undershoots by a clearly larger margin.
        """
        result = _plan(_config(soc_bins=50))

        assert result.terminal_shortfall_pct > 2.5
        assert result.dw_entry_soc_pct < 92.5
        # Issue #905 raised the knee: first charge shifted one slot later (4 vs 3)
        # because the finer model buys the same energy but starts charging later.
        assert _first_charge_idx(result) == 4

    def test_target_is_met_regardless_of_price_spread(self) -> None:
        """A flat-price day has nothing to arbitrage, but the DW target is not arbitrage.

        The coarse grid undershot here too (90.84%), which is the same defect wearing a
        different hat — the target is a commitment, not an opportunity.

        Issue #905: the raised taper knee brings the fine-grid result within one bin of
        target; the assert is tolerant of that discretisation noise.
        """
        result = _plan(_config(), slots=_slots(buy_override=0.17))

        assert result.terminal_shortfall_pct < 0.5
        assert result.dw_entry_soc_pct >= LIVE_TARGET_PCT - 0.5

    def test_no_overnight_sawtooth_is_reopened(self) -> None:
        """The #800 guard: a finer grid must not buy the target with overnight cycling."""
        for slots in (_slots(), _slots(buy_override=0.17)):
            result = _plan(_config(), slots=slots)
            assert _overnight_grid_charge_kwh(result) == 0.0

    def test_a_full_battery_does_not_invent_charging(self) -> None:
        """Resolution must not become an excuse to charge — nothing to do, do nothing."""
        result = _plan(_config(), soc=97.0)

        assert _overnight_grid_charge_kwh(result) == 0.0
        assert result.terminal_shortfall_pct == 0.0
