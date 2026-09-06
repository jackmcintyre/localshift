"""Tests for SyntheticSlotHealth — Issue #956.

A sustained run of synthetic slot-0 pricing (no forecast entry from the
configured price source covers "now") must be detectable as a rate with
hysteresis, not just visible in the WARNING log. These tests pin the pure
tracker logic in isolation from the coordinator/entity_monitor wiring.

Where a test needs to pin the tracker's rate at (or either side of) an exact
value across several successive record() calls, it writes directly to
`.samples` first (a plain list field) so the window's contents are
deterministic rather than relying on hand-counted organic accumulation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.localshift.coordinator.synthetic_slot_health import (
    CONSECUTIVE_TO_CLEAR,
    CONSECUTIVE_TO_DEGRADE,
    MAX_SAMPLES,
    MIN_SAMPLES_FOR_RATE,
    STARTUP_CHECK_EVALUATIONS,
    SYNTHETIC_RATE_THRESHOLD,
    WINDOW_MINUTES,
    SyntheticSlotHealth,
)

T0 = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class TestRate:
    def test_no_samples_is_zero(self):
        health = SyntheticSlotHealth()
        assert health.rate == 0.0
        assert health.sample_count == 0

    def test_three_of_six_is_half(self):
        health = SyntheticSlotHealth()
        pattern = [True, True, True, False, False, False]
        for i, is_synthetic in enumerate(pattern):
            health.record(is_synthetic, _at(i * 10))
        assert health.rate == 0.5
        assert health.sample_count == 6

    def test_samples_older_than_window_are_pruned(self):
        health = SyntheticSlotHealth()
        health.record(True, T0)
        # Far outside the window: only the new sample should remain.
        later = T0 + timedelta(minutes=WINDOW_MINUTES + 1)
        health.record(False, later)
        assert health.sample_count == 1
        assert health.rate == 0.0

    def test_buffer_never_exceeds_max_samples(self):
        health = SyntheticSlotHealth()
        for i in range(MAX_SAMPLES + 50):
            # Space samples 1 second apart so none fall outside the window
            # on their own (keeps this a pure cap test, not a prune test).
            health.record(i % 2 == 0, T0 + timedelta(seconds=i))
        assert health.sample_count <= MAX_SAMPLES


class TestDegrade:
    def test_below_min_samples_never_degrades_regardless_of_rate(self):
        health = SyntheticSlotHealth()
        assert MIN_SAMPLES_FOR_RATE > 3, "test assumes fewer than min samples"
        for i in range(3):
            health.record(True, _at(i))
        assert health.sample_count < MIN_SAMPLES_FOR_RATE
        assert health.degraded is False

    def test_three_consecutive_at_or_above_threshold_degrades(self):
        health = SyntheticSlotHealth()
        # Pin a window that already sits at/above threshold before each of
        # the next CONSECUTIVE_TO_DEGRADE record() calls, so each call is
        # independently guaranteed to read rate >= threshold.
        for i in range(CONSECUTIVE_TO_DEGRADE):
            health.samples = [(_at(-1), True)] * (MIN_SAMPLES_FOR_RATE - 1)
            health.record(True, _at(i))
            assert health.rate >= SYNTHETIC_RATE_THRESHOLD
        assert health.degraded is True

    def test_two_consecutive_above_threshold_is_not_enough(self):
        health = SyntheticSlotHealth()
        assert CONSECUTIVE_TO_DEGRADE > 2, "test assumes a 3-strike degrade rule"
        for i in range(CONSECUTIVE_TO_DEGRADE - 1):
            health.samples = [(_at(-1), True)] * (MIN_SAMPLES_FOR_RATE - 1)
            health.record(True, _at(i))
        assert health.degraded is False

    def test_rate_at_exactly_threshold_counts_as_degrading(self):
        health = SyntheticSlotHealth()
        m = MIN_SAMPLES_FOR_RATE
        # Pre-load so that appending one more True lands the rate at
        # EXACTLY the threshold: a plain >= comparison, not a strict >,
        # still counts as degrading.
        pre_true = max(0, m // 2 - 1)
        pre_false = (m - 1) - pre_true
        for i in range(CONSECUTIVE_TO_DEGRADE):
            health.samples = [(_at(-1), True)] * pre_true + [
                (_at(-1), False)
            ] * pre_false
            health.record(True, _at(i))
        assert health.rate == SYNTHETIC_RATE_THRESHOLD
        assert health.degraded is True

    def test_flapping_above_and_below_never_degrades(self):
        health = SyntheticSlotHealth()
        t = 0.0
        for _ in range(10):
            # Window sits comfortably above threshold; recording another
            # synthetic sample keeps it above threshold for this call only.
            health.samples = [(_at(t - 1), True)] * MIN_SAMPLES_FOR_RATE
            health.record(True, _at(t))
            assert health.rate >= SYNTHETIC_RATE_THRESHOLD
            t += 1

            # Window sits comfortably below threshold; recording a
            # non-synthetic sample keeps it below threshold for this call.
            health.samples = [(_at(t - 1), False)] * MIN_SAMPLES_FOR_RATE
            health.record(False, _at(t))
            assert health.rate < SYNTHETIC_RATE_THRESHOLD
            t += 1

        assert health.degraded is False


class TestClear:
    def _degraded_tracker(self) -> tuple[SyntheticSlotHealth, float]:
        health = SyntheticSlotHealth()
        for i in range(CONSECUTIVE_TO_DEGRADE):
            health.samples = [(_at(-1), True)] * (MIN_SAMPLES_FOR_RATE - 1)
            health.record(True, _at(i))
        assert health.degraded is True
        return health, float(CONSECUTIVE_TO_DEGRADE)

    def test_three_consecutive_below_threshold_clears(self):
        health, t = self._degraded_tracker()
        for i in range(CONSECUTIVE_TO_CLEAR):
            health.samples = [(_at(t - 1), False)] * (MIN_SAMPLES_FOR_RATE - 1)
            health.record(False, _at(t + i))
        assert health.degraded is False

    def test_two_consecutive_below_threshold_does_not_clear(self):
        health, t = self._degraded_tracker()
        assert CONSECUTIVE_TO_CLEAR > 2, "test assumes a 3-strike clear rule"
        for i in range(CONSECUTIVE_TO_CLEAR - 1):
            health.samples = [(_at(t - 1), False)] * (MIN_SAMPLES_FOR_RATE - 1)
            health.record(False, _at(t + i))
        assert health.degraded is True

    def test_clear_has_no_min_sample_guard(self):
        """A window flushed down to a handful of samples still clears."""
        health, t = self._degraded_tracker()
        # Jump far enough forward that every prior (synthetic) sample falls
        # outside the window and is pruned before the new, sparse window
        # is judged -- proving the clear path has no MIN_SAMPLES_FOR_RATE
        # guard the way the degrade path does.
        flush_time = t + WINDOW_MINUTES * 60 + 10
        for i in range(CONSECUTIVE_TO_CLEAR):
            health.record(False, _at(flush_time + i))
        assert health.sample_count < MIN_SAMPLES_FOR_RATE
        assert health.degraded is False


class TestStartupWarning:
    def test_synthetic_records_with_no_covering_slot_need_warning(self):
        health = SyntheticSlotHealth()
        for i in range(STARTUP_CHECK_EVALUATIONS):
            health.record(True, _at(i))
        assert health.needs_startup_warning is True

    def test_one_non_synthetic_record_suppresses_forever(self):
        health = SyntheticSlotHealth()
        health.record(False, _at(0))
        for i in range(1, STARTUP_CHECK_EVALUATIONS + 5):
            health.record(True, _at(i))
        assert health.needs_startup_warning is False

    def test_before_startup_threshold_no_warning_needed(self):
        health = SyntheticSlotHealth()
        assert STARTUP_CHECK_EVALUATIONS > 1, "test assumes a multi-eval threshold"
        for i in range(STARTUP_CHECK_EVALUATIONS - 1):
            health.record(True, _at(i))
        assert health.needs_startup_warning is False

    def test_startup_warning_logged_flag_suppresses_rearming(self):
        health = SyntheticSlotHealth()
        for i in range(STARTUP_CHECK_EVALUATIONS + 2):
            health.record(True, _at(i))
        assert health.needs_startup_warning is True
        health.startup_warning_logged = True
        health.record(True, _at(STARTUP_CHECK_EVALUATIONS + 3))
        assert health.needs_startup_warning is False


class TestToDict:
    def test_shape_and_key_set(self):
        health = SyntheticSlotHealth()
        health.record(True, T0)
        health.record(False, _at(1))
        result = health.to_dict()
        assert set(result.keys()) == {
            "rate",
            "degraded",
            "sample_count",
            "consecutive_above",
            "consecutive_below",
        }
        assert isinstance(result["rate"], float)
        assert isinstance(result["degraded"], bool)
        assert isinstance(result["sample_count"], int)

    def test_rate_is_rounded(self):
        health = SyntheticSlotHealth()
        # 1/3 has a long decimal tail; to_dict should round it.
        health.record(True, _at(0))
        health.record(False, _at(1))
        health.record(False, _at(2))
        result = health.to_dict()
        assert result["rate"] == round(1 / 3, 3)
