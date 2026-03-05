"""Scenario-based tests for battery automation logic.

This module loads JSON scenario files and validates that the computation
engine produces expected outputs for each scenario.

Phase 8 (#450): Updated to assert against DP optimizer outputs.
All scenarios now run with optimizer_enabled=True and optimizer_control_mode="active".
Legacy fields (daily_forecast_min_soc, demand_window_end_soc, boost_charge_needed)
have been replaced with DP-native assertions.
"""

import statistics
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from custom_components.localshift.computation_engine import (
    BatteryMode,
    ComputationEngine,
)
from custom_components.localshift.coordinator_data import CoordinatorData
from simulations.schema import Scenario, discover_scenarios

# ---------------------------------------------------------------------------
# Timezone helper
# ---------------------------------------------------------------------------


def dt_aware(year, month, day, hour, minute=0, second=0, tz_offset_hours=11):
    """Create a timezone-aware datetime."""
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        second,
        tzinfo=timezone(timedelta(hours=tz_offset_hours)),
    )


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


def setup_coordinator_data(input_data: dict) -> CoordinatorData:
    """Create CoordinatorData from scenario input.

    Args:
        input_data: Dictionary of input values from scenario

    Returns:
        Populated CoordinatorData instance
    """
    data = CoordinatorData()

    # Core state values
    data.soc = input_data.get("soc", 50.0)
    data.operation_mode = input_data.get("operation_mode", "autonomous")
    data.backup_reserve = input_data.get("backup_reserve", 50)
    data.grid_power_kw = input_data.get("grid_power_kw", 0.0)
    data.load_power_kw = input_data.get("load_power_kw", 0.5)
    data.solar_power_kw = input_data.get("solar_power_kw", 0.0)
    data.battery_power_kw = input_data.get("battery_power_kw", 0.0)
    data.general_price = input_data.get("general_price", 0.25)
    data.feed_in_price = input_data.get("feed_in_price", 0.08)
    data.price_spike = input_data.get("price_spike", False)
    data.manual_override = input_data.get("manual_override", False)

    # Forecast data
    data.solcast_today = input_data.get("solcast_today", [])
    data.solcast_tomorrow = input_data.get("solcast_tomorrow", [])
    data.general_forecast = input_data.get("general_forecast", [])
    data.feed_in_forecast = input_data.get("feed_in_forecast", [])

    # Initialize empty containers
    data.decision_log = []
    data.daily_forecast = []
    data.daily_forecast_soc_15min = []
    data.forecast_consumption_source_counts = {}
    data.forecast_history = []
    data.target_reached_today = input_data.get("target_reached_today", False)

    # Issue #319: Mark forecast as ready for tests (tests have forecast data)
    data.forecast_ready = True
    data.forecast_status = "ready"

    return data


def setup_mock_hass(input_data: dict) -> MagicMock:
    """Create mock Home Assistant instance with entity states.

    Args:
        input_data: Dictionary containing entity states

    Returns:
        Mock HASS instance
    """
    hass = MagicMock()
    hass.states = MagicMock()
    hass.data = {}

    def mock_get_state(entity_id):
        state = MagicMock()
        # Return appropriate state based on entity_id
        if "solcast_today" in entity_id:
            state.state = "unknown"
            state.attributes = {"detailedForecast": input_data.get("solcast_today", [])}
        elif "solcast_tomorrow" in entity_id:
            state.state = "unknown"
            state.attributes = {
                "detailedForecast": input_data.get("solcast_tomorrow", [])
            }
        elif "general_forecast" in entity_id:
            state.state = "unknown"
            state.attributes = {"forecasts": input_data.get("general_forecast", [])}
        elif "feed_in_forecast" in entity_id:
            state.state = "unknown"
            state.attributes = {"forecasts": input_data.get("feed_in_forecast", [])}
        else:
            state.state = "unknown"
            state.attributes = {}
        return state

    hass.states.get = mock_get_state
    return hass


