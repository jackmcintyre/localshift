"""Plan -> summary -> sensor wiring of the discounted solar view (#1271, #1273).

Replay skips the display-only keys, so nothing else checks that a real
SolarAccuracyTracker below 1 reaches the optimizer summary sensor attributes.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from custom_components.localshift.sensors.optimizer import OptimizerSummarySensor
from scripts import replay_plan_baseline as rpb

CAPTURE = Path(__file__).resolve().parents[2] / "simulations/replay/2026-09-03.json"


def test_tracker_below_one_reaches_summary_sensor_attributes(monkeypatch):
    seen: dict = {}
    real_record = rpb._record

    def spy(data):
        seen["data"] = data
        return real_record(data)

    monkeypatch.setattr(rpb, "_record", spy)
    # live_like seeds a real SolarAccuracyTracker, so accuracy comes from it.
    rpb.replay(json.loads(CAPTURE.read_text()), "live_like")

    data = seen["data"]
    coordinator = MagicMock()
    coordinator.data = data
    attrs = OptimizerSummarySensor(coordinator, MagicMock()).extra_state_attributes

    raw = attrs["dw_entry_solar_only_soc_pct"]
    discounted = attrs["dw_entry_solar_only_discounted_soc_pct"]
    assert raw is not None
    assert discounted is not None
    assert discounted < raw
    accuracy = data.optimizer_summary["solar_forecast_accuracy_used"]
    assert accuracy is not None
    assert accuracy < 1.0
    assert data.optimizer_summary["dw_entry_gate_soc_pct"] is not None
