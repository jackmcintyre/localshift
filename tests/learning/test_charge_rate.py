from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.const import POWER_SIGN_POSITIVE
from custom_components.localshift.learning.charge_rate import (
    ChargeRateLearner,
    _infer_mode_analysis_power_sign,
    _is_valid_mode_payload,
    _normalize_mode_history,
)


def test_normalize_mode_history_handles_none_and_objects() -> None:
    now = datetime.now()
    rows = _normalize_mode_history([
        None,
        SimpleNamespace(last_changed=now, state="self_consumption"),
        SimpleNamespace(last_updated=now + timedelta(seconds=1), state="unknown"),
    ])
    assert rows == [(now, "self_consumption")]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"generated_at": 1, "method": {}, "window": {}, "soc_bins_1pct_by_mode": {}},
        {
            "generated_at": datetime.now().isoformat(),
            "method": {"soc_bin_pct": 2, "resample": "1m"},
            "window": {"history_window_days": 14},
            "soc_bins_1pct_by_mode": {},
        },
        {
            "generated_at": datetime.now().isoformat(),
            "method": {"soc_bin_pct": 1, "resample": "5m"},
            "window": {"history_window_days": 14},
            "soc_bins_1pct_by_mode": {},
        },
        {
            "generated_at": datetime.now().isoformat(),
            "method": {"soc_bin_pct": 1, "resample": "1m"},
            "window": {"history_window_days": 7},
            "soc_bins_1pct_by_mode": {},
        },
    ],
)
def test_is_valid_mode_payload_rejects_invalid_payloads(payload) -> None:
    assert _is_valid_mode_payload(payload) is False


def test_is_valid_mode_payload_rejects_bad_rows() -> None:
    payload = {
        "generated_at": datetime.now().isoformat(),
        "method": {"soc_bin_pct": 1, "resample": "1m"},
        "window": {"history_window_days": 14},
        "soc_bins_1pct_by_mode": {
            "boost_charging": [
                {"soc": "x", "n": 1, "charge_kw": 1.0, "discharge_kw": 0.0}
            ],
            "grid_charging": [],
            "proactive_export": [],
            "self_consumption": [],
            "spike_discharge": [],
            "unknown": [],
        },
    }
    assert _is_valid_mode_payload(payload) is False


@pytest.mark.parametrize(
    "row",
    [
        {"soc": -1, "n": 1, "charge_kw": 1.0, "discharge_kw": 0.0},
        {"soc": 101, "n": 1, "charge_kw": 1.0, "discharge_kw": 0.0},
        {"soc": 50, "n": 0, "charge_kw": 1.0, "discharge_kw": 0.0},
        {"soc": 50, "n": 1, "charge_kw": float("nan"), "discharge_kw": 0.0},
        {"soc": 50, "n": 1, "charge_kw": 1.0, "discharge_kw": float("inf")},
    ],
)
def test_is_valid_mode_payload_rejects_invalid_row_values(row) -> None:
    payload = {
        "generated_at": datetime.now().isoformat(),
        "method": {"soc_bin_pct": 1, "resample": "1m"},
        "window": {"history_window_days": 14},
        "soc_bins_1pct_by_mode": {
            "boost_charging": [],
            "grid_charging": [],
            "proactive_export": [],
            "self_consumption": [row],
            "spike_discharge": [],
            "unknown": [],
        },
    }
    assert _is_valid_mode_payload(payload) is False


@pytest.mark.asyncio
async def test_configure_and_async_invalidate_paths() -> None:
    learner = ChargeRateLearner(hass=MagicMock(), entry_id="entry")
    learner._store = MagicMock()
    learner._store.async_save = AsyncMock()

    learner.configure("sensor.p", "sensor.s", POWER_SIGN_POSITIVE)
    assert learner._power_entity_id == "sensor.p"
    assert learner._soc_entity_id == "sensor.s"
    assert learner._power_sign_override == POWER_SIGN_POSITIVE

    learner._curves = {"normal": MagicMock()}
    learner._diagnostics = {"x": 1}
    learner._mode_analysis_payload = {"y": 2}
    learner._updated_at = datetime.now()

    await learner.async_invalidate()
    assert learner._curves == {}
    assert learner._diagnostics == {}
    assert learner._mode_analysis_payload == {}
    assert learner._updated_at is None
    learner._store.async_save.assert_awaited_once()


def test_update_mode_analysis_handles_no_overlap() -> None:
    learner = ChargeRateLearner(hass=MagicMock(), entry_id="entry")
    power = [(datetime(2026, 1, 1, 0, 0), 1.0)]
    soc = [(datetime(2026, 1, 1, 0, 0), 50.0)]
    mode = [(datetime(2026, 1, 2, 0, 0), "self_consumption")]
    assert learner.update_mode_analysis_from_history(power, soc, mode) is False
    assert learner.get_mode_analysis_payload() == {}


def test_infer_mode_analysis_power_sign_detects_negative_charging() -> None:
    base = datetime(2026, 1, 1, 0, 0)
    power = [(base + timedelta(minutes=i), -2.0 if i < 4 else 1.0) for i in range(8)]
    soc = [
        (
            base + timedelta(minutes=i),
            40.0 + (0.5 * i if i < 4 else 2.0 - 0.5 * (i - 4)),
        )
        for i in range(8)
    ]
    assert _infer_mode_analysis_power_sign(power, soc) == -1.0