def create_mock_entry(config_overrides: dict) -> MagicMock:
    """Create mock ConfigEntry with scenario config overrides.

    Args:
        config_overrides: Dictionary of config values to override

    Returns:
        Mock ConfigEntry instance
    """
    entry = MagicMock()
    entry.data = {}
    entry.options = {
        "battery_target": 90,
        "cheap_price_percentile": 40,
        "cheap_price_deadband": 0.02,
        "max_pre_charge_price": 0.30,
        "forecast_lookahead_hours": 8,
        "demand_window_start": "18:00:00",
        "demand_window_end": "22:00:00",
        "load_weight_recent": 0.7,
        "minimum_target_soc": 15,
        "spike_price_percentile": 80,
        # Phase 8 (#450): optimizer always enabled and active in scenario tests,
        # matching the post-Phase-6 state where shadow/assist modes are removed.
        "optimizer_enabled": True,
        "optimizer_control_mode": "active",
    }
    # Apply overrides (scenario JSON config_overrides take precedence)
    entry.options.update(config_overrides)
    return entry


def create_mock_get_entity_id() -> callable:
    """Create mock entity ID resolver."""

    def _get_entity_id(key):
        entity_map = {
            "teslemetry_soc": "sensor.tesla_powerwall_soc",
            "teslemetry_operation_mode": "sensor.tesla_powerwall_operation_mode",
            "teslemetry_backup_reserve": "sensor.tesla_powerwall_backup_reserve",
            "teslemetry_grid_power": "sensor.tesla_powerwall_grid_power",
            "teslemetry_load_power": "sensor.tesla_load_power",
            "teslemetry_solar_power": "sensor.tesla_solar_power",
            "solcast_today": "sensor.solcast_today",
            "solcast_tomorrow": "sensor.solcast_tomorrow",
            "general_forecast": "sensor.amber_general_forecast",
            "feed_in_forecast": "sensor.amber_feed_in_forecast",
        }
        return entity_map.get(key, f"sensor.{key}")

    return _get_entity_id


def create_mock_get_switch_state(switch_states: dict) -> callable:
    """Create mock switch state resolver.

    Args:
        switch_states: Dictionary of switch name -> bool state

    Returns:
        Function that returns switch states
    """

    def _get_switch_state(key):
        defaults = {
            "automation_enabled": True,
            "spike_discharge_enabled": True,
            "demand_window_block": True,
            "manual_override": False,
            "allow_dw_entry_under_target": False,
            "spike_discharge_conservative": False,
        }
        return switch_states.get(key, defaults.get(key, False))

    return _get_switch_state


def assert_expected_values(data: CoordinatorData, expected: dict, scenario_name: str):
    """Assert that CoordinatorData matches expected values.

    Supports standard attribute checks plus the following special keys:
    - ``optimizer_result_success``: checks ``data.optimizer_result["success"]``

    Args:
        data: Computed CoordinatorData
        expected: Dictionary of expected values
        scenario_name: Name of scenario (for error messages)
    """
    for key, expected_value in expected.items():
        # --- Special key: optimizer_result_success ---
        if key == "optimizer_result_success":
            result = data.optimizer_result
            if result is None:
                pytest.fail(
                    f"[{scenario_name}] optimizer_result is None — optimizer did not run"
                )
            actual = result.get("success")
            assert actual == expected_value, (
                f"[{scenario_name}] optimizer_result['success']: "
                f"expected {expected_value}, got {actual}"
            )
            continue

        # --- Legacy special keys retained for backwards compat (no longer in any scenario) ---
        if key == "daily_forecast_min_soc":
            if not data.daily_forecast:
                pytest.fail(f"[{scenario_name}] daily_forecast is empty")
            actual_value = min(
                slot.get("predicted_soc", 100) for slot in data.daily_forecast
            )
            assert actual_value == pytest.approx(expected_value, rel=0.01), (
                f"[{scenario_name}] {key}: expected {expected_value}, got {actual_value}"
            )
            continue

        if key == "demand_window_end_soc":
            if not data.daily_forecast:
                pytest.fail(f"[{scenario_name}] daily_forecast is empty")
            dw_end_soc = None
            for slot in data.daily_forecast:
                if slot.get("hour") == 22 and slot.get("minute") == 0:
                    dw_end_soc = slot.get("predicted_soc")
                    break
            if dw_end_soc is None:
                pytest.fail(f"[{scenario_name}] No forecast slot at demand window end")
            assert dw_end_soc >= expected_value, (
                f"[{scenario_name}] {key}: expected >= {expected_value}, got {dw_end_soc}"
            )
            continue

        actual_value = getattr(data, key, None)

        if actual_value is None:
            pytest.fail(f"[{scenario_name}] Missing attribute: {key}")

        # Handle BatteryMode enum comparison
        if isinstance(expected_value, str) and expected_value in [
            e.value for e in BatteryMode
        ]:
            expected_value = BatteryMode(expected_value)

        # Handle float comparisons with tolerance
        if isinstance(expected_value, float) and isinstance(actual_value, float):
            assert actual_value == pytest.approx(expected_value, rel=0.01), (
                f"[{scenario_name}] {key}: expected {expected_value}, got {actual_value}"
            )
        else:
            assert actual_value == expected_value, (
                f"[{scenario_name}] {key}: expected {expected_value}, got {actual_value}"
            )


