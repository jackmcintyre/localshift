"""Issue #1118: PROACTIVE_EXPORT reserve re-step timing and Tesla's 81-99 reset.

Replays the 2026-10-12 afternoon ($10.48/kWh feed-in) through
``StateMachine.evaluate_state_machine`` against a fake Powerwall that does what
the real one did that day: silently reset any reserve from 81 to 99 back to 80.

Home Assistant history, 15:30-16:00 AEDT (``sensor.my_home_percentage_charged``
only reports every five minutes):

    15:30 97.0   15:35 92.1   15:40 86.9   15:45 82.0
    15:50 80.5   15:55 79.0   16:00 77.5

What production wrote: 92.75 on entry (bounced to 80, and the health check
re-ran the whole transition a minute later), then 87.1 and 81.9 (both bounced),
77.0 at 15:45 and 72.5 at 16:00. The 15:55 sample sat 2.0 points above the
reserve of 77, outside the 1-point trigger, so the step waited another five
minutes while battery output sagged from 2.4 kW to 2.0 kW.

The wider trigger applies only while the retailer's price-spike flag
(``data.price_spike``) is on, and the flag was not on throughout: both spike
sensors dropped out at 16:00 and came back at 16:05, on the same 5-minute
boundary the SOC sample arrives on, and one of them read off or unavailable for
a minute twice before that. Tests that depend on step timing replay the
recorded flag (``_replay_recorded``); the rest hold it on. The ordinary-price
controls pin the old timing with it off.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.localshift.const import (
    BACKUP_RESERVE_MAX_VALID,
    PROACTIVE_EXPORT_RESERVE_STEP_TRIGGER_PERCENT,
    PROACTIVE_EXPORT_RESERVE_STEP_TRIGGER_SPIKE_PERCENT,
    PROACTIVE_EXPORT_SOC_BUFFER_PERCENT,
    BatteryMode,
)
from custom_components.localshift.coordinator import CoordinatorData
from custom_components.localshift.state.machine import StateMachine
from custom_components.localshift.state.mode_configs import (
    calculate_proactive_export_reserve,
)
from custom_components.localshift.utils.validation import IntegrationStatus

AEDT = timezone(timedelta(hours=11))
MINIMUM_TARGET_SOC = 10.0  # live number.localshift_minimum_target_soc

# (minutes after 15:30, SOC) as the sensor reported it on 2026-10-12.
SOC_SAMPLES_2026_10_12 = (
    (0, 97.0031841168758),
    (5, 92.133358306799),
    (10, 86.8889305113317),
    (15, 82.0191047012549),
    (20, 80.5206967596928),
    (25, 79.0222888181307),
    (30, 77.5238808765687),
)


def tesla_accepts(reserve: float) -> int:
    """What the Powerwall stores for a requested reserve (firmware July 2025)."""
    if BACKUP_RESERVE_MAX_VALID < reserve < 100:
        return BACKUP_RESERVE_MAX_VALID
    return int(reserve)


class FakePowerwall:
    """Stands in for BatteryController + the hardware behind it.

    ``set_proactive_export`` uses the shared #974 formula exactly as the real
    controller does (tests/test_battery_controller.py covers the real one).
    """

    def __init__(self) -> None:
        self.soc = 0.0
        self.hardware_reserve = 10
        self.requested: list[float] = []
        self.step_requests: list[tuple[float, float]] = []
        self.transitions = 0
        self.health_expectations: list[float] = []

        # A MagicMock controller, so the unrelated calls the state machine
        # makes (entity ids, state snapshots) need no modelling here.
        self.controller = MagicMock()
        self.controller.read_fresh_soc = MagicMock(side_effect=lambda: self.soc)
        self.controller.set_self_consumption = AsyncMock(return_value=True)
        self.controller.set_proactive_export = AsyncMock(side_effect=self._transition)
        self.controller.set_proactive_export_reserve = AsyncMock(side_effect=self._step)
        self.controller.verify_current_state = AsyncMock(side_effect=self._verify)

    def _write(self, reserve: float) -> None:
        self.requested.append(reserve)
        self.hardware_reserve = tesla_accepts(reserve)

    async def _transition(self, data, dry_run=False) -> bool:
        self.transitions += 1
        floor = max(MINIMUM_TARGET_SOC, float(data.planner_export_floor_pct))
        self._write(calculate_proactive_export_reserve(data.soc, floor))
        return True

    async def _step(self, reserve, dry_run=False) -> bool:
        self.step_requests.append((self.soc, reserve))
        self._write(reserve)
        return True

    async def _verify(self, **expected) -> bool:
        self.health_expectations.append(expected["expected_backup_reserve"])
        return abs(self.hardware_reserve - expected["expected_backup_reserve"]) < 1

    @property
    def bounced(self) -> list[float]:
        return [r for r in self.requested if BACKUP_RESERVE_MAX_VALID < r < 100]


def _option(key, default=None):
    return {"minimum_target_soc": MINIMUM_TARGET_SOC}.get(key, default)


def _switch(key):
    return {"automation_enabled": True, "dry_run": False}.get(key, False)


@pytest.fixture
def powerwall():
    return FakePowerwall()


def _build_machine(powerwall):
    notifications = MagicMock()
    for name in (
        "send_transition_notification",
        "send_transition_failed_notification",
        "send_health_correction_notification",
        "send_manual_override_timeout_notification",
        "send_automation_disabled_notification",
        "send_tesla_override_notification",
    ):
        setattr(notifications, name, AsyncMock())
    validator = MagicMock()
    validator.should_allow_automation = MagicMock(return_value=True)
    validator.status = IntegrationStatus.OK
    validator.errors = []
    validator.warnings = []
    sm = StateMachine(powerwall.controller, notifications, _switch, _option, validator)
    sm._commanded_mode = BatteryMode.SELF_CONSUMPTION
    return sm


@pytest.fixture
def machine(powerwall):
    return _build_machine(powerwall)


def _build_data():
    d = CoordinatorData()
    d.operation_mode = "autonomous"
    d.backup_reserve = 10
    d.grid_power_kw = -7.0
    d.battery_power_kw = 7.5
    d.load_power_kw = 0.5
    d.solar_power_kw = 2.0
    d.general_price = 10.6
    d.feed_in_price = 10.48
    # Default for the tests that do not depend on the flag's timing. The
    # recorded flag is replayed by _replay_recorded below.
    d.price_spike = True
    d.manual_override = False
    d.decision_log = []
    d.daily_forecast_soc_15min = []
    d.automation_ready = True
    d.active_mode = BatteryMode.PROACTIVE_EXPORT
    return d


@pytest.fixture
def data():
    return _build_data()


async def _replay(machine, powerwall, data, samples, minutes):
    """Run one state-machine evaluation per minute, as the coordinator does."""
    engine = MagicMock()
    start = datetime(2026, 10, 12, 15, 30, 15, tzinfo=AEDT)
    by_minute = dict(samples)
    soc = samples[0][1]
    with patch("custom_components.localshift.state.machine.dt_util.now") as now:
        for minute in range(minutes + 1):
            soc = by_minute.get(minute, soc)
            powerwall.soc = soc
            data.soc = soc
            data.backup_reserve = powerwall.hardware_reserve
            now.return_value = start + timedelta(minutes=minute)
            await machine.evaluate_state_machine(data, engine)


@pytest.mark.asyncio
async def test_replay_writes_no_reserve_tesla_resets(machine, powerwall, data):
    """From a full battery nothing in 81-99 is ever sent to the Powerwall."""
    await _replay(machine, powerwall, data, SOC_SAMPLES_2026_10_12, 30)

    assert machine._commanded_mode == BatteryMode.PROACTIVE_EXPORT
    assert powerwall.requested, "export never wrote a reserve"
    assert powerwall.bounced == []


@pytest.mark.asyncio
async def test_replay_health_check_agrees_with_written_reserve(
    machine, powerwall, data
):
    """The health check expects what the Powerwall holds, so it never re-runs
    the transition (2026-10-12 15:31:33: one correction, a minute after entry).
    """
    await _replay(machine, powerwall, data, SOC_SAMPLES_2026_10_12, 30)

    assert powerwall.transitions == 1
    assert powerwall.health_expectations, "health check never ran"
    assert all(
        expected <= BACKUP_RESERVE_MAX_VALID
        for expected in powerwall.health_expectations
    )


@pytest.mark.asyncio
async def test_replay_steps_before_soc_reaches_the_reserve(machine, powerwall, data):
    """The 15:55 sample (2.0 above the reserve) re-steps; production waited
    until 16:00, when SOC was 0.5 above it.
    """
    await _replay(machine, powerwall, data, SOC_SAMPLES_2026_10_12, 30)

    assert [reserve for _, reserve in powerwall.step_requests] == [
        pytest.approx(82.0191047012549 - 5),
        pytest.approx(79.0222888181307 - 5),
    ]


@pytest.mark.asyncio
async def test_replay_keeps_the_soc_5_leash(machine, powerwall, data):
    """Every step still trails SOC by the 5-point buffer: never deeper, and the
    reserve only ever moves down while export stays selected.
    """
    await _replay(machine, powerwall, data, SOC_SAMPLES_2026_10_12, 30)

    assert powerwall.step_requests
    for soc, reserve in powerwall.step_requests:
        assert reserve == pytest.approx(soc - PROACTIVE_EXPORT_SOC_BUFFER_PERCENT)
    stepped = [reserve for _, reserve in powerwall.step_requests]
    assert stepped == sorted(stepped, reverse=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("spike", "trigger"),
    [
        (True, PROACTIVE_EXPORT_RESERVE_STEP_TRIGGER_SPIKE_PERCENT),
        (False, PROACTIVE_EXPORT_RESERVE_STEP_TRIGGER_PERCENT),
    ],
)
async def test_no_rewrite_while_soc_holds_above_the_trigger(
    machine, powerwall, data, spike, trigger
):
    """Why neither trigger can flap: after a write the reserve sits 5 below SOC,
    so SOC must fall a further (5 - trigger) points before the next one. Twenty
    evaluations at a steady SOC write exactly once (the entry).
    """
    data.price_spike = spike
    await _replay(machine, powerwall, data, ((0, 60.0),), 20)

    assert powerwall.requested == [pytest.approx(55.0)]

    # Just outside the trigger: still nothing.
    powerwall.soc = data.soc = 55.0 + trigger + 0.1
    with patch("custom_components.localshift.state.machine.dt_util.now") as now:
        now.return_value = datetime(2026, 10, 12, 16, 30, tzinfo=AEDT)
        await machine.evaluate_state_machine(data, MagicMock())
    assert powerwall.step_requests == []

    # Just inside it: one step, to SOC - 5.
    inside = 55.0 + trigger - 0.1
    powerwall.soc = data.soc = inside
    with patch("custom_components.localshift.state.machine.dt_util.now") as now:
        now.return_value = datetime(2026, 10, 12, 16, 31, tzinfo=AEDT)
        await machine.evaluate_state_machine(data, MagicMock())
    assert powerwall.step_requests == [(inside, pytest.approx(inside - 5))]


@pytest.mark.asyncio
async def test_spike_flag_toggling_never_raises_or_repeats_a_reserve(
    machine, powerwall, data
):
    """The flag flipped five times between 16:00 and 16:35 on the day. Flipping
    it every minute over the same SOC trace still gives strictly falling writes.
    """
    engine = MagicMock()
    start = datetime(2026, 10, 12, 15, 30, 15, tzinfo=AEDT)
    by_minute = dict(SOC_SAMPLES_2026_10_12)
    soc = SOC_SAMPLES_2026_10_12[0][1]
    with patch("custom_components.localshift.state.machine.dt_util.now") as now:
        for minute in range(31):
            soc = by_minute.get(minute, soc)
            powerwall.soc = data.soc = soc
            data.backup_reserve = powerwall.hardware_reserve
            data.price_spike = minute % 2 == 0
            now.return_value = start + timedelta(minutes=minute)
            await machine.evaluate_state_machine(data, engine)

    assert machine._commanded_mode == BatteryMode.PROACTIVE_EXPORT
    assert len(powerwall.requested) >= 3
    assert all(
        later < earlier
        for earlier, later in zip(
            powerwall.requested, powerwall.requested[1:], strict=False
        )
    )
    assert powerwall.transitions == 1


@pytest.mark.asyncio
async def test_floor_above_tesla_ceiling_does_not_rewrite_every_tick(
    machine, powerwall, data
):
    """A planner export floor in 81-99 cannot be written; the reserve stays at
    80 rather than being re-sent each minute.
    """
    data.planner_export_floor_pct = 88.0

    await _replay(machine, powerwall, data, ((0, 97.0),), 10)

    assert powerwall.bounced == []
    assert powerwall.requested == [pytest.approx(80.0)]


# --- Ordinary-price controls: the spike flag is off, so step timing is as before.


@pytest.mark.asyncio
async def test_control_same_trace_without_spike_keeps_one_point_trigger(
    machine, powerwall, data
):
    """The 2026-10-12 SOC trace with the spike flag off keeps the 1-point
    trigger, now measured against the reserve Tesla actually holds (80): 82.0
    is 2.0 above it and waits; 80.5 steps. Production stepped at 82.0 only
    because it was tracking 81.9, a value the Powerwall never held. The 79.0
    sample that steps during a spike does not step here.
    """
    data.price_spike = False
    data.feed_in_price = 0.30

    await _replay(machine, powerwall, data, SOC_SAMPLES_2026_10_12, 30)

    assert powerwall.step_requests == [
        (80.5206967596928, pytest.approx(80.5206967596928 - 5))
    ]
    assert powerwall.bounced == []
    assert powerwall.transitions == 1


@pytest.mark.asyncio
async def test_control_ordinary_evening_export_does_not_step(machine, powerwall, data):
    """2026-10-12 18:42-18:48, feed-in 26-31c, spike flag off: entry at SOC 37.4
    (reserve 32.4), next sample 35.2 — 2.75 above the reserve. Production wrote
    no step, and still must not: inside 3.0 but outside the ordinary 1.0.
    """
    data.price_spike = False
    data.feed_in_price = 0.31

    await _replay(
        machine,
        powerwall,
        data,
        ((0, 37.4414684397827), (5, 35.1938565274396)),
        8,
    )

    assert powerwall.requested == [pytest.approx(37.4414684397827 - 5)]
    assert powerwall.step_requests == []


# --- The recorded spike flag, replayed alongside the recorded SOC.
#
# Home Assistant history for 2026-10-12, state changes as (HH:MM:SS, value).
# The state machine evaluated at about :33 past each minute that afternoon
# (reserve writes logged 15:45:33 and 16:00:33).

SOC_HISTORY_2026_10_12 = (
    ("15:30:15", 97.0031841168758),
    ("15:35:16", 92.133358306799),
    ("15:40:17", 86.8889305113317),
    ("15:45:19", 82.0191047012549),
    ("15:50:21", 80.5206967596928),
    ("15:55:20", 79.0222888181307),
    ("16:00:20", 77.5238808765687),
    ("16:05:21", 76.0254729350065),
    ("16:10:17", 73.0286570518824),
)

# StateReader._read_bool: anything but "on" (so "unavailable" too) is False.
SPIKE_HISTORY_2026_10_12 = {
    "binary_sensor.amber_express_100h_price_spike": (
        ("15:25:18", "on"),
        ("16:00:20", "off"),
        ("16:05:20", "on"),
        ("16:10:22", "off"),
    ),
    "binary_sensor.100h_price_spike": (
        ("15:26:16", "on"),
        ("15:35:30", "off"),
        ("15:36:30", "on"),
        ("15:45:32", "unavailable"),
        ("15:46:32", "on"),
        ("16:00:34", "off"),
        ("16:05:34", "on"),
        ("16:10:34", "off"),
    ),
}


def _at(clock: str) -> datetime:
    hour, minute, second = (int(part) for part in clock.split(":"))
    return datetime(2026, 10, 12, hour, minute, second, tzinfo=AEDT)


def _latest(history, when):
    """The last recorded value at or before ``when``."""
    value = None
    for clock, state in history:
        if _at(clock) <= when:
            value = state
    return value


async def _replay_recorded(machine, powerwall, data, spike_entity):
    """15:30:33-16:10:33, one evaluation a minute, SOC and flag as recorded."""
    spike_history = SPIKE_HISTORY_2026_10_12[spike_entity]
    when = _at("15:30:33")
    steps_at: list[str] = []
    with patch("custom_components.localshift.state.machine.dt_util.now") as now:
        while when <= _at("16:10:33"):
            soc = _latest(SOC_HISTORY_2026_10_12, when)
            powerwall.soc = data.soc = soc
            data.backup_reserve = powerwall.hardware_reserve
            data.price_spike = _latest(spike_history, when) == "on"
            now.return_value = when
            steps_before = len(powerwall.step_requests)
            await machine.evaluate_state_machine(data, MagicMock())
            if len(powerwall.step_requests) > steps_before:
                steps_at.append(when.strftime("%H:%M"))
            when += timedelta(minutes=1)
    return steps_at


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("spike_entity", "expected_steps"),
    [
        # Flag on at every sample but 16:00 and 16:10, when it had just gone
        # off: those two samples wait, as they would at ordinary prices.
        (
            "binary_sensor.amber_express_100h_price_spike",
            [
                ("15:45", 82.0191047012549),
                ("15:55", 79.0222888181307),
                ("16:05", 76.0254729350065),
            ],
        ),
        # Unavailable at 15:45:33 and not yet back on at 16:05:33, so those two
        # steps land a minute late. It was still on at 16:10:33 (off at :34),
        # which gives a fourth step the other sensor does not.
        (
            "binary_sensor.100h_price_spike",
            [
                ("15:46", 82.0191047012549),
                ("15:55", 79.0222888181307),
                ("16:06", 76.0254729350065),
                ("16:10", 73.0286570518824),
            ],
        ),
    ],
)
async def test_recorded_flag_replay_step_times(
    machine, powerwall, data, spike_entity, expected_steps
):
    """What the spike trigger would have written on the day, with the flag as
    each sensor actually reported it. Which of the two is configured live was
    not readable, so both are pinned. The 15:55 step — the one the issue is
    about — holds for both. Margins against the flag are a second or two, so
    the minute a step lands on is not something to rely on.
    """
    steps_at = await _replay_recorded(machine, powerwall, data, spike_entity)

    assert steps_at == [clock for clock, _ in expected_steps]
    assert powerwall.step_requests == [
        (soc, pytest.approx(soc - PROACTIVE_EXPORT_SOC_BUFFER_PERCENT))
        for _, soc in expected_steps
    ]
    assert powerwall.bounced == []
    assert powerwall.transitions == 1


# --- A moving export floor (#1116) must not turn the spike trigger into a flap.
#
# The load-sized floor is re-derived on every plan, and it does move up and
# back: 2026-10-12 17:43:34-17:45:15 the hardware reserve read 51 -> 53 -> 49.
# A floor above the tracked reserve raises it (existing behaviour). The spike
# trigger must not then lower it again unless SOC has actually drained.


async def _replay_floor(machine, powerwall, data, steps):
    """One evaluation a minute over ``(soc, planner_export_floor_pct)`` steps.

    The first step is repeated until the mode debounce has run and export is
    entered, so every later step is a re-step decision.
    """
    start = datetime(2026, 10, 12, 17, 40, 33, tzinfo=AEDT)
    minute = 0

    async def evaluate(soc, floor):
        nonlocal minute
        powerwall.soc = data.soc = soc
        data.planner_export_floor_pct = floor
        data.backup_reserve = powerwall.hardware_reserve
        now.return_value = start + timedelta(minutes=minute)
        minute += 1
        await machine.evaluate_state_machine(data, MagicMock())

    with patch("custom_components.localshift.state.machine.dt_util.now") as now:
        for _ in range(10):
            await evaluate(*steps[0])
            if powerwall.transitions:
                break
        assert powerwall.transitions == 1, "export was never entered"
        for soc, floor in steps[1:]:
            await evaluate(soc, floor)


@pytest.mark.asyncio
async def test_moving_floor_at_steady_soc_writes_once_during_a_spike(
    machine, powerwall, data
):
    """SOC steady at 55.5, floor alternating 53 / 50 every minute, spike on.

    Without the guard the reserve was rewritten every minute, alternating
    direction (53.0, 50.5, 53.0, 50.5, ...): the floor raised it and the 3.0
    trigger (SOC within 3 of 53) lowered it again, which the 1.0 trigger never
    did. SOC has not moved, so there is nothing for a step to follow.
    """
    data.price_spike = True
    await _replay_floor(machine, powerwall, data, [(55.5, 53.0), (55.5, 50.0)] * 5)

    assert machine._commanded_mode == BatteryMode.PROACTIVE_EXPORT
    assert powerwall.requested == [pytest.approx(53.0)]
    assert powerwall.transitions == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("soc", [53.5, 54.5, 55.5, 56.5, 58.5])
async def test_spike_flag_adds_no_write_while_soc_is_steady(soc):
    """With SOC steady and the floor alternating, the spike flag changes
    nothing: the writes are exactly those the ordinary trigger makes. Whatever
    the floor does, the wider trigger only acts on SOC that has drained.
    """
    steps = [(soc, 53.0), (soc, 50.0)] * 5
    writes = {}
    for spike in (False, True):
        powerwall = FakePowerwall()
        sm = _build_machine(powerwall)
        d = _build_data()
        d.price_spike = spike
        await _replay_floor(sm, powerwall, d, steps)
        writes[spike] = list(powerwall.requested)

    assert writes[True] == writes[False]


@pytest.mark.asyncio
async def test_spike_step_still_follows_soc_down_from_a_floor_raised_reserve(
    machine, powerwall, data
):
    """The guard is a spacing, not a lock-out. Entry at SOC 57 with the floor
    at 53 writes 53. The floor then falls to 50. SOC 55.5 (1.5 lower, inside
    the 3.0 trigger) waits; SOC 54.9 (2.1 lower, still outside the ordinary
    1.0) steps, to the floor.
    """
    data.price_spike = True
    await _replay_floor(
        machine,
        powerwall,
        data,
        [(57.0, 53.0), (55.5, 50.0), (55.5, 50.0), (54.9, 50.0), (54.9, 50.0)],
    )

    assert powerwall.requested == [pytest.approx(53.0), pytest.approx(50.0)]
    assert powerwall.step_requests == [(54.9, pytest.approx(50.0))]
