"""Unit tests for scripts/deploy_check.py.

Each invariant is exercised both ways: the healthy pair of dumps passes, and
breaking exactly one thing fails exactly that check.
"""

import copy
import json
import os
from datetime import datetime

import pytest

from scripts import deploy_check as dc

NOW = datetime(2026, 10, 14, 10, 20, 30)


def _dump() -> dict[str, dict]:
    def ent(state, **attrs):
        return {"state": state, "attributes": attrs}

    return {
        dc.HEALTH: ent("68/68"),
        dc.SUMMARY: ent(
            "success",
            config_options={"battery_target": 95.0, "minimum_target_soc": 10.0},
            initial_soc_pct=17.2,
            dw_entry_soc_pct=98.9,
            cycle_id="abc",
        ),
        dc.PLAN: ent("computed", total_slots=59, decisions=[]),
        dc.AUTOMATION: ent("on"),
        dc.DRY_RUN: ent("off"),
        dc.MODE: ent("optimizer"),
        dc.POWERWALL_SOC: ent("17.0"),
        "sensor.localshift_net_cost": ent("1.2"),
    }


def _log() -> list[str]:
    ls = "[custom_components.localshift.engine.optimizer_facade]"
    return [
        "2026-10-14 10:08:02.796 WARNING (SyncWorker_0) [homeassistant.loader] custom integration hacs",
        f"2026-10-14 10:08:16.129 WARNING (MainThread) {ls} Cannot read Tesla tariff from calendar",
        f"2026-10-14 10:08:16.202 WARNING (MainThread) {ls} DP optimizer: invalid SOC non_positive, skipping",
        f"2026-10-14 10:08:16.349 WARNING (MainThread) {ls} DP optimizer: invalid SOC non_positive, skipping",
        f"2026-10-14 10:18:15.000 INFO (MainThread) {ls} Pre-charge decision: initial_soc=17.2%",
        f"2026-10-14 10:19:15.000 INFO (MainThread) {ls} Pre-charge decision: initial_soc=17.2%",
        f"2026-10-14 10:20:15.000 INFO (MainThread) {ls} Pre-charge decision: initial_soc=17.2%",
    ]


def _failed(checks) -> list[str]:
    return [c.name for c in checks if not c.ok]


def _run(before=None, after=None, log=None, **kwargs):
    return dc.run_checks(
        before or _dump(), after or _dump(), log or _log(), NOW, **kwargs
    )


def test_healthy_pair_passes():
    assert _failed(_run()) == []


def test_lost_entity_fails_unless_allowed():
    after = _dump()
    del after["sensor.localshift_net_cost"]
    assert _failed(_run(after=after)) == ["entities present"]
    assert (
        _failed(
            _run(after=after, allow_removed_entities={"sensor.localshift_net_cost"})
        )
        == []
    )


def test_newly_unavailable_entity_fails():
    after = _dump()
    after["sensor.localshift_net_cost"]["state"] = "unavailable"
    assert _failed(_run(after=after)) == ["none newly unavailable"]


def test_already_unavailable_entity_is_not_new():
    before, after = _dump(), _dump()
    before["sensor.localshift_net_cost"]["state"] = "unavailable"
    after["sensor.localshift_net_cost"]["state"] = "unavailable"
    assert _failed(_run(before=before, after=after)) == []


def test_health_drop_fails():
    after = _dump()
    after[dc.HEALTH]["state"] = "67/68"
    assert _failed(_run(after=after)) == ["entity health"]


def test_deliberate_removal_needs_all_healthy():
    after = _dump()
    del after["sensor.localshift_net_cost"]
    allowed = {"sensor.localshift_net_cost"}
    after[dc.HEALTH]["state"] = "67/67"
    assert _failed(_run(after=after, allow_removed_entities=allowed)) == []
    after[dc.HEALTH]["state"] = "66/67"
    assert _failed(_run(after=after, allow_removed_entities=allowed)) == [
        "entity health"
    ]


def test_lost_summary_attribute_fails_unless_allowed():
    after = _dump()
    del after[dc.SUMMARY]["attributes"]["cycle_id"]
    assert _failed(_run(after=after)) == [
        "attributes kept: localshift_optimizer_summary"
    ]
    assert _failed(_run(after=after, allow_removed_attrs={"cycle_id"})) == []


def test_lost_plan_attribute_fails():
    after = _dump()
    del after[dc.PLAN]["attributes"]["decisions"]
    assert _failed(_run(after=after)) == [
        "attributes kept: localshift_optimizer_plan_detailed"
    ]


def test_changed_config_option_fails():
    after = _dump()
    after[dc.SUMMARY]["attributes"]["config_options"]["battery_target"] = 80.0
    checks = _run(after=after)
    assert _failed(checks) == ["config_options identical"]
    assert "battery_target 95.0->80.0" in next(c.detail for c in checks if not c.ok)


