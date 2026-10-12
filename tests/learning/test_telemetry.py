"""Save/load coverage for DecisionTelemetry (learning/telemetry.py).

The decision records are the only thing this class persists. The ``fake_store``
fixture replaces the methods of HA's ``Store`` class with an in-memory ledger
that remembers every key constructed, loaded or saved from any import site, so
the tests can also pin that the retired forecast-corrections store (#970) is
neither read nor written.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from custom_components.localshift.engine.outcomes import DecisionRecord
from custom_components.localshift.engine.types import PlannerAction
from custom_components.localshift.learning.telemetry import DecisionTelemetry

ENTRY_ID = "entry_abc"
DECISIONS_KEY = f"localshift.decision_outcomes.{ENTRY_ID}"
RETIRED_CORRECTIONS_KEY = f"localshift.forecast_corrections.{ENTRY_ID}"


class _StoreLedger:
    """In-memory disk plus a record of every Store construction, load and save."""

    def __init__(self) -> None:
        self.disk: dict[str, Any] = {}
        self.opened: list[str] = []
        self.loaded: list[str] = []
        self.saved: list[str] = []

    @property
    def touched(self) -> set[str]:
        return {*self.opened, *self.loaded, *self.saved}


@pytest.fixture
def fake_store():
    """Replace the behaviour of HA's ``Store`` class itself, not one import of it.

    Patching ``engine.outcomes.Store`` only covers that module's name binding: a
    ``Store`` imported anywhere else (``learning/telemetry.py`` included) would be
    the real class and never show up in the ledger. Patching the methods on the
    class records every construction, load and save whatever the import site.
    """
    ledger = _StoreLedger()

    def _init(self, hass, version, key, *args, **kwargs) -> None:
        _ = (hass, version, args, kwargs)
        self.key = key
        ledger.opened.append(key)

    async def _load(self) -> Any:
        ledger.loaded.append(self.key)
        return ledger.disk.get(self.key)

    async def _save(self, data: Any) -> None:
        ledger.saved.append(self.key)
        ledger.disk[self.key] = data

    def _delay_save(self, data_func, delay: float = 0) -> None:
        _ = delay
        ledger.saved.append(self.key)
        ledger.disk[self.key] = data_func()

    async def _remove(self) -> None:
        ledger.saved.append(self.key)
        ledger.disk.pop(self.key, None)

    with (
        patch.object(Store, "__init__", _init),
        patch.object(Store, "async_load", _load),
        patch.object(Store, "async_save", _save),
        patch.object(Store, "async_delay_save", _delay_save),
        patch.object(Store, "async_remove", _remove),
    ):
        yield ledger


def _telemetry() -> DecisionTelemetry:
    entry = MagicMock()
    entry.entry_id = ENTRY_ID
    return DecisionTelemetry(MagicMock(), entry)


def _completed_record() -> DecisionRecord:
    return DecisionRecord(
        timestamp=dt_util.now() - timedelta(hours=1),
        mode_chosen=PlannerAction.HOLD,
        previous_mode=PlannerAction.HOLD,
        soc_at_decision=55.0,
        general_price_at_decision=0.25,
        feed_in_price_at_decision=0.05,
        forecast_solar_remaining_kwh=4.0,
        forecast_consumption_remaining_kwh=6.0,
        cheap_price_threshold=0.15,
        battery_target_soc=95.0,
        weather_condition="sunny",
        day_of_week=1,
        hour_of_day=10,
        is_demand_window=False,
        duration_minutes=30.0,
        outcome_score=0.5,
    )


async def test_save_then_load_round_trips_the_decision_records(fake_store) -> None:
    first = _telemetry()
    await first.async_initialize()
    first.decision_tracker._completed_decisions.append(_completed_record())

    await first.async_save_all()

    second = _telemetry()
    await second.async_initialize()
    assert second.decision_tracker.completed_count == 1
    restored = second.decision_tracker.get_recent_decisions(hours=24)[0]
    assert restored.soc_at_decision == 55.0
    assert restored.mode_chosen == PlannerAction.HOLD


async def test_only_the_decision_store_is_touched(fake_store) -> None:
    """#970: a leftover corrections file is ignored, not loaded or rewritten."""
    stale = {"stats": {"1:11:summer": {"count": 3}}}
    fake_store.disk[RETIRED_CORRECTIONS_KEY] = stale

    telemetry = _telemetry()
    await telemetry.async_initialize()
    await telemetry.async_save_all()

    # The decision store really went through the ledger, so an empty result
    # below means "not touched", not "not observed".
    assert fake_store.opened == [DECISIONS_KEY]
    assert fake_store.loaded == [DECISIONS_KEY]
    assert fake_store.saved == [DECISIONS_KEY]

    assert [key for key in fake_store.touched if "forecast_corrections" in key] == []
    assert fake_store.touched == {DECISIONS_KEY}
    assert fake_store.disk[RETIRED_CORRECTIONS_KEY] is stale
    assert not hasattr(telemetry, "forecast_corrections")


async def test_the_ledger_sees_a_store_built_outside_the_decision_tracker(
    fake_store,
) -> None:
    """Guard on the guard: a Store imported at any other site is recorded too,
    which is what makes the "never opened" assertion above mean something."""
    from homeassistant.helpers import storage

    store = storage.Store(MagicMock(), 1, RETIRED_CORRECTIONS_KEY)
    await store.async_load()
    await store.async_save({"stats": {}})

    assert fake_store.opened == [RETIRED_CORRECTIONS_KEY]
    assert fake_store.loaded == [RETIRED_CORRECTIONS_KEY]
    assert fake_store.saved == [RETIRED_CORRECTIONS_KEY]


async def test_initialize_with_nothing_stored_starts_empty(fake_store) -> None:
    telemetry = _telemetry()
    await telemetry.async_initialize()

    assert telemetry.decision_tracker.completed_count == 0
    assert telemetry.decision_tracker.pending_count == 0


async def test_save_all_before_initialize_writes_nothing(fake_store) -> None:
    telemetry = _telemetry()

    await telemetry.async_save_all()

    assert fake_store.disk == {}


async def test_save_all_swallows_a_tracker_save_failure(fake_store, caplog) -> None:
    """Persistence runs on the tick path, so a storage error must not raise."""
    telemetry = _telemetry()
    await telemetry.async_initialize()
    telemetry.decision_tracker.async_save = AsyncMock(side_effect=OSError("disk full"))

    with caplog.at_level(logging.INFO):
        await telemetry.async_save_all()

    assert "Failed to save decision tracker: disk full" in caplog.text
    assert "Telemetry saved" not in caplog.text
