"""Tests for EvaluationDispatcher."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from custom_components.localshift.services.evaluation_dispatcher import (
    EvaluationDispatcher,
)
from homeassistant.util import dt as dt_util
from tests.fixtures.ha_entities import MockState, MockStates


def test_state_change_dispatches_evaluation():
    """State change triggers read, notify, and evaluation scheduling."""
    hass = MagicMock()
    hass.async_create_task = MagicMock()

    state_machine = MagicMock()
    state_machine.in_mode_transition = False

    read_state = MagicMock()
    notify_listeners = MagicMock()
    evaluate_state_machine = AsyncMock()

    dispatcher = EvaluationDispatcher(
        hass,
        lambda _key: "sensor.price",
        read_state,
        notify_listeners,
        evaluate_state_machine,
        state_machine,
        timedelta(minutes=10),
    )

    dispatcher.on_state_change(MagicMock())

    read_state.assert_called_once()
    notify_listeners.assert_called_once()
    hass.async_create_task.assert_called_once()
    assert (
        hass.async_create_task.call_args.args[1] == "localshift_evaluate_state_change"
    )


def test_state_change_skips_during_transition():
    """State changes are ignored during mode transitions."""
    hass = MagicMock()
    hass.async_create_task = MagicMock()

    state_machine = MagicMock()
    state_machine.in_mode_transition = True

    dispatcher = EvaluationDispatcher(
        hass,
        lambda _key: "sensor.price",
        MagicMock(),
        MagicMock(),
        AsyncMock(),
        state_machine,
        timedelta(minutes=10),
    )

    dispatcher.on_state_change(MagicMock())

    hass.async_create_task.assert_not_called()


def test_fast_tick_triggers_stale_price_evaluation():
    """Stale prices dispatch a stale-price evaluation."""
    hass = MagicMock()
    hass.async_create_task = MagicMock()

    now = dt_util.now()
    states = MockStates({
        "sensor.price": MockState(
            entity_id="sensor.price",
            state="0.25",
            attributes={},
            last_updated=now - timedelta(minutes=11),
        )
    })
    hass.states.get = states.get

    dispatcher = EvaluationDispatcher(
        hass,
        lambda _key: "sensor.price",
        MagicMock(),
        MagicMock(),
        AsyncMock(),
        MagicMock(in_mode_transition=False),
        timedelta(minutes=10),
    )

    dispatcher.on_fast_tick(now)

    assert hass.async_create_task.call_args.args[1] == "localshift_evaluate_stale_price"


def test_fast_tick_triggers_periodic_evaluation_when_fresh():
    """Fresh prices dispatch periodic evaluation."""
    hass = MagicMock()
    hass.async_create_task = MagicMock()

    now = dt_util.now()
    states = MockStates({
        "sensor.price": MockState(
            entity_id="sensor.price",
            state="0.25",
            attributes={},
            last_updated=now - timedelta(minutes=2),
        )
    })
    hass.states.get = states.get

    dispatcher = EvaluationDispatcher(
        hass,
        lambda _key: "sensor.price",
        MagicMock(),
        MagicMock(),
        AsyncMock(),
        MagicMock(in_mode_transition=False),
        timedelta(minutes=10),
    )

    dispatcher.on_fast_tick(now)

    assert hass.async_create_task.call_args.args[1] == "localshift_evaluate_periodic"
