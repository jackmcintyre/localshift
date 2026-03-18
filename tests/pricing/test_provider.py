"""Tests for pricing provider protocol."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def test_protocol_has_required_methods():
    """Test PricingProvider protocol defines required interface."""
    from custom_components.localshift.pricing.provider import PricingProvider

    assert hasattr(PricingProvider, "name")
    assert hasattr(PricingProvider, "entity_prefix")
    assert hasattr(PricingProvider, "read_forecasts")
    assert hasattr(PricingProvider, "is_spike")


def test_amber_provider_implements_protocol():
    """Test AmberProvider correctly implements PricingProvider."""
    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()
    assert provider.name == "amber"
    assert provider.entity_prefix == "sensor.100h_"


def test_amber_provider_read_forecasts():
    """Test AmberProvider reads forecasts from separate entity."""
    from unittest.mock import MagicMock

    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()

    hass = MagicMock()
    forecast_state = MagicMock()
    forecast_state.attributes = {
        "forecasts": [
            {
                "start_time": "2026-03-16T12:00:00+00:00",
                "duration": 30,
                "per_kwh": 0.15,
                "spike_status": "none",
            },
            {
                "start_time": "2026-03-16T12:30:00+00:00",
                "duration": 30,
                "per_kwh": 0.85,
                "spike_status": "spike",
            },
        ]
    }
    hass.states.get.return_value = forecast_state

    slots = provider.read_forecasts(hass, "sensor.general_price")

    assert len(slots) == 2
    assert slots[0].per_kwh == 0.15
    assert slots[0].is_spike is False
    assert slots[1].per_kwh == 0.85
    assert slots[1].is_spike is True


def test_amber_provider_read_forecasts_entity_not_found():
    """Test AmberProvider returns empty list when entity not found."""
    from unittest.mock import MagicMock

    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()
    hass = MagicMock()
    hass.states.get.return_value = None

    slots = provider.read_forecasts(hass, "sensor.100h_general_price")
    assert slots == []


def test_amber_provider_read_forecasts_no_forecasts_attr():
    """Test AmberProvider returns empty list when forecasts attribute missing."""
    from unittest.mock import MagicMock

    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()
    hass = MagicMock()
    state = MagicMock()
    state.attributes = {}
    hass.states.get.return_value = state

    slots = provider.read_forecasts(hass, "sensor.100h_general_price")
    assert slots == []


def test_amber_provider_read_forecasts_skips_malformed():
    """Test AmberProvider skips malformed forecast entries."""
    from unittest.mock import MagicMock

    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()
    hass = MagicMock()
    state = MagicMock()
    state.attributes = {
        "forecasts": [
            {"start_time": "bad-ts", "per_kwh": 0.15},  # invalid timestamp
            {
                "start_time": "2026-03-16T12:00:00+00:00",
                "duration": 30,
                "per_kwh": 0.20,
                "spike_status": "none",
            },
        ]
    }
    hass.states.get.return_value = state

    slots = provider.read_forecasts(hass, "sensor.100h_general_price")
    assert len(slots) == 1
    assert slots[0].per_kwh == 0.20


def test_amber_provider_is_spike_false():
    """Test AmberProvider.is_spike returns False for non-spike entries."""
    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()
    assert provider.is_spike({"spike_status": "none"}) is False
    assert provider.is_spike({}) is False


def test_amber_express_provider_read_forecasts():
    """Test AmberExpressProvider reads from _price_detailed entity."""
    from unittest.mock import MagicMock

    from custom_components.localshift.pricing.provider import AmberExpressProvider

    provider = AmberExpressProvider()

    hass = MagicMock()
    detailed_state = MagicMock()
    detailed_state.attributes = {
        "forecasts": [
            {
                "start_time": "2026-03-16T12:00:01+11:00",
                "end_time": "2026-03-16T12:30:00+11:00",
                "per_kwh": 0.20,
                "demand_window": False,
            },
            {
                "start_time": "2026-03-16T12:30:01+11:00",
                "end_time": "2026-03-16T13:00:00+11:00",
                "per_kwh": 2.50,
                "demand_window": True,
            },
        ]
    }
    hass.states.get.return_value = detailed_state

    slots = provider.read_forecasts(hass, "sensor.amber_express_100h_general_price")

    assert len(slots) == 2
    assert slots[0].per_kwh == 0.20
    assert slots[0].duration == 30  # inferred from timestamps
    assert slots[0].is_spike is False
    assert slots[1].per_kwh == 2.50
    assert slots[1].duration == 30  # inferred from timestamps
    assert slots[1].is_spike is True  # demand_window=True


def test_amber_express_entity_prefix():
    """Test AmberExpressProvider uses correct prefix."""
    from custom_components.localshift.pricing.provider import AmberExpressProvider

    provider = AmberExpressProvider()
    assert provider.entity_prefix == "sensor.amber_express_100h_"
    assert provider.name == "amber_express"


def test_amber_express_provider_falls_back_to_simple_entity():
    """Test AmberExpressProvider falls back to simple entity when detailed missing."""
    from unittest.mock import MagicMock

    from custom_components.localshift.pricing.provider import AmberExpressProvider

    provider = AmberExpressProvider()

    hass = MagicMock()
    # Detailed entity returns None
    simple_state = MagicMock()
    simple_state.attributes = {
        "forecast": [
            {
                "start_time": "2026-03-16T12:00:01+11:00",
                "end_time": "2026-03-16T12:30:00+11:00",
                "per_kwh": 0.20,
                "demand_window": False,
            },
        ]
    }
    hass.states.get.side_effect = lambda eid: (
        None if "detailed" in eid else simple_state
    )

    slots = provider.read_forecasts(hass, "sensor.amber_express_100h_general_price")
    assert len(slots) == 1
    assert slots[0].per_kwh == 0.20


def test_amber_express_provider_returns_empty_when_no_forecasts():
    """Test AmberExpressProvider returns empty list when no forecasts anywhere."""
    from unittest.mock import MagicMock

    from custom_components.localshift.pricing.provider import AmberExpressProvider

    provider = AmberExpressProvider()
    hass = MagicMock()
    hass.states.get.return_value = None

    slots = provider.read_forecasts(hass, "sensor.amber_express_100h_general_price")
    assert slots == []


def test_amber_express_is_spike_true():
    """Test AmberExpressProvider.is_spike True when demand_window=True."""
    from custom_components.localshift.pricing.provider import AmberExpressProvider

    provider = AmberExpressProvider()
    assert provider.is_spike({"demand_window": True}) is True


def test_amber_express_auto_corrects_non_express_entity_id():
    """Test AmberExpressProvider auto-corrects non-Express entity IDs.

    When read_forecasts receives sensor.100h_general_price instead of
    sensor.amber_express_100h_general_price, it should auto-correct
    the entity ID to look up the _detailed variant with the Express prefix.
    """
    from unittest.mock import MagicMock

    from custom_components.localshift.pricing.provider import AmberExpressProvider

    provider = AmberExpressProvider()

    hass = MagicMock()
    detailed_state = MagicMock()
    detailed_state.attributes = {
        "forecasts": [
            {
                "start_time": "2026-03-16T12:00:01+11:00",
                "end_time": "2026-03-16T12:30:00+11:00",
                "per_kwh": 0.25,
                "demand_window": False,
            },
        ]
    }

    # Mock hass.states.get to return the detailed state ONLY when
    # called with the corrected Express entity ID
    def get_state(entity_id):
        if entity_id == "sensor.amber_express_100h_general_price_detailed":
            return detailed_state
        return None

    hass.states.get.side_effect = get_state

    # Call with non-Express entity ID (what happens in production bug)
    slots = provider.read_forecasts(hass, "sensor.100h_general_price")

    # Should auto-correct and find the forecasts
    assert len(slots) == 1
    assert slots[0].per_kwh == 0.25
    assert slots[0].duration == 30


def test_amber_express_is_spike_false():
    """Test AmberExpressProvider.is_spike False when demand_window not set."""
    from custom_components.localshift.pricing.provider import AmberExpressProvider

    provider = AmberExpressProvider()
    assert provider.is_spike({"demand_window": False}) is False
    assert provider.is_spike({}) is False


def test_amber_express_provider_skips_malformed():
    """Test AmberExpressProvider skips malformed forecast entries."""
    from unittest.mock import MagicMock

    from custom_components.localshift.pricing.provider import AmberExpressProvider

    provider = AmberExpressProvider()
    hass = MagicMock()
    state = MagicMock()
    state.attributes = {
        "forecasts": [
            {"start_time": "not-a-date", "per_kwh": 0.20},  # invalid timestamp
            {
                "start_time": "2026-03-16T12:00:01+11:00",
                "end_time": "2026-03-16T12:30:00+11:00",
                "per_kwh": 0.30,
                "demand_window": False,
            },
        ]
    }
    hass.states.get.return_value = state

    slots = provider.read_forecasts(hass, "sensor.amber_express_100h_general_price")
    assert len(slots) == 1
    assert slots[0].per_kwh == 0.30


# --- Duration inference tests ---


def test_normalize_slot_infers_5min_duration_from_timestamps():
    """Test _normalize_slot infers duration=5 when no duration field present.

    Real Amber Express entries have NO duration field. Duration must be
    inferred from (end_time - start_time). Express timestamps have a :01
    second offset (e.g., 09:25:01 -> 09:30:00 = 299 seconds), which must
    round to 5 minutes.
    """
    from custom_components.localshift.pricing.provider import AmberExpressProvider

    provider = AmberExpressProvider()
    raw = {
        "start_time": "2026-03-16T09:25:01+00:00",
        "end_time": "2026-03-16T09:30:00+00:00",
        "per_kwh": 0.15,
        "demand_window": False,
    }
    slot = provider._normalize_slot(raw)
    assert slot.duration == 5


def test_normalize_slot_infers_30min_duration_from_timestamps():
    """Test _normalize_slot infers duration=30 for 30-minute Express entries.

    Express 30-min entries also lack the duration field but have timestamps
    30 minutes apart (with :01 offset).
    """
    from custom_components.localshift.pricing.provider import AmberExpressProvider

    provider = AmberExpressProvider()
    raw = {
        "start_time": "2026-03-16T10:00:01+00:00",
        "end_time": "2026-03-16T10:30:00+00:00",
        "per_kwh": 0.25,
        "demand_window": False,
    }
    slot = provider._normalize_slot(raw)
    assert slot.duration == 30


def test_normalize_slot_infers_15min_duration_from_timestamps():
    """Test _normalize_slot infers duration=15 for 15-minute entries.

    _VALID_DURATIONS includes 15, so ensure the rounding works for entries
    that are approximately 15 minutes apart.
    """
    from custom_components.localshift.pricing.provider import AmberExpressProvider

    provider = AmberExpressProvider()
    raw = {
        "start_time": "2026-03-16T10:00:01+00:00",
        "end_time": "2026-03-16T10:15:00+00:00",
        "per_kwh": 0.20,
        "demand_window": False,
    }
    slot = provider._normalize_slot(raw)
    assert slot.duration == 15


def test_normalize_slot_uses_explicit_duration_field():
    """Test _normalize_slot uses explicit duration field when present.

    Regular Amber entries include a duration field. It should be used
    directly without inference.
    """
    from custom_components.localshift.pricing.provider import AmberProvider

    provider = AmberProvider()
    raw = {
        "start_time": "2026-03-16T10:00:00+00:00",
        "duration": 5,
        "per_kwh": 0.15,
        "spike_status": "none",
    }
    slot = provider._normalize_slot(raw)
    assert slot.duration == 5


def test_normalize_slot_defaults_to_30_without_duration_or_end_time():
    """Test _normalize_slot defaults to 30 when neither duration nor end_time.

    Fallback behavior: if we can't determine duration, assume 30 minutes.
    """
    from custom_components.localshift.pricing.provider import AmberExpressProvider

    provider = AmberExpressProvider()
    raw = {
        "start_time": "2026-03-16T10:00:00+00:00",
        "per_kwh": 0.15,
        "demand_window": False,
    }
    slot = provider._normalize_slot(raw)
    assert slot.duration == 30


def test_normalize_slot_boundary_10min_rounds_to_5():
    """Test 10-minute gap rounds to nearest valid duration.

    10 minutes is equidistant from 5 and 15. With min() and tuple ordering
    (5, 15, 30), the first element (5) wins on tie.
    """
    from custom_components.localshift.pricing.provider import AmberExpressProvider

    provider = AmberExpressProvider()
    raw = {
        "start_time": "2026-03-16T10:00:00+00:00",
        "end_time": "2026-03-16T10:10:00+00:00",
        "per_kwh": 0.15,
        "demand_window": False,
    }
    slot = provider._normalize_slot(raw)
    assert slot.duration == 5  # equidistant: min() picks first (5)


def test_normalize_slot_boundary_22min_rounds_to_15():
    """Test 22-minute gap rounds to 15 (closest valid duration).

    22 min: |22-5|=17, |22-15|=7, |22-30|=8. Closest is 15.
    """
    from custom_components.localshift.pricing.provider import AmberExpressProvider

    provider = AmberExpressProvider()
    raw = {
        "start_time": "2026-03-16T10:00:00+00:00",
        "end_time": "2026-03-16T10:22:00+00:00",
        "per_kwh": 0.15,
        "demand_window": False,
    }
    slot = provider._normalize_slot(raw)
    assert slot.duration == 15


def test_normalize_slot_negative_duration_defaults_to_5():
    """Test negative duration (end_time < start_time) picks 5.

    If end_time is before start_time, delta is negative. abs() in the
    rounding means smallest valid duration (5) wins.
    """
    from custom_components.localshift.pricing.provider import AmberExpressProvider

    provider = AmberExpressProvider()
    raw = {
        "start_time": "2026-03-16T10:30:00+00:00",
        "end_time": "2026-03-16T10:00:00+00:00",
        "per_kwh": 0.15,
        "demand_window": False,
    }
    slot = provider._normalize_slot(raw)
    assert slot.duration == 5  # -30 min: abs diffs = |35|, |45|, |60|; 5 wins
