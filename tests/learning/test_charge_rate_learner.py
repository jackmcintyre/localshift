"""Tests for ChargeRateLearner charge-rate learning."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.engine.optimizer_dp import PlannerAction
from custom_components.localshift.learning.charge_rate import (
    ChargeRateCurve,
    ChargeRateLearner,
)


@pytest.mark.asyncio
async def test_charge_rate_learner_persists_curves(
    storage, history, decisions, monkeypatch
):
    from custom_components.localshift.learning import charge_rate as charge_rate_module

    hass = MagicMock()
    monkeypatch.setattr(charge_rate_module, "Store", MagicMock(return_value=storage))
    learner = ChargeRateLearner(hass, entry_id="entry-1")

    learner.update_from_history(
        history["power_history"],
        history["soc_history"],
        decisions,
    )
    await learner.async_save()

    assert storage.async_save.called
    saved = storage.async_save.call_args.args[0]
    assert saved["version"] == 1
    assert "curves" in saved
    assert "diagnostics" in saved


def test_charge_rate_learner_regime_separation(decisions, history):
    learner = ChargeRateLearner(MagicMock(), entry_id="entry-2")

    learner.update_from_history(
        history["power_history"],
        history["soc_history"],
        decisions,
    )

    assert learner.get_curve("normal") is not None
    assert learner.get_curve("boost") is not None
    diagnostics = learner.diagnostics
    assert diagnostics["labeled_sample_ratio"] > 0


def test_charge_rate_learner_skips_missing_decisions(history):
    learner = ChargeRateLearner(MagicMock(), entry_id="entry-3")
    learner.update_from_history(
        history["power_history"],
        history["soc_history"],
        [],
    )

    assert learner.get_curve("normal") is None
    assert learner.diagnostics["labeled_sample_ratio"] == 0


@pytest.mark.asyncio
async def test_charge_rate_learner_async_load_save(storage, monkeypatch):
    from custom_components.localshift.learning import charge_rate as charge_rate_module

    storage.async_load.return_value = {
        "version": 1,
        "updated_at": "2024-01-02T00:00:00+00:00",
        "curves": {
            "normal": {
                "bins": {0: 3.0, 50: 2.5},
                "sample_count": 120,
                "normalized_mad": 0.1,
                "min_samples": 50,
            }
        },
        "diagnostics": {"labeled_sample_ratio": 1.0},
    }

    hass = MagicMock()
    monkeypatch.setattr(charge_rate_module, "Store", MagicMock(return_value=storage))
    learner = ChargeRateLearner(hass, entry_id="entry-4")

    await learner.async_load()
    assert learner.get_curve("normal") is not None

    await learner.async_save()
    assert storage.async_save.called


def test_charge_rate_learner_respects_min_samples(history, decisions):
    learner = ChargeRateLearner(MagicMock(), entry_id="entry-5")
    short_power = history["power_history"][:10]
    short_soc = history["soc_history"][:10]
    short_decisions = decisions[:10]

    learner.update_from_history(short_power, short_soc, short_decisions)

    assert learner.get_curve("normal") is None
    assert learner.get_curve("boost") is None


def test_charge_rate_learner_calibrates_power_sign(history, decisions):
    learner = ChargeRateLearner(MagicMock(), entry_id="entry-6")
    inverted_power = [(ts, -abs(value)) for ts, value in history["power_history"]]

    learner.update_from_history(
        inverted_power,
        history["soc_history"],
        decisions,
    )

    assert learner.diagnostics["power_sign_inverted"] is True
    curve = learner.get_curve("normal")
    assert curve is not None
    assert min(curve.bins.values()) > 0


def test_charge_rate_learner_marks_stale(history, decisions):
    learner = ChargeRateLearner(MagicMock(), entry_id="entry-7")
    learner.update_from_history(
        history["power_history"],
        history["soc_history"],
        decisions,
    )

    learner._updated_at = datetime.now(UTC) - timedelta(days=8)
    assert learner.diagnostics["stale"] is True


def test_charge_rate_learner_tracks_missing_history(history, decisions):
    learner = ChargeRateLearner(MagicMock(), entry_id="entry-8")
    power_history = history["power_history"][:]
    soc_history = history["soc_history"][:]
    power_history.pop(2)
    soc_history.pop(4)

    learner.update_from_history(power_history, soc_history, decisions)

    diagnostics = learner.diagnostics
    assert diagnostics["missing_history"]["power"] > 0
    assert diagnostics["missing_history"]["soc"] > 0


def test_charge_rate_learner_skips_mismatched_decisions(history, decisions):
    learner = ChargeRateLearner(MagicMock(), entry_id="entry-9")
    zero_power = [(ts, 0.0) for ts, _ in history["power_history"]]

    learner.update_from_history(zero_power, history["soc_history"], decisions)

    diagnostics = learner.diagnostics
    assert diagnostics["decision_mismatch"] > 0
    assert learner.get_curve("normal") is None


def test_charge_rate_learner_applies_monotonic_smoothing(history, decisions):
    learner = ChargeRateLearner(MagicMock(), entry_id="entry-10")
    increasing_power = []
    for index, (ts, _) in enumerate(history["power_history"]):
        increasing_power.append((
            ts,
            1.0 + (index / len(history["power_history"])) * 4.0,
        ))

    mono_decisions = [
        SimpleNamespace(
            timestamp=dec.timestamp, mode_chosen=PlannerAction.CHARGE_GRID_NORMAL
        )
        for dec in decisions
    ]

    learner.update_from_history(
        increasing_power, history["soc_history"], mono_decisions
    )
    curve = learner.get_curve("normal")
    assert curve is not None

    sorted_bins = [
        rate for _, rate in sorted(curve.bins.items(), key=lambda item: item[0])
    ]
    assert all(
        earlier >= later
        for earlier, later in zip(sorted_bins, sorted_bins[1:], strict=False)
    )


def test_charge_rate_learner_update_from_history_with_state_objects(history):
    class State:
        def __init__(self, last_updated: datetime, state: float) -> None:
            self.last_updated = last_updated
            self.state = state

    power_states = [State(ts, value) for ts, value in history["power_history"]]
    soc_states = [State(ts, value) for ts, value in history["soc_history"]]
    slot_minutes = history["slot_minutes"]
    decisions = []
    for index in range(len(power_states)):
        decisions.append(
            SimpleNamespace(
                timestamp=history["start"]
                + timedelta(minutes=slot_minutes * index + 1),
                mode_chosen=PlannerAction.CHARGE_GRID_NORMAL,
            )
        )

    learner = ChargeRateLearner(MagicMock(), entry_id="entry-12")
    learner.update_from_history(power_states, soc_states, decisions)

    assert learner.get_curve("normal") is not None


def test_charge_rate_learner_handles_empty_history():
    learner = ChargeRateLearner(MagicMock(), entry_id="entry-13")
    updated = learner.update_from_history([], [], [])

    assert updated is False
    assert learner.diagnostics["labeled_sample_ratio"] == 0.0


@pytest.mark.asyncio
async def test_charge_rate_learner_async_fetch_history_missing_entities():
    learner = ChargeRateLearner(MagicMock(), entry_id="entry-14")
    power_history, soc_history = await learner.async_fetch_history()

    assert power_history == []
    assert soc_history == []


@pytest.mark.asyncio
async def test_charge_rate_learner_async_fetch_history_parses_recorder(monkeypatch):
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(
        return_value={
            "sensor.power": [
                {"start": datetime(2024, 1, 1, 0, 0, tzinfo=UTC), "mean": 2.5}
            ]
        }
    )

    state = SimpleNamespace(
        last_updated=datetime(2024, 1, 1, 0, 15, tzinfo=UTC), state="45"
    )
    history_module = types.SimpleNamespace(
        get_significant_states=AsyncMock(return_value={"sensor.soc": [state]})
    )
    statistics_module = types.SimpleNamespace(statistics_during_period=MagicMock())
    recorder_module = types.SimpleNamespace(
        history=history_module, statistics=statistics_module
    )
    monkeypatch.setitem(
        sys.modules, "homeassistant.components.recorder", recorder_module
    )

    learner = ChargeRateLearner(
        hass,
        entry_id="entry-15",
        power_entity_id="sensor.power",
        soc_entity_id="sensor.soc",
    )
    power_history, soc_history = await learner.async_fetch_history()

    assert power_history == [(datetime(2024, 1, 1, 0, 0, tzinfo=UTC), 2.5)]
    assert soc_history == [(datetime(2024, 1, 1, 0, 15, tzinfo=UTC), 45.0)]


@pytest.mark.asyncio
async def test_charge_rate_learner_async_fetch_history_handles_stat_error(monkeypatch):
    hass = MagicMock()
    hass.async_add_executor_job = MagicMock(side_effect=Exception("boom"))

    recorder_module = types.SimpleNamespace(
        history=types.SimpleNamespace(get_significant_states=MagicMock()),
        statistics=types.SimpleNamespace(statistics_during_period=MagicMock()),
    )
    monkeypatch.setitem(
        sys.modules, "homeassistant.components.recorder", recorder_module
    )

    learner = ChargeRateLearner(
        hass,
        entry_id="entry-16",
        power_entity_id="sensor.power",
        soc_entity_id="sensor.soc",
    )
    power_history, soc_history = await learner.async_fetch_history()

    assert power_history == []
    assert soc_history == []


@pytest.mark.asyncio
async def test_charge_rate_learner_async_load_ignores_bad_bins(storage):
    from custom_components.localshift.learning import charge_rate as charge_rate_module

    storage.async_load.return_value = {
        "version": 1,
        "curves": {"normal": {"bins": "bad"}},
        "diagnostics": {"note": "ignored"},
    }
    charge_rate_module.Store = MagicMock(return_value=storage)

    learner = ChargeRateLearner(MagicMock(), entry_id="entry-17")
    await learner.async_load()

    assert learner.get_curve("normal") is None


def test_charge_rate_learner_reports_recent_as_not_stale(history, decisions):
    learner = ChargeRateLearner(MagicMock(), entry_id="entry-18")
    learner.update_from_history(
        history["power_history"],
        history["soc_history"],
        decisions,
    )

    learner._updated_at = datetime.now(UTC)
    assert learner.diagnostics["stale"] is False


def test_charge_rate_helpers_handle_invalid_history():
    from custom_components.localshift.learning import charge_rate as charge_rate_module

    assert charge_rate_module._median([]) == 0.0

    now = datetime(2024, 1, 1, tzinfo=UTC)
    history = [
        (now, "bad"),
        SimpleNamespace(last_updated=None, state="1"),
        SimpleNamespace(last_updated=now, state="unknown"),
        SimpleNamespace(last_updated=now, state="3.5"),
    ]
    normalized = charge_rate_module._normalize_history(history)

    assert normalized == [(now, 3.5)]


def test_charge_rate_learner_get_curve_requires_min_samples():
    learner = ChargeRateLearner(MagicMock(), entry_id="entry-19")
    learner._curves["normal"] = ChargeRateCurve.from_bins(
        {0: 2.0}, sample_count=1, normalized_mad=0.0, min_samples=5
    )

    assert learner.get_curve("normal") is None


@pytest.mark.asyncio
async def test_charge_rate_learner_async_load_ignores_non_dict(storage):
    from custom_components.localshift.learning import charge_rate as charge_rate_module

    storage.async_load.return_value = []
    charge_rate_module.Store = MagicMock(return_value=storage)

    learner = ChargeRateLearner(MagicMock(), entry_id="entry-20")
    await learner.async_load()

    assert learner.get_curve("normal") is None


@pytest.mark.asyncio
async def test_charge_rate_learner_async_load_ignores_non_dict_curves(storage):
    from custom_components.localshift.learning import charge_rate as charge_rate_module

    storage.async_load.return_value = {"version": 1, "curves": "bad"}
    charge_rate_module.Store = MagicMock(return_value=storage)

    learner = ChargeRateLearner(MagicMock(), entry_id="entry-21")
    await learner.async_load()

    assert learner.get_curve("normal") is None


@pytest.mark.asyncio
async def test_charge_rate_learner_async_load_skips_non_dict_curve_data(storage):
    from custom_components.localshift.learning import charge_rate as charge_rate_module

    storage.async_load.return_value = {
        "version": 1,
        "curves": {"normal": "bad"},
        "updated_at": "invalid",
    }
    charge_rate_module.Store = MagicMock(return_value=storage)

    learner = ChargeRateLearner(MagicMock(), entry_id="entry-22")
    await learner.async_load()

    assert learner.get_curve("normal") is None


@pytest.mark.asyncio
async def test_charge_rate_learner_async_fetch_history_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "homeassistant.components.recorder", None)
    learner = ChargeRateLearner(
        MagicMock(),
        entry_id="entry-23",
        power_entity_id="sensor.power",
        soc_entity_id="sensor.soc",
    )
    power_history, soc_history = await learner.async_fetch_history()

    assert power_history == []
    assert soc_history == []


@pytest.mark.asyncio
async def test_charge_rate_learner_async_fetch_history_now_none(monkeypatch):
    from custom_components.localshift.learning import charge_rate as charge_rate_module

    monkeypatch.setattr(charge_rate_module.dt_util, "now", lambda: None)
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(return_value={"sensor.power": []})
    history_module = types.SimpleNamespace(
        get_significant_states=AsyncMock(return_value={"sensor.soc": []})
    )
    statistics_module = types.SimpleNamespace(statistics_during_period=MagicMock())
    recorder_module = types.SimpleNamespace(
        history=history_module, statistics=statistics_module
    )
    monkeypatch.setitem(
        sys.modules, "homeassistant.components.recorder", recorder_module
    )

    learner = ChargeRateLearner(
        hass,
        entry_id="entry-24",
        power_entity_id="sensor.power",
        soc_entity_id="sensor.soc",
    )
    power_history, soc_history = await learner.async_fetch_history()

    assert power_history == []
    assert soc_history == []


@pytest.mark.asyncio
async def test_charge_rate_learner_async_fetch_history_handles_soc_error(monkeypatch):
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(
        return_value={
            "sensor.power": [{"start": datetime(2024, 1, 1, tzinfo=UTC), "mean": 2.0}]
        }
    )
    history_module = types.SimpleNamespace(
        get_significant_states=AsyncMock(side_effect=Exception("boom"))
    )
    statistics_module = types.SimpleNamespace(statistics_during_period=MagicMock())
    recorder_module = types.SimpleNamespace(
        history=history_module, statistics=statistics_module
    )
    monkeypatch.setitem(
        sys.modules, "homeassistant.components.recorder", recorder_module
    )

    learner = ChargeRateLearner(
        hass,
        entry_id="entry-25",
        power_entity_id="sensor.power",
        soc_entity_id="sensor.soc",
    )
    power_history, soc_history = await learner.async_fetch_history()

    assert power_history == []
    assert soc_history == []


@pytest.mark.asyncio
async def test_charge_rate_learner_async_fetch_history_skips_invalid_rows(monkeypatch):
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(
        return_value={
            "sensor.power": [
                "bad",
                {"mean": 2.0},
                {"start": datetime(2024, 1, 1, tzinfo=UTC), "mean": "bad"},
            ]
        }
    )
    history_module = types.SimpleNamespace(
        get_significant_states=AsyncMock(
            return_value={
                "sensor.soc": [
                    SimpleNamespace(last_updated=None, state="1"),
                    SimpleNamespace(
                        last_updated=datetime(2024, 1, 1, tzinfo=UTC), state="bad"
                    ),
                ]
            }
        )
    )
    statistics_module = types.SimpleNamespace(statistics_during_period=MagicMock())
    recorder_module = types.SimpleNamespace(
        history=history_module, statistics=statistics_module
    )
    monkeypatch.setitem(
        sys.modules, "homeassistant.components.recorder", recorder_module
    )

    learner = ChargeRateLearner(
        hass,
        entry_id="entry-26",
        power_entity_id="sensor.power",
        soc_entity_id="sensor.soc",
    )
    power_history, soc_history = await learner.async_fetch_history()

    assert power_history == []
    assert soc_history == []


def test_charge_rate_learner_skips_invalid_decisions(history):
    learner = ChargeRateLearner(MagicMock(), entry_id="entry-27")
    decisions = [
        SimpleNamespace(timestamp=None, mode_chosen=PlannerAction.CHARGE_GRID_NORMAL),
        SimpleNamespace(timestamp=history["start"], mode_chosen="invalid"),
        SimpleNamespace(
            timestamp=history["start"] + timedelta(minutes=1),
            mode_chosen=PlannerAction.HOLD,
        ),
    ]

    learner.update_from_history(
        history["power_history"],
        history["soc_history"],
        decisions,
    )

    assert learner.get_curve("normal") is None


def test_charge_rate_learner_build_curve_handles_empty_and_nan():
    learner = ChargeRateLearner(MagicMock(), entry_id="entry-28")
    curve, diagnostics = learner._build_curve([])

    assert curve is None
    assert diagnostics["sample_count"] == 0


def test_charge_rate_learner_label_regime_returns_none_for_hold():
    learner = ChargeRateLearner(MagicMock(), entry_id="entry-29")
    assert learner._label_regime(PlannerAction.HOLD) is None

    nan_samples = [(float(i), float("nan")) for i in range(100)]
    curve, diagnostics = learner._build_curve(nan_samples)
    assert curve is None
    assert diagnostics["sample_count"] == 0


@pytest.mark.asyncio
async def test_charge_rate_learner_ignores_corrupt_storage(storage):
    from custom_components.localshift.learning import charge_rate as charge_rate_module

    storage.async_load.return_value = {"version": 99, "curves": "bad"}
    charge_rate_module.Store = MagicMock(return_value=storage)

    learner = ChargeRateLearner(MagicMock(), entry_id="entry-11")
    await learner.async_load()

    assert learner.get_curve("normal") is None
    assert learner.get_curve("boost") is None
    assert learner.diagnostics.get("load_error") is None