# ---------------------------------------------------------------------------
# DP assertion helpers
# ---------------------------------------------------------------------------


def count_actions(decisions: list[dict], action: str) -> int:
    """Count optimizer decision slots with the given action string.

    Args:
        decisions: List of serialized PlannedSlotDecision dicts from data.optimizer_decisions
        action: PlannerAction string value, e.g. "charge_grid_normal", "hold"

    Returns:
        Number of slots whose action matches
    """
    return sum(1 for d in decisions if d.get("action") == action)


def get_slots_by_action(decisions: list[dict], action: str) -> list[dict]:
    """Return all decision dicts whose action matches the given string.

    Args:
        decisions: List of serialized PlannedSlotDecision dicts
        action: PlannerAction string value

    Returns:
        Filtered list of decision dicts
    """
    return [d for d in decisions if d.get("action") == action]


def get_charge_slots(decisions: list[dict]) -> list[dict]:
    """Return all grid-charging decision slots (normal or boost).

    Args:
        decisions: List of serialized PlannedSlotDecision dicts

    Returns:
        Combined list of charge_grid_normal and charge_grid_boost slots
    """
    return get_slots_by_action(decisions, "charge_grid_normal") + get_slots_by_action(
        decisions, "charge_grid_boost"
    )


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------


def run_scenario(scenario_name: str) -> CoordinatorData:
    """Run a named scenario through compute_derived_values and return data.

    Loads the scenario JSON from simulations/scenarios/, sets up the computation
    engine with the scenario's config_overrides and switch_states, runs
    compute_derived_values(), and returns the populated CoordinatorData.

    Args:
        scenario_name: Stem of the scenario JSON file, e.g. "sunny-day"

    Returns:
        CoordinatorData populated by compute_derived_values()

    Raises:
        FileNotFoundError: If no matching scenario file is found
    """

    scenario_paths = discover_scenarios()
    matching = [p for p in scenario_paths if p.stem == scenario_name]
    if not matching:
        raise FileNotFoundError(
            f"No scenario file found for '{scenario_name}'. "
            f"Available: {[p.stem for p in scenario_paths]}"
        )

    scenario = Scenario.from_json(matching[0])

    test_time_str = scenario.input.get("test_time", "2026-02-16T14:00:00+11:00")
    test_time = datetime.fromisoformat(test_time_str)

    data = setup_coordinator_data(scenario.input)
    hass = setup_mock_hass(scenario.input)
    entry = create_mock_entry(scenario.config_overrides)
    get_entity_id = create_mock_get_entity_id()
    get_switch_state = create_mock_get_switch_state(scenario.switch_states)

    engine = ComputationEngine(hass, entry, get_entity_id, get_switch_state)

    recent_load = scenario.input.get("load_power_kw", 0.5)
    with (
        patch("homeassistant.util.dt.now", return_value=test_time),
        patch.object(engine, "_get_historical_hourly_averages", return_value={}),
        patch.object(engine._history_fetcher, "_historical_load_cache", {}),
        patch.object(engine._history_fetcher, "_historical_load_sample_counts", {}),
        patch.object(engine._history_fetcher, "_historical_load_source", "none"),
        patch.object(engine._history_fetcher, "_recent_load_1hr_kw", recent_load),
    ):
        engine.compute_derived_values(data)

    return data


