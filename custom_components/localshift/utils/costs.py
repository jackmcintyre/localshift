"""Cost tracking functionality for energy costs and savings."""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from ..const import BATTERY_CAPACITY_KWH

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..coordinator.data import CoordinatorData

_LOGGER = logging.getLogger(__name__)

# Deadband (kW) below which a power flow is treated as idle/noise. Matches the
# 0.1 kW threshold used elsewhere for "charging"/"exporting" (binary_sensor.py,
# engine/excess_solar_signals.py).
_POWER_DEADBAND_KW = 0.1
# SOC headroom (%) below which the battery is treated as "full" for the purpose
# of the export-leak metric. Exporting while above this is not counted as a leak.
_BATTERY_FULL_SOC_PCT = 99.0

# Day-to-date totals on CoordinatorData that are snapshotted across a restart
# (Issue #1102). Every one is reporting only and is cleared by
# reset_daily_accumulators. target_reached_today is deliberately absent: it
# feeds the pre-charge price calculation, so restoring it would change control
# behaviour after a restart.
#
# The dollar totals are signed. Each is a non-negative power multiplied by a
# raw price, and prices are stored unclamped (state/reader.py): the buy price
# and the feed-in price both go negative in this system, so any of the four
# can legitimately finish a day below zero.
DAILY_DOLLAR_FIELDS: tuple[str, ...] = (
    "grid_import_cost",
    "grid_export_revenue",
    "battery_savings",
    "battery_charge_cost",
)
# The kWh totals can never be negative: _accumulate_energy_kwh is their only
# writer and every amount it adds is max(power, 0) / 60 behind a positive
# deadband, a min() of two such amounts, or an SOC delta gated on > 0.
DAILY_KWH_FIELDS: tuple[str, ...] = (
    "grid_import_kwh_today",
    "grid_export_kwh_today",
    "solar_kwh_today",
    "grid_to_battery_kwh_today",
    "soc_gain_during_grid_charge_kwh_today",
    "export_while_battery_not_full_kwh_today",
)
DAILY_ACCUMULATOR_FIELDS: tuple[str, ...] = DAILY_DOLLAR_FIELDS + DAILY_KWH_FIELDS

# Sanity bounds on a restored total. They exist to catch a corrupt or
# hand-edited snapshot (1e300, a value in the wrong unit), not to police a
# real day, so both are far beyond anything a house can do:
#   - 5,000 kWh in a day is about 208 kW held for 24 hours. A 100 A
#     three-phase supply, the largest residential connection, flat out all
#     day is about 1,660 kWh.
#   - $100,000 is that 5,000 kWh at $20/kWh, about the NEM wholesale market
#     price cap, i.e. three times the largest supply's energy bought at the
#     cap for every minute of the day. Dollar totals are signed, so the bound
#     applies both ways (the market floor is -$1/kWh, well inside it).
# A total outside its bound rejects the whole snapshot, like any other
# malformed value.
MAX_DAILY_TOTAL_KWH: float = 5_000.0
MAX_DAILY_TOTAL_DOLLARS: float = 100_000.0

# (field, lowest, highest) accepted on restore: dollars signed, kWh not.
_TOTAL_BOUNDS: tuple[tuple[str, float, float], ...] = (
    *(
        (key, -MAX_DAILY_TOTAL_DOLLARS, MAX_DAILY_TOTAL_DOLLARS)
        for key in DAILY_DOLLAR_FIELDS
    ),
    *((key, 0.0, MAX_DAILY_TOTAL_KWH) for key in DAILY_KWH_FIELDS),
)


