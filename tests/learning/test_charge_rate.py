"""Tests for charge rate learning overrides."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.localshift.const import (
    POWER_SIGN_POSITIVE,
)
from custom_components.localshift.learning.charge_rate import ChargeRateLearner


def test_charge_rate_learner_respects_power_sign_override(history, decisions):
    """Power sign override bypasses auto-detection."""
    learner = ChargeRateLearner(
        MagicMock(),
        entry_id="entry-override",
        power_sign_override=POWER_SIGN_POSITIVE,
    )
    inverted_power = [(ts, -abs(value)) for ts, value in history["power_history"]]

    learner.update_from_history(
        inverted_power,
        history["soc_history"],
        decisions,
    )

    assert learner.diagnostics["power_sign_override"] == POWER_SIGN_POSITIVE
    assert learner.diagnostics["power_sign_inverted"] is False


@pytest.mark.asyncio
async def test_charge_rate_learner_invalidate_clears_curves(
    storage, history, decisions, monkeypatch
):
    from custom_components.localshift.learning import charge_rate as charge_rate_module

    hass = MagicMock()
    monkeypatch.setattr(charge_rate_module, "Store", MagicMock(return_value=storage))
    learner = ChargeRateLearner(hass, entry_id="entry-clear")

    learner.update_from_history(
        history["power_history"],
        history["soc_history"],
        decisions,
    )
    assert learner.get_curve("normal") is not None

    await learner.async_invalidate()

    assert learner.get_curve("normal") is None