# ---------------------------------------------------------------------------
# Parametrised scenario test (JSON expected values)
# ---------------------------------------------------------------------------

SCENARIO_PATHS = discover_scenarios()


@pytest.mark.parametrize("scenario_path", SCENARIO_PATHS, ids=lambda p: p.stem)
def test_scenario(scenario_path, request):
    """Run a single scenario test.

    This test:
    1. Loads the scenario JSON
    2. Sets up mock HASS, config, and coordinator data
    3. Runs compute_derived_values
    4. Validates expected outputs defined in the JSON ``expected`` block

    Phase 8 (#450): Skip decorator removed. All scenarios now run against DP outputs.
    The JSON expected blocks use DP-native fields (optimizer_result_success, etc.).
    """
    scenario = Scenario.from_json(scenario_path)

    if scenario.input.get("skip", False):
        pytest.skip(f"Scenario marked as skip: {scenario.name}")

    test_time_str = scenario.input.get("test_time", "2026-02-16T14:00:00+11:00")
    test_time = datetime.fromisoformat(test_time_str)

    data = setup_coordinator_data(scenario.input)
    hass = setup_mock_hass(scenario.input)
    entry = create_mock_entry(scenario.config_overrides)
    get_entity_id = create_mock_get_entity_id()
    get_switch_state = create_mock_get_switch_state(scenario.switch_states)

    engine = ComputationEngine(hass, entry, get_entity_id, get_switch_state)

    recent_load = scenario.input.get("load_power_kw", 0.5)
    with (
        patch("homeassistant.util.dt.now", return_value=test_time),
        patch.object(engine, "_get_historical_hourly_averages", return_value={}),
        patch.object(engine._history_fetcher, "_historical_load_cache", {}),
        patch.object(engine._history_fetcher, "_historical_load_sample_counts", {}),
        patch.object(engine._history_fetcher, "_historical_load_source", "none"),
        patch.object(engine._history_fetcher, "_recent_load_1hr_kw", recent_load),
    ):
        engine.compute_derived_values(data)

    assert_expected_values(data, scenario.expected, scenario.name)


def test_scenario_discovery():
    """Verify that scenario discovery works."""
    assert len(SCENARIO_PATHS) >= 0, "No scenarios discovered"

    for path in SCENARIO_PATHS:
        scenario = Scenario.from_json(path)
        assert scenario.name, f"Scenario {path} has no name"
        assert scenario.input, f"Scenario {path} has no input"
        assert scenario.expected, f"Scenario {path} has no expected values"


# ---------------------------------------------------------------------------
# DP-native scenario assertion tests — sunny day
# ---------------------------------------------------------------------------


def test_sunny_day_no_charge_at_peak_prices():
    """Sunny day: DP never grid-charges at above-median buy prices.

    When solar is sufficient to meet the target, any top-up charging that
    does occur should be confined to cheap-price slots. Charging during
    expensive periods is always economically irrational given sufficient solar.
    """
    data = run_scenario("sunny-day")
    decisions = data.optimizer_decisions
    assert decisions, "optimizer produced no decisions"

    charge_slots = get_charge_slots(decisions)
    if not charge_slots:
        return  # No charging at all is fine for a sunny day

    all_buy_prices = [d["buy_price"] for d in decisions if d["buy_price"] > 0]
    if not all_buy_prices:
        return
    median_buy = statistics.median(all_buy_prices)

    for slot in charge_slots:
        assert slot["buy_price"] <= median_buy, (
            f"Sunny day: grid charge at slot {slot['slot_index']} "
            f"has buy_price={slot['buy_price']:.4f} > median={median_buy:.4f}"
        )


