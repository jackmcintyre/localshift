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

The retailer's price-spike flag (``data.price_spike``) was on from 15:25 to
16:00. The wider trigger applies only while it is on; the ordinary-price
controls at the bottom pin the old timing with it off.
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


@pytest.fixture
def machine(powerwall):
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
def data():
    d = CoordinatorData()
    d.operation_mode = "autonomous"
    d.backup_reserve = 10
    d.grid_power_kw = -7.0
    d.battery_power_kw = 7.5
    d.load_power_kw = 0.5
    d.solar_power_kw = 2.0
    d.general_price = 10.6
    d.feed_in_price = 10.48
    d.price_spike = True  # binary_sensor.*_price_spike, on 15:25-16:00
    d.manual_override = False
    d.decision_log = []
    d.daily_forecast = []
    d.daily_forecast_soc_15min = []
    d.automation_ready = True
    d.active_mode = BatteryMode.PROACTIVE_EXPORT
    return d


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
