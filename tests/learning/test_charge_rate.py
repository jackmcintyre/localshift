from .test_charge_rate_curve import *  # noqa: F401,F403
from .test_charge_rate_learner import *  # noqa: F401,F403

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.localshift.const import POWER_SIGN_NEGATIVE, POWER_SIGN_POSITIVE
from custom_components.localshift.learning import charge_rate as charge_rate_module
from custom_components.localshift.learning.charge_rate import (
    ChargeRateLearner,
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


def test_update_mode_analysis_records_discharge_bins() -> None:
    learner = ChargeRateLearner(hass=MagicMock(), entry_id="entry")
    base = datetime(2026, 1, 1, 0, 0)
    power = [(base + timedelta(minutes=i), -2.0) for i in range(12)]
    soc = [(base + timedelta(minutes=i), 60.0 + (i * 0.1)) for i in range(12)]
    mode = [(base + timedelta(minutes=i), "self_consumption") for i in range(12)]

    assert learner.update_mode_analysis_from_history(power, soc, mode) is True
    rows = learner.get_mode_analysis_payload()["soc_bins_1pct_by_mode"][
        "self_consumption"
    ]
    assert any(row["discharge_kw"] > 0 for row in rows)


def test_update_from_history_respects_sign_overrides() -> None:
    base = datetime(2026, 1, 1, 0, 0)
    power = [(base + timedelta(minutes=15 * i), 3.0) for i in range(10)]
    soc = [(base + timedelta(minutes=15 * i), 40.0 + i) for i in range(10)]
    decisions = [
        SimpleNamespace(
            timestamp=base + timedelta(minutes=15 * i), mode_chosen="charge_grid_normal"
        )
        for i in range(10)
    ]

    learner_pos = ChargeRateLearner(
        hass=MagicMock(),
        entry_id="entry",
        power_sign_override=POWER_SIGN_POSITIVE,
    )
    learner_pos.update_from_history(power, soc, decisions)
    assert learner_pos.diagnostics["power_sign_inverted"] is False

    learner_neg = ChargeRateLearner(
        hass=MagicMock(),
        entry_id="entry",
        power_sign_override=POWER_SIGN_NEGATIVE,
    )
    learner_neg.update_from_history(power, soc, decisions)
    assert learner_neg.diagnostics["power_sign_inverted"] is True