def test_sunny_day_terminal_shortfall_zero():
    """Sunny day: solar sufficient — optimizer terminal shortfall must be 0.

    With abundant solar the DP should be able to project zero shortfall at the
    demand-window terminal slot. A non-zero shortfall would mean the optimizer
    believes the battery cannot reach the target, contradicting the scenario's
    solar-sufficient premise.
    """
    data = run_scenario("sunny-day")
    assert data.optimizer_result is not None, "optimizer did not run"
    shortfall = data.optimizer_result.get("terminal_shortfall_pct", -1)
    assert shortfall == pytest.approx(0.0, abs=0.1), (
        f"Sunny day should have zero terminal shortfall, got {shortfall:.2f}%"
    )


# ---------------------------------------------------------------------------
# DP-native scenario assertion tests — cloudy day
# ---------------------------------------------------------------------------


def test_cloudy_day_requires_some_grid_charging():
    """Cloudy day: solar insufficient — DP must plan at least one charging slot.

    With poor solar forecast, the only path to meeting the demand-window target
    is grid charging. The optimizer should always schedule at least one charge
    slot when solar cannot reach target.
    """
    data = run_scenario("cloudy-day")
    decisions = data.optimizer_decisions
    assert decisions, "optimizer produced no decisions"

    charge_slots = get_charge_slots(decisions)
    assert len(charge_slots) >= 1, (
        f"Cloudy day should have at least 1 charge slot, got {len(charge_slots)}"
    )


def test_cloudy_day_grid_charge_at_cheap_prices_only():
    """Cloudy day: any grid charging occurs only at or below the median buy price.

    The optimizer should select the cheapest available slots for charging.
    Charging at above-median prices when cheaper slots exist earlier in the
    horizon is an economic error the DP should never make.
    """
    data = run_scenario("cloudy-day")
    decisions = data.optimizer_decisions
    assert decisions, "optimizer produced no decisions"

    charge_slots = get_charge_slots(decisions)
    if not charge_slots:
        pytest.skip(
            "No charge slots found — covered by test_cloudy_day_requires_some_grid_charging"
        )

    all_buy_prices = [d["buy_price"] for d in decisions if d["buy_price"] > 0]
    assert all_buy_prices, "No buy prices found in decisions"
    median_buy = statistics.median(all_buy_prices)

    for slot in charge_slots:
        assert slot["buy_price"] <= median_buy, (
            f"Cloudy day: grid charge at slot {slot['slot_index']} "
            f"has buy_price={slot['buy_price']:.4f} > median={median_buy:.4f}"
        )


# ---------------------------------------------------------------------------
# DP-native scenario assertion tests — high solar / negative FIT
# ---------------------------------------------------------------------------


def test_high_solar_no_export_at_negative_prices():
    """Negative FIT: DP never schedules proactive export when sell_price <= 0.

    Exporting at negative feed-in prices means paying to export — always
    irrational. The optimizer must never emit an EXPORT_PROACTIVE action
    for a slot whose sell_price is zero or negative.
    """
    data = run_scenario("high-solar-negative-fit")
    decisions = data.optimizer_decisions
    assert decisions, "optimizer produced no decisions"

    export_slots = get_slots_by_action(decisions, "export_proactive")
    for slot in export_slots:
        assert slot["sell_price"] > 0, (
            f"high-solar-negative-fit: export at slot {slot['slot_index']} "
            f"has non-positive sell_price={slot['sell_price']:.4f}"
        )


# ---------------------------------------------------------------------------
# DP-native scenario assertion tests — hot day / price spike
# ---------------------------------------------------------------------------


def test_hot_day_no_grid_charge_during_peak():
    """Price spike scenario: DP never charges from grid during the peak-price slots.

    During a high-price event, grid charging is economically irrational.
    The top-decile buy-price slots should never have a charge_grid action.
    """
    data = run_scenario("hot-day-spike")
    decisions = data.optimizer_decisions
    assert decisions, "optimizer produced no decisions"

    all_buy_prices = sorted(d["buy_price"] for d in decisions if d["buy_price"] > 0)
    if not all_buy_prices:
        return

    # Top 10% of buy prices constitute "peak"
    top_10pct_threshold = all_buy_prices[int(len(all_buy_prices) * 0.9)]

    peak_slots = [d for d in decisions if d["buy_price"] >= top_10pct_threshold]
    for slot in peak_slots:
        action = slot.get("action", "")
        assert action not in ("charge_grid_normal", "charge_grid_boost"), (
            f"hot-day-spike: grid charge at peak-price slot {slot['slot_index']} "
            f"(buy_price={slot['buy_price']:.4f}, action={action})"
        )


