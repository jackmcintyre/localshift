"""Issue #1205: price entities missing at HA start-up are not a warning.

Amber Express can create its price entities a few seconds after LocalShift's
first read. During the start-up grace that is expected (DEBUG); the same gap
after the grace is a real outage (WARNING). Once the prices arrive, the next
read picks them up.
"""

import logging
from unittest.mock import MagicMock

import pytest

from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.pricing.provider import AmberExpressProvider
from custom_components.localshift.state.reader import StateReader

GENERAL = "sensor.amber_express_100h_general_price"
FEED_IN = "sensor.amber_express_100h_feed_in_price"
SLOT = {
    "start_time": "2026-10-17T17:00:00+00:00",
    "end_time": "2026-10-17T17:30:00+00:00",
    "duration": 30,
    "per_kwh": 0.25,
}


def _state(value, attributes=None):
    state = MagicMock()
    state.state = value
    state.attributes = attributes or {}
    return state


def _hass(prices_present: bool):
    """hass whose Amber Express entities exist only when prices_present."""
    hass = MagicMock()

    def get(entity_id):
        if entity_id in (GENERAL, FEED_IN):
            if not prices_present:
                return None
            return _state("0.25", {"detailedForecast": [SLOT]})
        return _state("50")

    hass.states.get.side_effect = get
    return hass


@pytest.fixture
def grace():
    return {"active": True}


@pytest.fixture
def reader(mock_entry, mock_entity_validator, grace):
    provider = AmberExpressProvider()
    provider.startup_grace_active = lambda: grace["active"]
    return StateReader(
        _hass(False),
        mock_entry,
        mock_entity_validator,
        provider,
        startup_grace_active=lambda: grace["active"],
    )


def _price_records(caplog, level):
    return [
        r
        for r in caplog.records
        if r.levelno == level
        and ("price entity" in r.getMessage() or "No forecasts" in r.getMessage())
    ]


def test_prices_missing_in_grace_log_no_warning(reader, caplog, mock_entry):
    mock_entry.data = {
        **mock_entry.data,
        "pricing_general_price": GENERAL,
        "pricing_feed_in_price": FEED_IN,
    }
    with caplog.at_level(logging.DEBUG):
        data = CoordinatorData()
        reader.read_all_external_state(data)

    assert data.prices_available is False
    assert _price_records(caplog, logging.WARNING) == []
    assert _price_records(caplog, logging.DEBUG)


def test_prices_arrive_after_first_read(reader, caplog, grace, mock_entry):
    mock_entry.data = {
        **mock_entry.data,
        "pricing_general_price": GENERAL,
        "pricing_feed_in_price": FEED_IN,
    }
    with caplog.at_level(logging.DEBUG):
        first = CoordinatorData()
        reader.read_all_external_state(first)
        # The integration finishes loading; the next read sees the prices.
        reader.hass = _hass(True)
        second = CoordinatorData()
        reader.read_all_external_state(second)

    assert first.prices_available is False
    assert second.prices_available is True
    assert second.general_price == 0.25
    assert second.general_forecast
    assert _price_records(caplog, logging.WARNING) == []


def test_prices_missing_after_grace_still_warns(reader, caplog, grace, mock_entry):
    mock_entry.data = {
        **mock_entry.data,
        "pricing_general_price": GENERAL,
        "pricing_feed_in_price": FEED_IN,
    }
    grace["active"] = False
    with caplog.at_level(logging.DEBUG):
        data = CoordinatorData()
        reader.read_all_external_state(data)

    assert data.prices_available is False
    messages = [r.getMessage() for r in _price_records(caplog, logging.WARNING)]
    assert any("General price entity" in m for m in messages)
    assert any("Feed-in price entity" in m for m in messages)
    assert any("No forecasts found" in m for m in messages)


def test_provider_without_grace_callback_warns(caplog):
    provider = AmberExpressProvider()
    with caplog.at_level(logging.DEBUG):
        assert provider.read_forecasts(_hass(False), GENERAL) == []
    assert any(r.levelno == logging.WARNING for r in caplog.records)
