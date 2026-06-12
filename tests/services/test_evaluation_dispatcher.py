"""Tests for EvaluationDispatcher."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.util import dt as dt_util

from custom_components.localshift.coordinator.data import CoordinatorData
from custom_components.localshift.services.evaluation_dispatcher import (
    EvaluationDispatcher,
)
from tests.fixtures.ha_entities import MockState, MockStates


class StubCoordinator:
    def __init__(self) -> None:
        self.data = CoordinatorData()
        self.async_recompute_and_evaluate = AsyncMock()

    def read_state(self) -> None:
        return None


@patch("custom_components.localshift.services.evaluation_dispatcher.async_call_later")
def test_state_change_dispatches_evaluation(mock_call_later):
    """State change triggers read, notify, and deferred evaluation scheduling."""
    mock_unsub = MagicMock()
    mock_call_later.return_value = mock_unsub

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
    hass.async_create_task.assert_not_called()
    mock_call_later.assert_called_once()

    timer_callback = mock_call_later.call_args[0][2]
    timer_callback(MagicMock())

    hass.async_create_task.assert_called_once()
    assert hass.async_create_task.call_args.args[1] == "localshift_evaluate_coalesced"


@patch("custom_components.localshift.services.evaluation_dispatcher.async_call_later")
def test_multiple_state_changes_coalesce_into_single_evaluation(mock_call_later):
    """Multiple entity changes within the window trigger one evaluation."""
    timer_unsubs = [MagicMock(), MagicMock(), MagicMock()]
    mock_call_later.side_effect = timer_unsubs

    hass = MagicMock()
    hass.async_create_task = MagicMock()

    state_machine = MagicMock()
    state_machine.in_mode_transition = False

    read_state = MagicMock()

    dispatcher = EvaluationDispatcher(
        hass,
        lambda _key: "sensor.price",
        read_state,
        MagicMock(),
        AsyncMock(),
        state_machine,
        timedelta(minutes=10),
    )

    dispatcher.on_state_change(MagicMock())
    dispatcher.on_state_change(MagicMock())
    dispatcher.on_state_change(MagicMock())

    assert read_state.call_count == 3
    assert mock_call_later.call_count == 3
    timer_unsubs[0].assert_called_once()
    timer_unsubs[1].assert_called_once()
    timer_unsubs[2].assert_not_called()
    hass.async_create_task.assert_not_called()

    timer_callback = mock_call_later.call_args[0][2]
    timer_callback(MagicMock())

    hass.async_create_task.assert_called_once()


@patch("custom_components.localshift.services.evaluation_dispatcher.async_call_later")
def test_cancel_pending_coalesce(mock_call_later):
    """Pending coalesced evaluation can be cancelled."""
    mock_unsub = MagicMock()
    mock_call_later.return_value = mock_unsub

    hass = MagicMock()
    hass.async_create_task = MagicMock()

    state_machine = MagicMock()
    state_machine.in_mode_transition = False

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

    dispatcher.cancel_pending_coalesce()
    mock_unsub.assert_called_once()
    assert dispatcher._coalesce_unsub is None
    assert dispatcher._coalesce_count == 0


@patch("custom_components.localshift.services.evaluation_dispatcher.async_call_later")
def test_coalesce_timer_skips_if_transition_started(mock_call_later):
    """Timer callback skips evaluation if transition starts before expiry."""
    mock_unsub = MagicMock()
    mock_call_later.return_value = mock_unsub

    hass = MagicMock()
    hass.async_create_task = MagicMock()

    state_machine = MagicMock()
    state_machine.in_mode_transition = False

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
    state_machine.in_mode_transition = True

    timer_callback = mock_call_later.call_args[0][2]
    timer_callback(MagicMock())

    hass.async_create_task.assert_not_called()


@patch("custom_components.localshift.services.evaluation_dispatcher.async_call_later")
def test_coalesce_timer_skips_if_state_machine_removed(mock_call_later):
    """Timer callback skips evaluation if state machine is missing."""
    mock_unsub = MagicMock()
    mock_call_later.return_value = mock_unsub

    hass = MagicMock()
    hass.async_create_task = MagicMock()

    state_machine = MagicMock()
    state_machine.in_mode_transition = False

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
    dispatcher._state_machine = None

    timer_callback = mock_call_later.call_args[0][2]
    timer_callback(MagicMock())

    hass.async_create_task.assert_not_called()


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


def test_state_change_skips_when_state_machine_missing():
    hass = MagicMock()
    hass.async_create_task = MagicMock()

    dispatcher = EvaluationDispatcher(
        hass,
        lambda _key: "sensor.price",
        MagicMock(),
        MagicMock(),
        AsyncMock(),
        None,
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


def test_fast_tick_ignores_missing_price_entity():
    hass = MagicMock()
    hass.async_create_task = MagicMock()
    hass.states.get = MagicMock(return_value=None)

    now = dt_util.now()
    dispatcher = EvaluationDispatcher(
        hass,
        lambda _key: "sensor.price",
        MagicMock(),
        MagicMock(),
        AsyncMock(),
        MagicMock(in_mode_transition=False),
        timedelta(minutes=10),
    )
    dispatcher._load_deviation_detector = MagicMock()
    dispatcher._load_deviation_detector.evaluate.return_value = False

    stale_price = dispatcher.on_fast_tick(now)

    assert stale_price is False
    assert hass.async_create_task.call_args.args[1] == "localshift_evaluate_periodic"


def test_fast_tick_ignores_invalid_price_state():
    hass = MagicMock()
    hass.async_create_task = MagicMock()

    now = dt_util.now()
    states = MockStates({
        "sensor.price": MockState(
            entity_id="sensor.price",
            state="unknown",
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
    dispatcher._load_deviation_detector = MagicMock()
    dispatcher._load_deviation_detector.evaluate.return_value = False

    stale_price = dispatcher.on_fast_tick(now)

    assert stale_price is False
    assert hass.async_create_task.call_args.args[1] == "localshift_evaluate_periodic"


def test_fast_tick_triggers_load_deviation_reoptimization():
    hass = MagicMock()
    hass.async_create_task = MagicMock()

    coordinator = StubCoordinator()

    dispatcher = EvaluationDispatcher(
        hass,
        lambda _key: "sensor.price",
        coordinator.read_state,
        MagicMock(),
        AsyncMock(),
        MagicMock(in_mode_transition=False),
        timedelta(minutes=10),
    )
    dispatcher._load_deviation_detector = MagicMock()
    dispatcher._load_deviation_detector.evaluate.return_value = True

    now = dt_util.now()
    dispatcher.on_fast_tick(now)

    dispatcher._load_deviation_detector.evaluate.assert_called_once_with(
        coordinator.data, now
    )
    assert (
        hass.async_create_task.call_args.args[1]
        == "localshift_reoptimize_load_deviation"
    )
    # #622 gate replacement: a reoptimization may update the plan but must NOT
    # grant a mode re-decision.
    coordinator.async_recompute_and_evaluate.assert_called_once_with(
        invalidate_decision=False
    )


def test_load_deviation_reoptimization_does_not_invalidate_decision():
    """#622 gate replacement: load-deviation reoptimizer passes invalidate_decision=False."""
    hass = MagicMock()
    hass.async_create_task = MagicMock()

    coordinator = StubCoordinator()

    dispatcher = EvaluationDispatcher(
        hass,
        lambda _key: "sensor.price",
        coordinator.read_state,
        MagicMock(),
        AsyncMock(),
        MagicMock(in_mode_transition=False),
        timedelta(minutes=10),
    )
    # Load-deviation does not fire; solar-event does.
    dispatcher._load_deviation_detector = MagicMock()
    dispatcher._load_deviation_detector.evaluate.return_value = False
    dispatcher._solar_event_detector = MagicMock()
    dispatcher._solar_event_detector.evaluate.return_value = True

    dispatcher.on_fast_tick(dt_util.now())

    assert (
        hass.async_create_task.call_args.args[1] == "localshift_reoptimize_solar_event"
    )
    coordinator.async_recompute_and_evaluate.assert_called_once_with(
        invalidate_decision=False
    )


def test_fast_tick_uses_periodic_evaluation_when_no_load_deviation_trigger():
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

    coordinator = StubCoordinator()
    dispatcher = EvaluationDispatcher(
        hass,
        lambda _key: "sensor.price",
        coordinator.read_state,
        MagicMock(),
        AsyncMock(),
        MagicMock(in_mode_transition=False),
        timedelta(minutes=10),
    )
    dispatcher._load_deviation_detector = MagicMock()
    dispatcher._load_deviation_detector.evaluate.return_value = False

    dispatcher.on_fast_tick(now)

    dispatcher._load_deviation_detector.evaluate.assert_called_once_with(
        coordinator.data, now
    )
    assert hass.async_create_task.call_args.args[1] == "localshift_evaluate_periodic"