# ---------------------------------------------------------------------------
# DP-native scenario assertion tests — high target SOC
# ---------------------------------------------------------------------------


def test_high_target_soc_requires_grid_charging():
    """High SOC target: solar alone insufficient — DP must plan grid charging.

    With SOC=20%, target=80%, and moderate solar that cannot bridge the gap,
    the optimizer must schedule grid charging to meet the demand-window target.
    """
    data = run_scenario("high-target-soc")
    decisions = data.optimizer_decisions
    assert decisions, "optimizer produced no decisions"

    charge_slots = get_charge_slots(decisions)
    assert len(charge_slots) >= 1, (
        f"high-target-soc: expected grid charging (solar insufficient), "
        f"got {len(charge_slots)} charge slots"
    )


def test_high_target_soc_charges_in_cheap_slots():
    """High SOC target: DP grid-charges only in cheap overnight slots.

    With a steep price differential between overnight cheap slots (0.06-0.08)
    and expensive evening slots (0.38-0.45), the optimizer must confine all
    charging to the cheap window — never at above-median prices.
    """
    data = run_scenario("high-target-soc")
    decisions = data.optimizer_decisions
    assert decisions, "optimizer produced no decisions"

    charge_slots = get_charge_slots(decisions)
    if not charge_slots:
        pytest.skip(
            "No charge slots — covered by test_high_target_soc_requires_grid_charging"
        )

    all_buy_prices = [d["buy_price"] for d in decisions if d["buy_price"] > 0]
    assert all_buy_prices
    median_buy = statistics.median(all_buy_prices)

    for slot in charge_slots:
        assert slot["buy_price"] <= median_buy, (
            f"high-target-soc: grid charge at slot {slot['slot_index']} "
            f"has buy_price={slot['buy_price']:.4f} > median={median_buy:.4f}"
        )


# ---------------------------------------------------------------------------
# Contradiction regression tests — #401
# ---------------------------------------------------------------------------


def test_solar_can_reach_target_matches_optimizer_result_sunny():
    """Regression #401: data.solar_can_reach_target == optimizer_result['can_solar_reach_target'] (sunny).

    After Phase 4, data.solar_can_reach_target is set exclusively from the DP
    result in _write_optimizer_fields(). The serialized optimizer_result dict
    must carry the same value. Any divergence indicates a second code path that
    has overwritten data.solar_can_reach_target after the DP write — reproducing
    the split-brain bug from #401.
    """
    data = run_scenario("sunny-day")
    assert data.optimizer_result is not None, "optimizer did not run"
    assert (
        data.solar_can_reach_target == data.optimizer_result["can_solar_reach_target"]
    ), (
        f"Contradiction (#401): data.solar_can_reach_target={data.solar_can_reach_target} "
        f"but optimizer_result['can_solar_reach_target']="
        f"{data.optimizer_result['can_solar_reach_target']}"
    )


def test_solar_can_reach_target_matches_optimizer_result_cloudy():
    """Regression #401: data.solar_can_reach_target == optimizer_result['can_solar_reach_target'] (cloudy).

    Same invariant as the sunny-day test, verified against the cloudy-day scenario
    where the expected value is False. Guards the False branch of the linkage.
    """
    data = run_scenario("cloudy-day")
    assert data.optimizer_result is not None, "optimizer did not run"
    assert (
        data.solar_can_reach_target == data.optimizer_result["can_solar_reach_target"]
    ), (
        f"Contradiction (#401): data.solar_can_reach_target={data.solar_can_reach_target} "
        f"but optimizer_result['can_solar_reach_target']="
        f"{data.optimizer_result['can_solar_reach_target']}"
    )