class CostTracker:
    """Tracks energy costs and savings over time."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the cost tracker."""
        self.hass = hass
        # Last-seen SOC (%), used to convert SOC deltas into kWh for the
        # grid-charge-efficiency metric (Issue #868). None until the first
        # sample is seen, so the first interval's gain is skipped (no baseline).
        self._last_soc_pct: float | None = None
        # Local date the in-memory totals belong to (Issue #1102). The saved
        # snapshot is stamped with this rather than the wall clock, so a save
        # that lands just after midnight but ahead of the reset is still dated
        # yesterday and is discarded on restore.
        self._totals_date: date = dt_util.now().date()

    @property
    def totals_date(self) -> date:
        """Local date the day-to-date totals belong to."""
        return self._totals_date

    def snapshot_daily_totals(self, data: CoordinatorData) -> dict[str, Any]:
        """Return the day's totals, dated, for persistence (Issue #1102)."""
        return {
            "date": self._totals_date.isoformat(),
            "totals": {
                key: float(getattr(data, key)) for key in DAILY_ACCUMULATOR_FIELDS
            },
        }

    def restore_daily_totals(self, data: CoordinatorData, snapshot: Any) -> bool:
        """Restore the day's totals from a snapshot taken before a restart.

        Applied only when the snapshot is dated today (local date); otherwise
        the totals stay at zero, so a previous day's figures can never leak
        into today. The snapshot is validated whole before any field is
        written, so a bad one cannot leave a partial restore behind.

        Returns:
            True if the totals were restored, False if the snapshot belongs to
            another day.

        Raises:
            ValueError: the snapshot is malformed.

        """
        if not isinstance(snapshot, dict):
            raise ValueError(f"expected a dict, got {type(snapshot).__name__}")
        saved_date = snapshot.get("date")
        if not isinstance(saved_date, str):
            raise ValueError(f"missing or non-string date: {saved_date!r}")
        snapshot_date = date.fromisoformat(saved_date)
        totals = snapshot.get("totals")
        if not isinstance(totals, dict):
            raise ValueError("missing totals")

        # A total introduced after the snapshot was written restores as zero.
        # Keys this version does not know, here or at the top level, are
        # ignored: only DAILY_ACCUMULATOR_FIELDS are ever read or written.
        values: dict[str, float] = {}
        for key, low, high in _TOTAL_BOUNDS:
            value = totals.get(key, 0.0)
            # The range test also rejects NaN (every comparison is False) and
            # both infinities, and is safe for an int too large for a float.
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not low <= value <= high
            ):
                raise ValueError(f"invalid {key}: {value!r}")
            values[key] = float(value)

        today = dt_util.now().date()
        if snapshot_date != today:
            return False

        for key, value in values.items():
            setattr(data, key, value)
        self._totals_date = today
        # Same reasoning as the midnight reset (Issue #899): the first sample
        # after a restart sets a fresh SOC baseline, so SOC that moved while
        # Home Assistant was down is not booked to one tick.
        self._last_soc_pct = None
        return True

    def accumulate_costs(self, data: CoordinatorData) -> None:
        """Accumulate per-minute energy costs from current power and price.

        Replaces YAML A16 (localshift_cost_accumulator).
        Formula: power_kW × price_$/kWh / 60 = $/min
        """
        # Issue #1102: totals restored seconds before midnight would outlive
        # the day if the midnight timer was armed too late to fire. Start the
        # new day from zero here rather than keep adding to yesterday's.
        # Totals only: target_reached_today is a control input and is not
        # this guard's to clear (computation_engine clears it on date change).
        if dt_util.now().date() != self._totals_date:
            self._reset_daily_totals(data)

        # Grid import cost: positive grid power × buy price
        import_cost = max(data.grid_power_kw, 0.0) * data.general_price / 60
        data.grid_import_cost += import_cost

        # Grid export revenue: negative grid power (export) × sell price
        export_revenue = max(-data.grid_power_kw, 0.0) * data.feed_in_price / 60
        data.grid_export_revenue += export_revenue

        # Tesla/Teslemetry sign convention: battery_power_kw > 0 = DISCHARGING,
        # battery_power_kw < 0 = CHARGING (matches binary_sensor.py:228 and
        # engine/excess_solar_signals.py:173 which use < -0.1 for "charging").
        # Battery savings: discharge (positive power) × buy price (avoided purchase)
        savings = max(data.battery_power_kw, 0.0) * data.general_price / 60
        data.battery_savings += savings

        # Battery charge cost: charge (negative power) × buy price
        charge_cost = max(-data.battery_power_kw, 0.0) * data.general_price / 60
        data.battery_charge_cost += charge_cost

        # Daily energy accounting for the #868 performance metrics.
        self._accumulate_energy_kwh(data)

    def _accumulate_energy_kwh(self, data: CoordinatorData) -> None:
        """Accumulate per-minute kWh for the Issue #868 performance metrics.

        Sign conventions (see accumulate_costs above):
          grid_power_kw > 0    => importing from grid
          grid_power_kw < 0    => exporting to grid
          battery_power_kw < 0 => charging
          battery_power_kw > 0 => discharging

        All energy is power_kW / 60 (one minute of integration). The SOC-gain term
        is a daily proxy: it converts the SOC delta observed since the previous
        sample into kWh via BATTERY_CAPACITY_KWH, attributing it to grid charging
        only while the battery is being charged from the grid. It is an
        approximation (it ignores simultaneous solar charging) used only for the
        display-only grid_charge_efficiency ratio.
        """
        # Total grid import / export energy this minute.
        import_kw = max(data.grid_power_kw, 0.0)
        export_kw = max(-data.grid_power_kw, 0.0)
        if import_kw >= _POWER_DEADBAND_KW:
            data.grid_import_kwh_today += import_kw / 60
        if export_kw >= _POWER_DEADBAND_KW:
            data.grid_export_kwh_today += export_kw / 60

        # Total solar generation this minute (Issue #971 — feeds the daily
        # summary, replacing the retired sensor.solar_production_energy_daily
        # YAML utility meter). Same deadband as the grid accumulators above.
        solar_kw = max(data.solar_power_kw, 0.0)
        if solar_kw >= _POWER_DEADBAND_KW:
            data.solar_kwh_today += solar_kw / 60

        # Grid energy flowing into the battery: only when both importing from the
        # grid AND charging the battery. The grid-to-battery rate cannot exceed
        # either flow, so take the min of the two.
        charge_kw = max(-data.battery_power_kw, 0.0)
        charging_from_grid = (
            import_kw >= _POWER_DEADBAND_KW and charge_kw >= _POWER_DEADBAND_KW
        )
        if charging_from_grid:
            grid_to_battery_kw = min(charge_kw, import_kw)
            data.grid_to_battery_kwh_today += grid_to_battery_kw / 60

        # SOC gain (kWh) observed since the previous sample, attributed to grid
        # charging when we are charging from the grid. Skip the first sample
        # (no baseline) and ignore SOC drops.
        if self._last_soc_pct is not None and charging_from_grid:
            soc_delta_pct = data.soc - self._last_soc_pct
            if soc_delta_pct > 0:
                data.soc_gain_during_grid_charge_kwh_today += (
                    soc_delta_pct / 100.0 * BATTERY_CAPACITY_KWH
                )
        self._last_soc_pct = data.soc

        # Export that occurred while the battery still had room to absorb it.
        # That exported energy could have been self-consumed into the battery,
        # so it counts toward the export-loss ratio.
        if export_kw >= _POWER_DEADBAND_KW and data.soc < _BATTERY_FULL_SOC_PCT:
            data.export_while_battery_not_full_kwh_today += export_kw / 60

    def reset_daily_accumulators(self, data: CoordinatorData) -> None:
        """Reset daily cost and energy accumulators and the target flag.

        Replaces YAML A12 (localshift_reset_target_reached). This is the
        midnight reset: the day's totals and the target latch together.
        """
        data.target_reached_today = False
        self._reset_daily_totals(data)

    def _reset_daily_totals(self, data: CoordinatorData) -> None:
        """Zero the ten day-to-date totals and start a new totals day.

        Reporting state only. It must never write target_reached_today or any
        other control input: accumulate_costs calls it from its date guard.
        """
        data.grid_import_cost = 0.0
        data.grid_export_revenue = 0.0
        data.battery_savings = 0.0
        data.battery_charge_cost = 0.0
        self._reset_energy_accumulators(data)
        # Issue #899: also clear the SOC baseline so the first grid-charge
        # sample after midnight establishes a fresh baseline. Without this, a
        # grid charge spanning midnight leaves _last_soc_pct at the
        # pre-midnight value, and the next sample attributes the entire
        # overnight gain to the new day's grid-charge-efficiency metric.
        self._last_soc_pct = None
        self._totals_date = dt_util.now().date()

    def _reset_energy_accumulators(self, data: CoordinatorData) -> None:
        """Reset the Issue #868 daily energy accumulators."""
        data.grid_import_kwh_today = 0.0
        data.grid_export_kwh_today = 0.0
        data.solar_kwh_today = 0.0
        data.grid_to_battery_kwh_today = 0.0
        data.soc_gain_during_grid_charge_kwh_today = 0.0
        data.export_while_battery_not_full_kwh_today = 0.0