@pytest.mark.parametrize(
    ("entity", "state", "check"),
    [
        (dc.AUTOMATION, "off", "automation enabled"),
        (dc.DRY_RUN, "on", "dry run off"),
        (dc.MODE, "manual", "optimization mode unchanged"),
        (dc.SUMMARY, "failed", "summary state success"),
    ],
)
def test_switch_and_state_regressions_fail(entity, state, check):
    after = _dump()
    after[entity]["state"] = state
    assert _failed(_run(after=after)) == [check]


def test_empty_plan_fails():
    after = _dump()
    after[dc.PLAN]["attributes"]["total_slots"] = 0
    assert _failed(_run(after=after)) == ["plan slot count"]


def test_slot_count_drift_within_tolerance_passes():
    after = _dump()
    after[dc.PLAN]["attributes"]["total_slots"] = 59 - dc.SLOT_COUNT_TOLERANCE
    assert _failed(_run(after=after)) == []
    after[dc.PLAN]["attributes"]["total_slots"] -= 1
    assert _failed(_run(after=after)) == ["plan slot count"]


def test_initial_soc_far_from_powerwall_fails():
    after = _dump()
    after[dc.POWERWALL_SOC]["state"] = "60"
    assert _failed(_run(after=after)) == ["initial SOC matches Powerwall"]


def test_missing_powerwall_reading_fails():
    after = _dump()
    del after[dc.POWERWALL_SOC]
    assert _failed(_run(after=after)) == ["initial SOC matches Powerwall"]


def test_dw_entry_move_fails_unless_allowed():
    after = _dump()
    after[dc.SUMMARY]["attributes"]["dw_entry_soc_pct"] = 80.0
    assert _failed(_run(after=after)) == ["projected DW entry"]
    assert _failed(_run(after=after, allow_dw_entry_move=True)) == []


def test_dw_entry_absent_on_both_sides_passes():
    before, after = _dump(), _dump()
    before[dc.SUMMARY]["attributes"]["dw_entry_soc_pct"] = None
    after[dc.SUMMARY]["attributes"]["dw_entry_soc_pct"] = None
    assert _failed(_run(before=before, after=after)) == []


def test_error_line_fails():
    log = _log() + [
        "2026-10-14 10:20:20.000 ERROR (MainThread) [custom_components.localshift.coordinator] boom"
    ]
    assert _failed(_run(log=log)) == ["no localshift errors"]


def test_traceback_through_localshift_fails():
    log = _log() + [
        "2026-10-14 10:20:20.000 ERROR (MainThread) [homeassistant.core] Error doing job",
        "Traceback (most recent call last):",
        '  File "/config/custom_components/localshift/engine/x.py", line 3, in f',
        "ValueError: nope",
    ]
    assert _failed(_run(log=log)) == ["no localshift errors"]


def test_other_integration_error_is_ignored():
    log = _log() + [
        "2026-10-14 10:20:20.000 ERROR (MainThread) [custom_components.hacs] unrelated",
        "Traceback (most recent call last):",
        '  File "/config/custom_components/hacs/x.py", line 3, in f',
    ]
    assert _failed(_run(log=log)) == []


def test_unknown_warning_is_reported_but_does_not_fail():
    log = _log() + [
        "2026-10-14 10:20:20.000 WARNING (MainThread) [custom_components.localshift.state.machine] new thing"
    ]
    checks = _run(log=log)
    assert _failed(checks) == []
    assert "1 beyond" in next(c.detail for c in checks if "warnings" in c.name)


def test_benign_warning_repeated_late_is_reported():
    log = _log() + [
        "2026-10-14 10:20:20.000 WARNING (MainThread) [custom_components.localshift.engine.optimizer_facade] "
        "DP optimizer: invalid SOC non_positive, skipping"
    ]
    assert "1 beyond" in next(c.detail for c in _run(log=log) if "warnings" in c.name)


def test_too_few_plans_fails():
    assert _failed(_run(log=_log()[:5])) == ["planner running"]


def test_planner_gone_quiet_fails():
    checks = dc.run_checks(_dump(), _dump(), _log(), datetime(2026, 10, 14, 10, 30, 0))
    assert _failed(checks) == ["planner running"]


def test_unreadable_log_fails():
    assert _failed(_run(log=["no timestamps here"])) == ["log readable"]


def _component(tmp_path, src_mtime, pyc_mtime):
    comp = tmp_path / "localshift"
    (comp / "__pycache__").mkdir(parents=True)
    src = comp / "sensor.py"
    src.write_text("x = 1\n")
    pyc = comp / "__pycache__" / "sensor.cpython-314.pyc"
    pyc.write_bytes(b"")
    os.utime(src, (src_mtime, src_mtime))
    os.utime(pyc, (pyc_mtime, pyc_mtime))
    return comp


RESTART = datetime(2026, 10, 14, 10, 8, 2)


def test_compiled_current_passes(tmp_path):
    comp = _component(tmp_path, RESTART.timestamp() - 100, RESTART.timestamp() + 10)
    assert dc.check_compiled(comp, RESTART, expect_recompile=True).ok


