"""Rolling synthetic-slot-0 health tracker.

Issue #956: ``engine/slot_schedule.py::_ensure_current_slot_coverage`` falls
back to a SYNTHETIC slot 0 whenever no entry from the configured price
forecast source covers "now" -- it borrows the next interval's price instead.
A single synthetic slot is harmless (one missed tick); a SUSTAINED run of them
means the configured forecast source is not producing usable data. Before
this module existed that only showed up as a WARNING log line -- it fired
1006 times on one live deployment while ``sensor.localshift_integration_status``
stayed "ok" and ``entity_health`` read "0/0", because nothing counted the
warnings or fed them back into integration status.

This module is pure logic (no Home Assistant imports) so it is trivial to
unit-test to full coverage in isolation. ``coordinator/entity_monitor.py``
wires an instance of :class:`SyntheticSlotHealth` (stored on
``CoordinatorData.synthetic_slot_health``) into ``integration_status``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

# Share of the rolling window's evaluations that must be synthetic before the
# tracker considers itself degraded. A module constant, not a literal, so a
# future PR can promote it to a config entity without touching the logic.
SYNTHETIC_RATE_THRESHOLD = 0.5

# Rolling window width, in minutes. Evaluations are dispatched roughly once a
# minute (tick_scheduler's fast tick + the evaluation dispatcher + price
# ticks), so this keeps ~60 samples in the common case -- enough to smooth
# over a single missed tick without hiding a genuinely sustained failure.
WINDOW_MINUTES = 60

# Hard cap on samples retained regardless of window width, so a burst of
# sub-minute evaluations (e.g. rapid price ticks) cannot grow the buffer
# unbounded.
MAX_SAMPLES = 240

# Minimum samples in the window before the rate is trusted for a degrade
# decision. Below this, a single synthetic slot at startup (rate=1.0 on one
# sample) would otherwise read as fully degraded immediately.
MIN_SAMPLES_FOR_RATE = 6

# Consecutive record() calls with rate >= SYNTHETIC_RATE_THRESHOLD required to
# flip `degraded` True. Streaks count EVALUATIONS, not wall-clock time -- an
# evaluation cadence slower than ~1/min stretches this out proportionally.
CONSECUTIVE_TO_DEGRADE = 3

# Consecutive record() calls with rate < SYNTHETIC_RATE_THRESHOLD required to
# clear `degraded` back to False. Deliberately NOT gated by
# MIN_SAMPLES_FOR_RATE: a window that has been flushed of synthetic samples
# (few samples, all clean) should recover, not stay stuck degraded just
# because the sample count also dropped below the degrade-side minimum.
CONSECUTIVE_TO_CLEAR = 3

# Evaluations since startup after which, if no forecast entry has EVER
# covered "now", a single loud startup WARNING fires naming the configured
# forecast source. Small on purpose: this is a "the integration never worked"
# signal, not a rate -- it should fire fast, once.
STARTUP_CHECK_EVALUATIONS = 3


@dataclass
class SyntheticSlotHealth:
    """Rolling tracker for the synthetic-slot-0 rate.

    Call :meth:`record` once per successful slot build (see
    ``engine/optimizer_facade.py::OptimizerFacade.run_inline``); read
    ``.rate``, ``.degraded``, ``.needs_startup_warning`` from
    ``coordinator/entity_monitor.py``.
    """

    samples: list[tuple[datetime, bool]] = field(default_factory=list)
    """(recorded_at, is_synthetic) pairs currently inside the rolling window."""

    consecutive_above: int = 0
    """Consecutive record() calls that read rate >= threshold."""

    consecutive_below: int = 0
    """Consecutive record() calls that read rate < threshold (or too few samples)."""

    degraded: bool = False
    """Whether the sustained-synthetic-rate condition is currently active."""

    evaluations_since_start: int = 0
    """Total record() calls made, regardless of window pruning."""

    covering_slot_seen: bool = False
    """Whether any evaluation has EVER seen a non-synthetic slot 0."""

    startup_warning_logged: bool = False
    """Set by the caller once the one-shot startup WARNING has fired."""

    def record(self, is_synthetic: bool, now: datetime) -> None:
        """Record one evaluation's slot-0 price_source and update state.

        Args:
            is_synthetic: True when slot 0's ``price_source == "synthetic"``.
            now: Timestamp to record the sample at, and to prune the rolling
                window against.

        """
        self.samples.append((now, is_synthetic))

        cutoff = now - timedelta(minutes=WINDOW_MINUTES)
        self.samples = [s for s in self.samples if s[0] >= cutoff]
        if len(self.samples) > MAX_SAMPLES:
            self.samples = self.samples[-MAX_SAMPLES:]

        self.evaluations_since_start += 1
        if not is_synthetic:
            self.covering_slot_seen = True

        if self.sample_count >= MIN_SAMPLES_FOR_RATE and (
            self.rate >= SYNTHETIC_RATE_THRESHOLD
        ):
            self.consecutive_above += 1
            self.consecutive_below = 0
        else:
            self.consecutive_below += 1
            self.consecutive_above = 0

        if not self.degraded and self.consecutive_above >= CONSECUTIVE_TO_DEGRADE:
            self.degraded = True
        elif self.degraded and self.consecutive_below >= CONSECUTIVE_TO_CLEAR:
            self.degraded = False

    @property
    def rate(self) -> float:
        """Share of samples currently in the window where slot 0 was synthetic."""
        if not self.samples:
            return 0.0
        synthetic_count = sum(1 for _, is_synthetic in self.samples if is_synthetic)
        return synthetic_count / len(self.samples)

    @property
    def sample_count(self) -> int:
        """Number of samples currently inside the rolling window."""
        return len(self.samples)

    @property
    def needs_startup_warning(self) -> bool:
        """True once startup has run long enough without ever seeing a real slot."""
        return (
            self.evaluations_since_start >= STARTUP_CHECK_EVALUATIONS
            and not self.covering_slot_seen
            and not self.startup_warning_logged
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the tracker's current state for a sensor attribute."""
        return {
            "rate": round(self.rate, 3),
            "degraded": self.degraded,
            "sample_count": self.sample_count,
            "consecutive_above": self.consecutive_above,
            "consecutive_below": self.consecutive_below,
        }
