"""Slot schedule computation for hybrid timescale forecasts."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .utils import get_slot_duration_minutes, parse_slot_time

_LOGGER = logging.getLogger(__name__)

# Forecast slot constants
# 15-min slots throughout for consistent alignment with Solcast 30-minute periods
TOTAL_SLOTS = 96  # 24 hours × 4 slots/hour

# Issue #510 Slice 2: Amber's detailedForecast starts each interval one second
# after the boundary (the 12:30 interval starts at 12:30:01). Absorb that
# offset when deciding which interval covers "now" — but only if it really is
# boundary noise, so a genuinely mid-interval start keeps its own boundary.
#
# Issue #949: the tolerance is SCALED TO THE INTERVAL, not flat. A flat 60s
# window is ~3% of a 30-minute interval but a full 20% of a 5-minute one, and
# on the fine granularity it let a genuinely late entry masquerade as the
# current interval: slot 0 could then begin up to 59 seconds AFTER now with
# nothing at all covering the interim. The scaling keeps the window a small
# fraction of whatever interval it is applied to:
#   min(_BOUNDARY_OFFSET_TOLERANCE_S, duration_minutes * 2) seconds
# → 10s on a 5-minute interval, unchanged 60s on a 30-minute interval.
# 10s is still ~10x the documented +1s provider skew, so the offset this
# constant exists to absorb keeps being absorbed.
# If the offset were ever to exceed the window, the covering entry stops being
# recognised and slot 0 falls back to the synthetic next-interval borrow: the
# exact defect Issue #510 Slice 2 removed. That failure is loud, not silent —
# it trips the SYNTHETIC SLOT FALLBACK warning below — but it is the reason to
# keep each window comfortably above the observed +1s rather than tighten it
# to the bare minimum.
_BOUNDARY_OFFSET_TOLERANCE_S = 60

# Issue #976: "Hybrid slot schedule:" fired at INFO every optimizer cycle
# regardless of whether anything changed. It now logs at INFO only when the
# horizon signature (slot count, first slot start, last slot start) differs
# from the previous cycle's — a genuine shape change worth surfacing — and at
# DEBUG otherwise. This signature is process-global, not per-config-entry:
# with two config entries running concurrently, their signatures could
# alternate and produce more INFO logging than either running alone would.
# That failure mode only ever produces *more* logging, never less, so it is
# an accepted tradeoff rather than a correctness bug.
_LAST_HORIZON_SIGNATURE: tuple | None = None


def _boundary_offset_tolerance_s(duration_minutes: int) -> float:
    """Return the boundary-noise window for an interval of this width.

    Issue #949: the window must scale with the interval it is applied to, or
    it swallows a meaningful slice of the fine granularity and a late entry
    gets credited with time that has already passed.

    Args:
        duration_minutes: Entry's duration in minutes (5, 30, or 60)

    Returns:
        Tolerance in seconds: min(60, duration_minutes * 2), floored at 0.

    """
    return max(0.0, min(float(_BOUNDARY_OFFSET_TOLERANCE_S), duration_minutes * 2))


def _interval_origin(slot_start: datetime, duration_minutes: int) -> datetime:
    """Return the true interval boundary for an entry's start time.

    Floors slot_start down to the nearest duration_minutes boundary, but only
    treats that floor as the real origin when slot_start is within the
    interval's boundary-noise tolerance of it — a few seconds of provider
    clock skew, not a genuinely different (misaligned) start time.

    Args:
        slot_start: Entry's parsed start time
        duration_minutes: Entry's duration (5 or 30)

    Returns:
        The interval's true boundary, or slot_start unchanged if it is not
        boundary noise.

    """
    floored = slot_start.replace(
        minute=(slot_start.minute // duration_minutes) * duration_minutes,
        second=0,
        microsecond=0,
    )
    if (slot_start - floored).total_seconds() < _boundary_offset_tolerance_s(
        duration_minutes
    ):
        return floored
    return slot_start


def _covers_now(
    slot_start: datetime, duration_minutes: int, now_local: datetime
) -> bool:
    """Return True if this entry's interval contains now_local.

    Only 5- and 30-minute entries are eligible — 60-minute entries always
    return False here, which keeps _split_60min_slot's split-then-drop
    behaviour for the current hour completely unchanged (see module docstring
    constraint: 60-min path is untouched by Issue #510 Slice 2).

    Args:
        slot_start: Entry's parsed start time
        duration_minutes: Entry's duration (5, 30, or 60)
        now_local: Current local time

    Returns:
        True if [interval origin, interval origin + duration) contains now.

    """
    if duration_minutes not in (5, 30):
        return False
    origin = _interval_origin(slot_start, duration_minutes)
    return origin <= now_local < origin + timedelta(minutes=duration_minutes)


def _floor_to_5min(moment: datetime) -> datetime:
    """Floor a datetime down to the nearest 5-minute wall-clock boundary.

    Shared by the stale-sensor synthetic fallback and by the current-slot
    re-anchoring in `_parse_single_entry`, so the two "what does a 5-minute
    'now' slot look like" formulas can never drift apart.

    Args:
        moment: The datetime to floor.

    Returns:
        moment with minute rounded down to a multiple of 5 and seconds/
        microseconds zeroed.

    """
    current_5min = (moment.minute // 5) * 5
    return moment.replace(minute=current_5min, second=0, microsecond=0)


def compute_hybrid_slot_schedule(
    now_local: datetime,
    general_forecast: list[dict],
    ha_timezone: str,
    max_forecast_hours: int = 24,
) -> tuple[list[dict], dict]:
    """Build hybrid slot schedule: ALL 5-min slots, then 30-min.

    Issue #327: Uses native data granularities without interpolation.
    - Amber provides 5-min near-term (~45-60 min), then 30-min extended forecast
    - This function identifies 5-min slots and switches to 30-min at boundary

    NO INTERPOLATION - use actual data only.
    NO GAPS - 5-min slots end at 30-min boundary, 30-min starts immediately.

    Args:
        now_local: Current datetime in HA local timezone
        general_forecast: List of Amber price forecast entries with start_time, end_time, duration
        ha_timezone: HA configured timezone (e.g., "Australia/Sydney")
        max_forecast_hours: Maximum hours to forecast (default 24)

    Returns:
        Tuple of (slots, metadata) where:
        - slots: List of slot dicts with:
            - start: datetime of slot start
            - interval_minutes: 5 or 30
            - price: price in $/kWh
            - price_source: "forecast_current" (the entry whose interval covers
              now), "5min", "30min", or "synthetic" (stale-sensor fallback used
              only when no entry covers now). A covering 30-min entry is
              re-anchored to a 5-minute "now" quantum rather than kept at its
              own 30-min width, so slot 0's width never exceeds 5 minutes
              regardless of the feed's native granularity — see
              `_parse_single_entry`.
            - estimate: bool | None, the covering entry's Amber `estimate` flag
              (True pre-settlement, False once settled; None on synthetic or
              60-min-derived slots)
        - metadata: Dict with:
            - timezone: HA timezone
            - slot_intervals: {"5min": count, "30min": count}
            - transition_boundary: Time when 5-min switches to 30-min (or None)
            - total_slots: Total number of slots
            - horizon_hours: Actual time span covered by slots in hours

    """
    metadata = _create_initial_metadata(ha_timezone)

    if not general_forecast:
        _LOGGER.warning("compute_hybrid_slot_schedule: Empty general_forecast")
        return [], metadata

    all_slots_raw = _parse_forecast_entries(general_forecast, now_local, ha_timezone)
    if not all_slots_raw:
        _LOGGER.warning("compute_hybrid_slot_schedule: No valid slots after parsing")
        return [], metadata

    all_slots_raw.sort(key=lambda x: x["start"])

    five_min_slots, thirty_min_slots = _separate_slots_by_duration(all_slots_raw)

    _LOGGER.debug(
        "compute_hybrid_slot_schedule: Found %d 5-min slots, %d 30-min slots",
        len(five_min_slots),
        len(thirty_min_slots),
    )

    cutoff_time = now_local + timedelta(hours=max_forecast_hours)
    slots, transition_boundary = _build_hybrid_schedule(
        five_min_slots, thirty_min_slots, cutoff_time
    )

    _ensure_current_slot_coverage(slots, now_local)

    if slots:
        _LOGGER.debug(
            "SLOT0_CURRENT: start=%s interval=%dmin price=%.4f source=%s estimate=%s",
            slots[0]["start"].isoformat(),
            slots[0]["interval_minutes"],
            slots[0]["price"],
            slots[0]["price_source"],
            slots[0].get("estimate"),
        )

    _compute_slot_metadata(slots, metadata, transition_boundary)

    return slots, metadata


def _create_initial_metadata(ha_timezone: str) -> dict:
    """Create initial metadata dict.

    Args:
        ha_timezone: HA timezone string

    Returns:
        Initial metadata dict

    """
    return {
        "timezone": ha_timezone,
        "slot_intervals": {"5min": 0, "30min": 0},
        "transition_boundary": None,
        "total_slots": 0,
        "horizon_hours": 0.0,
    }


def _parse_forecast_entries(
    general_forecast: list[dict], now_local: datetime, ha_timezone: str
) -> list[dict]:
    """Parse forecast entries into slot dicts.

    Args:
        general_forecast: Raw forecast entries
        now_local: Current local time
        ha_timezone: HA timezone

    Returns:
        List of parsed slot dicts

    """
    all_slots_raw: list[dict] = []

    malformed_durations = 0

    for entry in general_forecast:
        slot = _parse_single_entry(entry, now_local, ha_timezone)
        if slot:
            all_slots_raw.extend(slot if isinstance(slot, list) else [slot])
        elif _has_malformed_duration(entry, now_local, ha_timezone):
            malformed_durations += 1

    # Issue #946: one aggregated warning, not one per bad entry — a bad feed
    # can carry dozens of malformed entries and this runs on every 5-minute
    # evaluation tick.
    if malformed_durations:
        _LOGGER.warning(
            "slot_schedule: skipped %d of %d forecast entries with a malformed "
            "duration -- horizon may be short; check the price sensor's data",
            malformed_durations,
            len(general_forecast),
        )

    return all_slots_raw


def _has_malformed_duration(
    entry: object, now_local: datetime, ha_timezone: str
) -> bool:
    """Report whether a skipped entry was dropped for a malformed duration.

    Issue #946: distinguishes "this entry was malformed" (worth a warning)
    from "this entry was legitimately elapsed or unusable" (not). Used only
    to aggregate the warning count in `_parse_forecast_entries`; the skip
    itself already happened in `_get_entry_duration`.

    Args:
        entry: The raw forecast entry that produced no slot
        now_local: Current local time
        ha_timezone: HA timezone

    Returns:
        True if the entry carries a `duration` field that cannot be read as
        an int (or an end_time/start_time pair that cannot be subtracted).

    """
    if not hasattr(entry, "get"):
        return False

    duration = entry.get("duration")  # type: ignore[union-attr]
    if duration is None:
        return False
    try:
        int(duration)
    except (ValueError, TypeError):
        return True
    return False


def _parse_single_entry(
    entry: dict, now_local: datetime, ha_timezone: str
) -> dict | list[dict] | None:
    """Parse a single forecast entry.

    Args:
        entry: Forecast entry dict
        now_local: Current local time
        ha_timezone: HA timezone

    Returns:
        Slot dict, list of slot dicts, or None

    """
    if not hasattr(entry, "get"):
        return None

    start_time_str = entry.get("start_time")
    if not start_time_str:
        return None

    slot_start = parse_slot_time(start_time_str, ha_timezone)
    if slot_start is None:
        return None

    duration_minutes = _get_entry_duration(entry, slot_start, ha_timezone)
    if duration_minutes is None or duration_minutes not in (5, 30, 60):
        return None

    # Issue #510 Slice 2: an entry whose interval covers "now" is retained as
    # the current slot even though its raw (pre-floor) start may read as
    # slightly in the past — that's the +1s Amber offset, not staleness. An
    # entry is only dropped once its interval has genuinely elapsed.
    is_current = _covers_now(slot_start, duration_minutes, now_local)
    if not is_current and slot_start < now_local:
        return None

    price = float(entry.get("per_kwh", 0))

    if duration_minutes == 60:
        return _split_60min_slot(slot_start, price)

    if is_current and duration_minutes == 30:
        # SCOPE NOTE (#510): this branch is OUT OF SCOPE per the spec's
        # Non-goals — "5-min price sites only … 30-min support is out of
        # scope". On a 5-minute Amber feed the entry covering "now" is
        # always a 5-minute one, so this branch is unreachable on the site
        # this was built for and has never been validated against live
        # 30-minute data. Kept because the reasoning below is sound and it
        # costs nothing dormant; re-verify against a real 30-minute feed
        # before relying on it.
        # Review feedback on #510 Slice 2: retaining this entry at its own
        # 30-minute width would make slot 0 up to 30 minutes wide no matter
        # how far into the interval "now" actually is (e.g. still the full
        # 30-min slot with ten seconds of real time left in it). Every DP
        # energy term scales by slot width (slot_hours = interval_minutes /
        # 60), so a mostly-elapsed slot 0 lets the optimiser book
        # charge/discharge time that no longer exists, and it drags
        # core.py's DW-runway anchor (minutes_to_dw) and the published
        # precharge_runway_quantum_min back with it by the same amount.
        # Re-anchor slot 0 to the same 5-minute "now" quantum the
        # stale-sensor fallback below already uses, priced from this real
        # covering entry instead of a synthetic borrowed price — the brief
        # asked for the covering entry's PRICE, not for slot 0 to grow to
        # 30 minutes wide. A covering 5-min entry is already at (or under)
        # that quantum, so it keeps its own start/width untouched below.
        return _current_30min_slot(entry, slot_start, now_local, price)

    return {
        "start": slot_start,
        "interval_minutes": duration_minutes,
        "price": price,
        "price_source": "forecast_current"
        if is_current
        else ("5min" if duration_minutes == 5 else "30min"),
        "estimate": entry.get("estimate"),
    }


def _current_30min_slot(
    entry: dict, slot_start: datetime, now_local: datetime, price: float
) -> dict:
    """Build slot 0 from a covering 30-minute entry, bounded to a 5-min quantum.

    The covering entry is re-anchored onto the 5-minute "now" grid (see the
    review feedback quoted in `_parse_single_entry`) so slot 0 never claims
    up to 30 minutes of already-elapsed time. Two corrections keep that
    re-anchor from destroying the schedule instead of bounding it:

    Issue #947 — do not re-anchor when nothing has elapsed. The re-anchor
    exists to stop slot 0 claiming time that has ALREADY gone by. When the
    entry's origin is not strictly before now (now sits exactly on the
    boundary, or in the sub-second window before Amber's +1s offset), no
    time has elapsed and there is nothing to bound: the entry is returned at
    its own start and full 30-minute width. Collapsing it to 5 minutes there
    deleted 25 minutes from the head of the horizon and broke this module's
    NO GAPS contract.

    Issue #948 — back the anchor off so slot 0 ends on the covering
    interval's own boundary. Re-anchoring to floor_5(now) blindly made slot 0
    end at floor_5(now)+5, which on a feed that is NOT aligned to the
    5-minute grid (e.g. intervals at :18/:48) overran the covering interval's
    real end. That overrun then forced `_add_30min_after_transition` to
    discard the next REAL interval, punching a hole in the horizon. Backing
    off to (interval_end - 5min) when the grid anchor would overrun keeps
    slot 0 a 5-minute quantum containing now, ending exactly on the real
    boundary, with no gap and no double-counted overlap.

    Args:
        entry: The covering forecast entry (for its `estimate` flag)
        slot_start: Entry's parsed start time
        now_local: Current local time
        price: The covering entry's price per kWh

    Returns:
        Slot 0 as a 5-minute (or full 30-minute, if nothing has elapsed)
        slot priced from the covering entry.

    """
    origin = _interval_origin(slot_start, 30)
    interval_end = origin + timedelta(minutes=30)

    if origin >= now_local:
        # Nothing elapsed -> nothing to bound (#947).
        return {
            "start": slot_start,
            "interval_minutes": 30,
            "price": price,
            "price_source": "forecast_current",
            "estimate": entry.get("estimate"),
        }

    anchor = _floor_to_5min(now_local)
    if anchor + timedelta(minutes=5) > interval_end:
        # Grid anchor would overrun the covering interval -> back it off so
        # slot 0 ends exactly on the real boundary (#948).
        anchor = interval_end - timedelta(minutes=5)

    return {
        "start": anchor,
        "interval_minutes": 5,
        "price": price,
        "price_source": "forecast_current",
        "estimate": entry.get("estimate"),
    }


def _get_entry_duration(
    entry: dict, slot_start: datetime, ha_timezone: str
) -> int | None:
    """Get duration for a forecast entry.

    Issue #946: a malformed duration must skip THIS entry, never abort the
    build. `get_slot_duration_minutes` does a bare `int(duration)`, so a
    non-integral value such as "30.0" raises ValueError and a value of the
    wrong type raises TypeError. Both are caught here and returned as None,
    which `_parse_single_entry` already treats as "drop the entry" — so the
    rest of the horizon survives and the optimiser keeps a plan instead of
    getting none. (Deliberately NOT fixed inside utils.py: coercing
    `int(float(duration))` there would silently accept malformed data for
    every other caller.)

    Args:
        entry: Forecast entry
        slot_start: Parsed start time
        ha_timezone: HA timezone

    Returns:
        Duration in minutes, or None if absent/malformed/unresolvable

    """
    try:
        duration_minutes = get_slot_duration_minutes(entry)
    except (ValueError, TypeError):
        _LOGGER.debug(
            "slot_schedule: malformed duration on entry start=%s duration=%r "
            "-- skipping entry",
            entry.get("start_time"),
            entry.get("duration"),
        )
        return None

    if duration_minutes is not None:
        return duration_minutes

    end_time_str = entry.get("end_time")
    if not end_time_str:
        return None

    slot_end = parse_slot_time(end_time_str, ha_timezone)
    if slot_end:
        try:
            return int((slot_end - slot_start).total_seconds() / 60)
        except (ValueError, TypeError):
            _LOGGER.debug(
                "slot_schedule: unresolvable end_time on entry start=%s "
                "end_time=%r -- skipping entry",
                entry.get("start_time"),
                end_time_str,
            )
            return None

    return None


def _split_60min_slot(slot_start: datetime, price: float) -> list[dict]:
    """Split a 60-minute slot into two 30-minute slots.

    Args:
        slot_start: Slot start time
        price: Price per kWh

    Returns:
        List of two 30-min slot dicts

    """
    return [
        {
            "start": slot_start,
            "interval_minutes": 30,
            "price": price,
            "price_source": "30min",
        },
        {
            "start": slot_start + timedelta(minutes=30),
            "interval_minutes": 30,
            "price": price,
            "price_source": "30min",
        },
    ]


def _separate_slots_by_duration(slots: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separate slots into 5-min and 30-min lists.

    Args:
        slots: All slots

    Returns:
        Tuple of (5-min slots, 30-min slots)

    """
    five_min = [s for s in slots if s["interval_minutes"] == 5]
    thirty_min = [s for s in slots if s["interval_minutes"] == 30]
    return five_min, thirty_min


def _build_hybrid_schedule(
    five_min_slots: list[dict], thirty_min_slots: list[dict], cutoff_time: datetime
) -> tuple[list[dict], datetime | None]:
    """Build hybrid schedule combining 5-min and 30-min slots.

    Args:
        five_min_slots: 5-minute slots
        thirty_min_slots: 30-minute slots
        cutoff_time: Maximum forecast time

    Returns:
        Tuple of (combined slots, transition boundary)

    """
    slots: list[dict] = []
    transition_boundary = None

    slots.extend(five_min_slots)

    if five_min_slots:
        last_5min_end = five_min_slots[-1]["start"] + timedelta(minutes=5)
        transition_boundary = _add_30min_after_transition(
            slots, thirty_min_slots, last_5min_end, cutoff_time
        )
    else:
        _add_all_30min_slots(slots, thirty_min_slots, cutoff_time)
        if thirty_min_slots:
            transition_boundary = thirty_min_slots[0]["start"]

    slots.sort(key=lambda x: x["start"])
    return slots, transition_boundary


def _add_30min_after_transition(
    slots: list[dict],
    thirty_min_slots: list[dict],
    last_5min_end: datetime,
    cutoff_time: datetime,
) -> datetime | None:
    """Add 30-min slots that are not already covered by the 5-min transition.

    Issue #948: this used to discard every 30-min entry starting before
    `last_5min_end`. On an ALIGNED feed that was safe — the overlapping
    entry ends exactly where the 5-min block ends, so dropping it is a no-op.
    On a MISALIGNED feed (intervals at :18/:48) the entry that straddles
    `last_5min_end` is a REAL interval whose tail the 5-min block does not
    own, and discarding it deleted live time and punched a hole in the
    horizon. Only entries whose interval is FULLY contained inside the 5-min
    block are dropped; a straddling entry is kept, and the re-anchor in
    `_current_30min_slot` is responsible for making that hand-off exact.

    Args:
        slots: Slot list to extend
        thirty_min_slots: 30-minute slots
        last_5min_end: End time of last 5-min slot
        cutoff_time: Maximum forecast time

    Returns:
        Transition boundary time (start of the first retained 30-min slot)
        or None if nothing was retained.

    """
    transition_boundary: datetime | None = None
    for slot in thirty_min_slots:
        slot_end = slot["start"] + timedelta(minutes=slot["interval_minutes"])
        if slot_end <= last_5min_end:
            # Entirely inside the 5-min block -> already represented there.
            continue
        if slot["start"] >= cutoff_time:
            break
        if transition_boundary is None:
            transition_boundary = slot["start"]
        slots.append(slot)
    return transition_boundary


def _add_all_30min_slots(
    slots: list[dict], thirty_min_slots: list[dict], cutoff_time: datetime
) -> None:
    """Add all 30-min slots within cutoff.

    Args:
        slots: Slot list to extend
        thirty_min_slots: 30-minute slots
        cutoff_time: Maximum forecast time

    """
    for slot in thirty_min_slots:
        if slot["start"] < cutoff_time:
            slots.append(slot)


def _ensure_current_slot_coverage(slots: list[dict], now_local: datetime) -> None:
    """Ensure there's a slot covering 'now' by adding a synthetic slot if needed.

    Issue #510 Slice 2: this is the stale-sensor FALLBACK, not the steady
    state. In normal operation _parse_single_entry already retains the real
    forecast entry covering "now" as slot 0 (price_source="forecast_current"),
    so this function is a no-op. It only fires when no entry covers now at
    all — e.g. the price sensor's forecast has gone stale.

    Args:
        slots: Slot list (modified in place)
        now_local: Current local time

    """
    if not slots:
        return

    covers_now = _covers_now(slots[0]["start"], slots[0]["interval_minutes"], now_local)

    _LOGGER.debug(
        "HYBRID_SLOTS: slots=%d, first_slot=%s, now_local=%s, comparison=%s",
        len(slots),
        slots[0]["start"].strftime("%H:%M:%S"),
        now_local.strftime("%H:%M:%S"),
        "covers now" if covers_now else "does not cover now",
    )

    if covers_now:
        return

    synthetic_start = _floor_to_5min(now_local)
    estimated_price = slots[0]["price"] if slots else 0.0

    synthetic_slot = {
        "start": synthetic_start,
        "interval_minutes": 5,
        "price": estimated_price,
        "price_source": "synthetic",
    }
    slots.insert(0, synthetic_slot)

    _LOGGER.warning(
        "SYNTHETIC SLOT FALLBACK: no entry in the configured price forecast "
        "covers %s (first entry %s, gap=%.0fs) — slot 0 price borrowed from "
        "the next interval; check the configured general-price forecast "
        "source (pricing_general_forecast), not just sensor staleness. Rate "
        "tracked for integration degradation (#956).",
        synthetic_start.strftime("%H:%M:%S"),
        slots[1]["start"].strftime("%H:%M:%S") if len(slots) > 1 else "N/A",
        (slots[1]["start"] - synthetic_start).total_seconds() if len(slots) > 1 else 0,
    )


def _compute_slot_metadata(
    slots: list[dict], metadata: dict, transition_boundary: datetime | None
) -> None:
    """Compute and store slot metadata.

    Args:
        slots: Final slot list
        metadata: Metadata dict (modified in place)
        transition_boundary: Transition boundary time

    """
    five_min_count = len([s for s in slots if s["interval_minutes"] == 5])
    thirty_min_count = len([s for s in slots if s["interval_minutes"] in (30, 60)])

    metadata["slot_intervals"] = {"5min": five_min_count, "30min": thirty_min_count}
    metadata["total_slots"] = len(slots)
    metadata["transition_boundary"] = (
        transition_boundary.strftime("%H:%M") if transition_boundary else None
    )

    if slots:
        horizon_delta = slots[-1]["start"] - slots[0]["start"]
        last_slot_duration = slots[-1]["interval_minutes"]
        horizon_hours = (horizon_delta.total_seconds() / 3600.0) + (
            last_slot_duration / 60.0
        )
        metadata["horizon_hours"] = round(horizon_hours, 2)

        _log_slot_details(slots)

    global _LAST_HORIZON_SIGNATURE
    signature = (
        len(slots),
        slots[0]["start"] if slots else None,
        slots[-1]["start"] if slots else None,
    )
    level = logging.INFO if signature != _LAST_HORIZON_SIGNATURE else logging.DEBUG
    _LAST_HORIZON_SIGNATURE = signature

    _LOGGER.log(
        level,
        "Hybrid slot schedule: %d 5-min slots, %d 30-min slots, horizon=%.2fh, transition at %s",
        five_min_count,
        thirty_min_count,
        metadata["horizon_hours"],
        metadata["transition_boundary"] or "N/A",
    )


def _log_slot_details(slots: list[dict]) -> None:
    """Log timezone information for slots.

    Args:
        slots: Slot list

    """
    _LOGGER.debug(
        "HYBRID_SLOTS: First 5 slots (with TZ): %s",
        [s["start"].isoformat() for s in slots[:5]],
    )
    _LOGGER.debug(
        "HYBRID_SLOTS: Slot 0 TZ info: %s (offset=%s)",
        slots[0]["start"].isoformat(),
        slots[0]["start"].utcoffset(),
    )
    if len(slots) > 1:
        _LOGGER.debug(
            "HYBRID_SLOTS: Slot 1 TZ info: %s (offset=%s)",
            slots[1]["start"].isoformat(),
            slots[1]["start"].utcoffset(),
        )