def test_compiled_older_than_source_fails(tmp_path):
    comp = _component(tmp_path, RESTART.timestamp() + 50, RESTART.timestamp() + 10)
    assert not dc.check_compiled(comp, RESTART, expect_recompile=False).ok


def test_compiled_before_restart_fails_only_when_recompile_expected(tmp_path):
    comp = _component(tmp_path, RESTART.timestamp() - 5000, RESTART.timestamp() - 4000)
    assert dc.check_compiled(comp, RESTART, expect_recompile=False).ok
    assert not dc.check_compiled(comp, RESTART, expect_recompile=True).ok


def test_no_compiled_files_fails(tmp_path):
    (tmp_path / "localshift").mkdir()
    assert not dc.check_compiled(tmp_path / "localshift", RESTART, False).ok


def test_main_exit_codes(tmp_path, capsys, monkeypatch):
    def write(name, ents):
        path = tmp_path / name
        path.write_text(
            json.dumps({
                "meta": {},
                "groups": {"g": [{"entity_id": eid, **e} for eid, e in ents.items()]},
            })
        )
        return path

    good = write("good.json", _dump())
    broken_ents = copy.deepcopy(_dump())
    broken_ents[dc.AUTOMATION]["state"] = "off"
    broken = write("broken.json", broken_ents)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log = tmp_path / "ha.log"
    ls = "[custom_components.localshift.engine.optimizer_facade]"
    log.write_text(
        "\n".join(
            f"{stamp}.000 INFO (MainThread) {ls} Pre-charge decision: x"
            for _ in range(2)
        )
    )

    base = ["--before", str(good), "--log", str(log)]
    assert dc.main([*base, "--after", str(good)]) == 0
    assert dc.main([*base, "--after", str(broken)]) == 1
    assert "FAIL  automation enabled" in capsys.readouterr().out

    # Before the first health tick nothing is judged, even a broken switch.
    unticked = copy.deepcopy(broken_ents)
    unticked[dc.HEALTH]["state"] = "0/0"
    assert dc.main([*base, "--after", str(write("unticked.json", unticked))]) == 2
    out = capsys.readouterr().out
    assert "NOT SETTLED: re-run later" in out
    assert "FAIL" not in out and "PASS" not in out

    # --wait-settled re-fetches until the tick lands, then judges.
    fetches = iter([unticked, _dump()])
    monkeypatch.setattr(dc, "fetch_live_states", lambda: next(fetches))
    monkeypatch.setattr(dc, "SETTLE_POLL_S", 0)
    after = str(write("unticked.json", unticked))
    assert dc.main([*base, "--after", after, "--wait-settled", "60"]) == 0
    # A timeout that passes before the tick still reports NOT SETTLED.
    assert dc.main([*base, "--after", after, "--wait-settled", "0"]) == 2


def _health(ents, statuses: dict[str, str]):
    ents[dc.HEALTH]["state"] = (
        f"{sum(s == 'ok' for s in statuses.values())}/{len(statuses)}"
    )
    ents[dc.HEALTH]["attributes"] = {
        "entities": {},
        "localshift_entities": {
            eid: {"entity_id": eid, "status": s} for eid, s in statuses.items()
        },
    }
    return ents


ACCURACY = "sensor.localshift_forecast_accuracy"


def test_unknown_since_restart_is_not_settled():
    before = _health(_dump(), {ACCURACY: "ok", "sensor.localshift_net_cost": "ok"})
    after = _health(_dump(), {ACCURACY: "unknown", "sensor.localshift_net_cost": "ok"})
    assert ACCURACY in dc.not_settled(before, after)


@pytest.mark.parametrize(
    ("was", "now"),
    [
        ("ok", "unavailable"),  # a real failure, not a slow first value
        ("unknown", "unknown"),  # already unknown before the restart
    ],
)
def test_other_health_drops_are_judged(was, now):
    before = _health(_dump(), {ACCURACY: was, "sensor.localshift_net_cost": "ok"})
    after = _health(_dump(), {ACCURACY: now, "sensor.localshift_net_cost": "ok"})
    assert dc.not_settled(before, after) is None


def test_wait_settled_polls_until_settled_or_timeout():
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    unticked = _dump()
    unticked[dc.HEALTH]["state"] = "0/0"
    fetches = iter([unticked, OSError("connection refused"), _dump()])

    def fetch():
        item = next(fetches)
        if isinstance(item, Exception):
            raise item
        return item

    kw = {"sleep": sleep, "clock": lambda: clock[0]}
    settled = dc.wait_settled(_dump(), unticked, 120, fetch=fetch, **kw)
    assert dc.not_settled(_dump(), settled) is None
    assert clock[0] == 3 * dc.SETTLE_POLL_S

    clock[0] = 0.0
    stuck = dc.wait_settled(_dump(), unticked, 50, fetch=lambda: unticked, **kw)
    assert dc.not_settled(_dump(), stuck) is not None
    assert clock[0] == 50
