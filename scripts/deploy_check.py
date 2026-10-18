#!/usr/bin/env python3
"""deploy_check.py — compare the live system before and after a deploy.

Takes two entity dumps written by ``scripts/ha_entity_dump.py`` (one from just
before the restart, one from after the planner has produced at least two
plans), plus the Home Assistant log, and checks the invariants a deploy must
not break. Prints a pass/fail table and exits 1 if any invariant fails.

A dump taken before the integration's first health tick (entity health reads
"0/0", or entities read "unknown" that were healthy before the restart) is not
judged: the script prints NOT SETTLED and exits 3. The tick lands 5-10
minutes after a restart; --wait-settled re-fetches the after state until then,
and if the deadline passes first it judges anyway: a sensor a deploy broke
reads "unknown" for good, and that must fail, not wait forever.

The Home Assistant log is truncated at every restart, so "since the restart"
is the whole file and the restart time is its first timestamp. Without --log
the log is fetched over the API: the network share keeps serving cached pages
of the previous log for minutes after a restart, so a file read there can
show the old restart time and a planner that looks stopped.

Run with --preflight (and no dumps) before reserving: it reads the live plan
and refuses (exit 1) inside the demand window, when a grid-charge slot starts
or is running within the next 30 minutes, or when the pre-charge backstop is
active or within 30 minutes of firing. A restart cuts off a charge in flight.

Usage
-----
  scripts/deploy_check.py --preflight
  scripts/deploy_check.py --wait-restart 21:05:30   # time just before the restart was issued
  scripts/deploy_check.py --before B.json --after A.json \\
      --component-dir "$HA_CONFIG/custom_components/localshift"

  --log PATH                   read the log from a file instead of the API
  --allow-removed-attr KEY     an attribute the change removed on purpose (repeatable)
  --allow-removed-entity ID    an entity the change removed on purpose (repeatable)
  --allow-dw-entry-move        the change is meant to move the projected entry
  --expect-recompile           code was copied: every compiled file must post-date the restart
  --wait-settled SECONDS       re-fetch the after state every 20 s until settled,
                               then judge (cannot be combined with --log)
  --wait-restart TIME          wait (up to --timeout, default 900 s) until HA has gone
                               down and come back with a log that starts after this
                               time (HH:MM:SS today, or YYYY-MM-DD HH:MM:SS); take
                               the after dump only once this exits 0

Exit codes: 0 all invariants hold, 1 an invariant failed, 2 usage error,
3 not settled yet (only without --wait-settled).
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

SUMMARY = "sensor.localshift_optimizer_summary"
PLAN = "sensor.localshift_optimizer_plan_detailed"
HEALTH = "sensor.localshift_entity_health"
AUTOMATION = "switch.localshift_automation_enabled"
DRY_RUN = "switch.localshift_dry_run"
MODE = "select.localshift_optimization_mode"
POWERWALL_SOC = "sensor.my_home_percentage_charged"

SOC_TOLERANCE_PCT = 5.0
DW_ENTRY_TOLERANCE_PCT = 3.0
# The plan covers "now until the end of the forecast", so its slot count
# drifts by a few slots between two dumps taken minutes apart.
SLOT_COUNT_TOLERANCE = 12
PLAN_LINE = "Pre-charge decision"
# One plan a minute; allow a missed tick before calling the planner quiet.
PLAN_GAP_MAX = timedelta(seconds=150)
MIN_PLANS = 2
SETTLE_POLL_S = 20
RESTART_POLL_S = 3
# Distinct from argparse's 2, so a retry wrapper cannot loop on a typo.
NOT_SETTLED_EXIT = 3

UNAVAILABLE = {"unavailable", "unknown"}
LOG_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+ ")
# Start-up warnings seen on every healthy restart: (pattern, max count, seconds
# after the restart within which they are tolerated).
BENIGN_WARNINGS: list[tuple[re.Pattern, int, int]] = [
    (re.compile(r"Cannot read Tesla tariff"), 1, 60),
    (re.compile(r"invalid SOC non_positive, skipping"), 2, 60),
]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def load_dump(path: Path) -> dict[str, dict]:
    """Flatten a dump's groups into ``{entity_id: state}``."""
    data = json.loads(Path(path).read_text())
    return {e["entity_id"]: e for group in data["groups"].values() for e in group}


def _state(ents: dict[str, dict], eid: str) -> str | None:
    ent = ents.get(eid)
    return None if ent is None else ent.get("state")


def _attrs(ents: dict[str, dict], eid: str) -> dict:
    return (ents.get(eid) or {}).get("attributes") or {}


def _localshift(ents: dict[str, dict]) -> set[str]:
    return {eid for eid in ents if "localshift" in eid}


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _health_entries(ents: dict[str, dict]) -> dict[str, dict]:
    """The per-entity statuses behind the entity health sensor's count."""
    attrs = _attrs(ents, HEALTH)
    return {**(attrs.get("entities") or {}), **(attrs.get("localshift_entities") or {})}


def not_settled(before, after) -> str | None:
    """Why the after dump predates the first health tick, or None once settled.

    Until the tick the health sensor reads "0/0"; just after it, entities that
    have not produced a value yet read "unknown". Only an entity that was
    healthy in the before dump counts, so a real breakage is still judged.
    """
    health = _state(after, HEALTH)
    if health == "0/0":
        return "entity health 0/0: no health tick since the restart"
    was_ok = {k for k, e in _health_entries(before).items() if e.get("status") == "ok"}
    unhealthy = {
        k: e for k, e in _health_entries(after).items() if e.get("status") != "ok"
    }
    if unhealthy and all(
        e.get("status") == "unknown" and k in was_ok for k, e in unhealthy.items()
    ):
        names = sorted(e.get("entity_id") or k for k, e in unhealthy.items())
        return f"entity health {health}: unknown since the restart: {', '.join(names)}"
    return None


def check_entities(before, after, allow_removed: set[str]) -> list[Check]:
    lost = sorted(_localshift(before) - _localshift(after) - allow_removed)
    newly_dead = sorted(
        eid
        for eid in _localshift(after)
        if _state(after, eid) in UNAVAILABLE and _state(before, eid) not in UNAVAILABLE
    )
    health_before, health_after = _state(before, HEALTH), _state(after, HEALTH)
    health_ok = health_after is not None and health_after == health_before
    if allow_removed and health_after is not None:
        # A deliberate removal changes the total; require every entity healthy.
        healthy, _, total = health_after.partition("/")
        health_ok = healthy == total
    return [
        Check(
            "entities present",
            not lost,
            f"lost: {', '.join(lost)}"
            if lost
            else f"{len(_localshift(after))} localshift entities",
        ),
        Check(
            "none newly unavailable", not newly_dead, ", ".join(newly_dead) or "none"
        ),
        Check("entity health", health_ok, f"{health_before} -> {health_after}"),
    ]


def check_attributes(before, after, allow_removed: set[str]) -> list[Check]:
    checks = []
    for eid in (SUMMARY, PLAN):
        lost = sorted(
            set(_attrs(before, eid)) - set(_attrs(after, eid)) - allow_removed
        )
        checks.append(
            Check(
                f"attributes kept: {eid.split('.', 1)[1]}",
                not lost,
                f"lost: {', '.join(lost)}"
                if lost
                else f"{len(_attrs(after, eid))} keys",
            )
        )
    opts_before = _attrs(before, SUMMARY).get("config_options")
    opts_after = _attrs(after, SUMMARY).get("config_options")
    if opts_after is None or opts_before is None:
        checks.append(
            Check("config_options identical", False, "config_options missing")
        )
    else:
        changed = sorted(
            k
            for k in set(opts_before) | set(opts_after)
            if opts_before.get(k) != opts_after.get(k)
        )
        checks.append(
            Check(
                "config_options identical",
                not changed,
                "changed: "
                + ", ".join(
                    f"{k} {opts_before.get(k)!r}->{opts_after.get(k)!r}"
                    for k in changed
                )
                if changed
                else "identical",
            )
        )
    return checks


def check_switches(before, after) -> list[Check]:
    mode_before, mode_after = _state(before, MODE), _state(after, MODE)
    return [
        Check(
            "automation enabled",
            _state(after, AUTOMATION) == "on",
            f"{_state(after, AUTOMATION)}",
        ),
        Check(
            "dry run off", _state(after, DRY_RUN) == "off", f"{_state(after, DRY_RUN)}"
        ),
        Check(
            "optimization mode unchanged",
            mode_after is not None and mode_after == mode_before,
            f"{mode_before} -> {mode_after}",
        ),
    ]


def check_plan(before, after, allow_dw_entry_move: bool) -> list[Check]:
    summary = _attrs(after, SUMMARY)
    checks = [
        Check(
            "summary state success",
            _state(after, SUMMARY) == "success",
            f"{_state(after, SUMMARY)}",
        )
    ]

    slots_before = _as_float(_attrs(before, PLAN).get("total_slots"))
    slots_after = _as_float(_attrs(after, PLAN).get("total_slots"))
    slots_ok = (
        slots_before is not None
        and slots_after is not None
        and slots_after > 0
        and abs(slots_after - slots_before) <= SLOT_COUNT_TOLERANCE
    )
    checks.append(
        Check("plan slot count", slots_ok, f"{slots_before} -> {slots_after}")
    )

    initial = _as_float(summary.get("initial_soc_pct"))
    powerwall = _as_float(_state(after, POWERWALL_SOC))
    soc_ok = (
        initial is not None
        and powerwall is not None
        and abs(initial - powerwall) <= SOC_TOLERANCE_PCT
    )
    checks.append(
        Check(
            "initial SOC matches Powerwall",
            soc_ok,
            f"planner {initial} vs Powerwall {powerwall}",
        )
    )

    dw_before = _as_float(_attrs(before, SUMMARY).get("dw_entry_soc_pct"))
    dw_after = _as_float(summary.get("dw_entry_soc_pct"))
    if allow_dw_entry_move:
        checks.append(
            Check(
                "projected DW entry",
                dw_after is not None,
                f"{dw_before} -> {dw_after} (move allowed)",
            )
        )
    else:
        # Both null is legitimate: no demand window left in the horizon.
        dw_ok = (dw_before is None and dw_after is None) or (
            dw_before is not None
            and dw_after is not None
            and abs(dw_after - dw_before) <= DW_ENTRY_TOLERANCE_PCT
        )
        checks.append(Check("projected DW entry", dw_ok, f"{dw_before} -> {dw_after}"))
    return checks


def _log_time(line: str) -> datetime | None:
    m = LOG_TS.match(line)
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") if m else None


def _records(lines: list[str]):
    """Group the log into ``(timestamp, header, continuation lines)`` records."""
    record = None
    for line in lines:
        ts = _log_time(line)
        if ts is not None:
            if record:
                yield record
            record = (ts, line, [])
        elif record:
            record[2].append(line)
    if record:
        yield record


def _is_error(header: str, body: list[str]) -> bool:
    if "localshift" in header and (" ERROR " in header or "Traceback" in header):
        return True
    # Another component's error whose traceback runs through our code.
    return any("custom_components/localshift" in line for line in body)


class _WarningTally:
    """Counts localshift warnings that fall outside the known start-up set."""

    def __init__(self, restart: datetime):
        self.restart = restart
        self.seen = [0] * len(BENIGN_WARNINGS)
        self.unexpected: list[str] = []

    def add(self, ts: datetime, header: str) -> None:
        for i, (pat, limit, window) in enumerate(BENIGN_WARNINGS):
            if pat.search(header):
                self.seen[i] += 1
                if self.seen[i] <= limit and ts - self.restart <= timedelta(
                    seconds=window
                ):
                    return
                break
        self.unexpected.append(header.strip())


def _planner_check(plan_times: list[datetime], now: datetime) -> Check:
    if len(plan_times) < MIN_PLANS:
        return Check(
            "planner running",
            False,
            f"{len(plan_times)} plans since restart, need {MIN_PLANS}",
        )
    last_gap = plan_times[-1] - plan_times[-2]
    age = now - plan_times[-1]
    return Check(
        "planner running",
        last_gap <= PLAN_GAP_MAX and age <= PLAN_GAP_MAX,
        f"{len(plan_times)} plans, last gap {int(last_gap.total_seconds())}s, "
        f"last plan {int(age.total_seconds())}s ago",
    )


def check_log(lines: list[str], now: datetime) -> tuple[list[Check], datetime | None]:
    """Check the log written since the restart. ``now`` is local wall time."""
    records = list(_records(lines))
    if not records:
        return [Check("log readable", False, "no timestamped lines")], None
    restart = records[0][0]

    errors, plan_times, warnings = [], [], _WarningTally(restart)
    for ts, header, body in records:
        if _is_error(header, body):
            errors.append(header.strip())
        elif " WARNING " in header and "custom_components.localshift" in header:
            warnings.add(ts, header)
        elif PLAN_LINE in header and "localshift" in header:
            plan_times.append(ts)

    unexpected = warnings.unexpected
    return [
        Check(
            "no localshift errors",
            not errors,
            f"{len(errors)}: {errors[0][:140]}" if errors else "none",
        ),
        # Reported, not failed: a live bug under investigation can legitimately
        # warn. The caller compares the count before and after.
        Check(
            "localshift warnings (informational)",
            True,
            f"{len(unexpected)} beyond the known start-up set"
            + (f"; first: {unexpected[0][:120]}" if unexpected else ""),
        ),
        _planner_check(plan_times, now),
    ], restart


def check_compiled(
    component_dir: Path, restart: datetime, expect_recompile: bool
) -> Check:
    """Compiled files must not be older than their source; after a copy they
    must also post-date the restart, which proves the new code was loaded."""
    stale, old, count = [], [], 0
    for src in component_dir.rglob("*.py"):
        compiled = list((src.parent / "__pycache__").glob(f"{src.stem}.cpython-*.pyc"))
        if not compiled:
            continue  # never imported
        count += 1
        newest = max(p.stat().st_mtime for p in compiled)
        if newest < src.stat().st_mtime:
            stale.append(src.name)
        if expect_recompile and newest < restart.timestamp() - 60:
            old.append(src.name)
    if count == 0:
        return Check("compiled files", False, f"none found under {component_dir}")
    if stale:
        return Check(
            "compiled files", False, f"older than source: {', '.join(stale[:5])}"
        )
    if old:
        return Check(
            "compiled files",
            False,
            f"{len(old)} pre-date the restart: {', '.join(old[:5])}",
        )
    return Check("compiled files", True, f"{count} current")


RESTART_CLEAR_MIN = 30
DEFAULT_DW = ("15:00", "21:00")


def _hhmm(value, default: str) -> str:
    return str(value)[:5] if value else default


def preflight(ents: dict[str, dict], now: datetime) -> list[Check]:
    """Is it safe to restart now? ``now`` must be timezone-aware local time."""
    summary = _attrs(ents, SUMMARY)
    opts = summary.get("config_options") or {}
    start = _hhmm(opts.get("demand_window_start"), DEFAULT_DW[0])
    end = _hhmm(opts.get("demand_window_end"), DEFAULT_DW[1])
    clock = now.strftime("%H:%M")
    in_dw = start <= clock < end

    horizon = now + timedelta(minutes=RESTART_CLEAR_MIN)
    charging = []
    for slot in _attrs(ents, PLAN).get("decisions") or []:
        begins = datetime.fromisoformat(slot["timestamp_iso"])
        ends = begins + timedelta(minutes=slot.get("slot_interval_minutes") or 5)
        if "charge" in slot.get("action", "") and ends > now and begins < horizon:
            charging.append(begins.strftime("%H:%M"))

    slack = _as_float(summary.get("precharge_runway_slack_min"))
    backstop = bool(summary.get("precharge_backstop_active"))
    return [
        Check("outside the demand window", not in_dw, f"{clock}, window {start}-{end}"),
        Check(
            f"no grid charge within {RESTART_CLEAR_MIN} min",
            not charging,
            f"charge slots at {', '.join(charging)}" if charging else "none",
        ),
        Check("backstop not active", not backstop, f"active={backstop}"),
        Check(
            f"runway slack over {RESTART_CLEAR_MIN} min",
            slack is None or slack > RESTART_CLEAR_MIN,
            f"{slack} min",
        ),
    ]


def run_checks(
    before,
    after,
    log_lines,
    now,
    *,
    component_dir=None,
    allow_removed_attrs=frozenset(),
    allow_removed_entities=frozenset(),
    allow_dw_entry_move=False,
    expect_recompile=False,
) -> list[Check]:
    checks = check_entities(before, after, set(allow_removed_entities))
    checks += check_attributes(before, after, set(allow_removed_attrs))
    checks += check_switches(before, after)
    checks += check_plan(before, after, allow_dw_entry_move)
    log_checks, restart = check_log(log_lines, now)
    checks += log_checks
    if component_dir is not None and restart is not None:
        checks.append(check_compiled(Path(component_dir), restart, expect_recompile))
    return checks


def render(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = [
        f"{'PASS' if c.ok else 'FAIL'}  {c.name:<{width}}  {c.detail}" for c in checks
    ]
    failed = sum(not c.ok for c in checks)
    lines.append("")
    lines.append(
        f"{len(checks) - failed}/{len(checks)} passed"
        + (f", {failed} FAILED" if failed else "")
    )
    return "\n".join(lines)


def fetch_live_log() -> list[str]:
    """Read the current log over the API, with ha_entity_dump's credentials."""
    import types
    import urllib.request

    sys.path.insert(0, str(Path(__file__).parent))
    from ha_entity_dump import resolve_credentials

    url, token = resolve_credentials(types.SimpleNamespace(url=None, token=None))
    req = urllib.request.Request(
        f"{url}/api/error_log", headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode(errors="replace").splitlines()


def fetch_live_states() -> dict[str, dict]:
    """The after state over the API, filtered to the groups a dump keeps."""
    import types
    import urllib.request

    sys.path.insert(0, str(Path(__file__).parent))
    from ha_entity_dump import bucket, resolve_credentials

    url, token = resolve_credentials(types.SimpleNamespace(url=None, token=None))
    req = urllib.request.Request(
        f"{url}/api/states", headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        states = json.loads(resp.read().decode())
    return {s["entity_id"]: s for s in states if bucket(s["entity_id"])}


def _api_up(urlopen=None) -> bool:
    """True once HA reports RUNNING; any error, other status or state is down.

    The HTTP server answers early in start-up, before custom integrations
    load, so a 200 alone does not mean LocalShift's entities exist yet.
    """
    import types
    import urllib.request

    sys.path.insert(0, str(Path(__file__).parent))
    from ha_entity_dump import resolve_credentials

    url, token = resolve_credentials(types.SimpleNamespace(url=None, token=None))
    req = urllib.request.Request(
        f"{url}/api/config", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with (urlopen or urllib.request.urlopen)(req, timeout=10) as resp:
            return json.loads(resp.read().decode()).get("state") == "RUNNING"
    except (OSError, ValueError, http.client.HTTPException):
        return False


def _log_restart_time(lines: list[str]) -> datetime | None:
    """The log is truncated at every restart: its first timestamp is the start."""
    return next((t for line in lines if (t := _log_time(line))), None)


def restart_issued_at(value: str, now: datetime) -> datetime:
    """Parse the restart time: "YYYY-MM-DD HH:MM:SS", or "HH:MM:SS" meaning today.

    A time later than now is refused rather than guessed at: read as
    yesterday it would let the still-running old process pass at once.
    Across midnight, pass the full date.
    """
    try:
        started = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        started = datetime.combine(
            now.date(), datetime.strptime(value, "%H:%M:%S").time()
        )
    if started > now:
        raise ValueError(f"restart time {value} is in the future")
    return started


def wait_restart(
    started: datetime,
    timeout_s: float,
    *,
    up=None,
    fetch_log=None,
    sleep=time.sleep,
    clock=time.monotonic,
) -> str | None:
    """Wait for HA to go down and come back after a restart issued at ``started``.

    A snapshot taken while HA is shutting down reads every entity as missing,
    and one taken just before the restart lands judges the old process. So
    this waits for HA to stop reporting RUNNING, then to report it again, and then
    requires the log's first timestamp (the new process's start) to be no
    earlier than ``started``. A restart fast enough to be missed between polls
    still passes on the log alone. Returns None when restarted, else why not.
    """
    up = up or _api_up
    fetch_log = fetch_log or fetch_live_log
    deadline = clock() + timeout_s
    seen_down = False
    while clock() < deadline:
        if not up():
            seen_down = True
        else:
            try:
                restart = _log_restart_time(fetch_log())
            except (OSError, ValueError, http.client.HTTPException):
                restart = None
            if restart is not None and restart >= started:
                return None
        sleep(RESTART_POLL_S)
    return f"no restart after {started:%H:%M:%S} within {timeout_s:.0f} s" + (
        " (API went down but the log never showed a new start)" if seen_down else ""
    )


def wait_settled(
    before, after, timeout_s, *, fetch=None, sleep=time.sleep, clock=time.monotonic
):
    """Re-fetch the after state every SETTLE_POLL_S until settled or timed out."""
    fetch = fetch or fetch_live_states
    deadline = clock() + timeout_s
    while (reason := not_settled(before, after)) and clock() < deadline:
        print(f"waiting: {reason}", file=sys.stderr)
        sleep(min(SETTLE_POLL_S, max(0.0, deadline - clock())))
        try:
            after = fetch()
        except (OSError, ValueError) as exc:  # HA still coming up
            print(f"waiting: fetch failed: {exc}", file=sys.stderr)
    return after


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--preflight", action="store_true")
    ap.add_argument("--before", type=Path)
    ap.add_argument("--after", type=Path)
    ap.add_argument("--log", type=Path)
    ap.add_argument("--component-dir", type=Path)
    ap.add_argument("--allow-removed-attr", action="append", default=[])
    ap.add_argument("--allow-removed-entity", action="append", default=[])
    ap.add_argument("--allow-dw-entry-move", action="store_true")
    ap.add_argument("--expect-recompile", action="store_true")
    ap.add_argument("--wait-settled", type=float, metavar="SECONDS")
    ap.add_argument("--wait-restart", metavar="HH:MM:SS")
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args(argv)
    if args.wait_restart:
        try:
            started = restart_issued_at(args.wait_restart, datetime.now())
        except ValueError as exc:
            ap.error(str(exc))
        reason = wait_restart(started, args.timeout)
        print(
            reason
            or f"restarted: HA came back with a log starting after {started:%H:%M:%S}"
        )
        return 0 if reason is None else 1
    if args.preflight:
        checks = preflight(fetch_live_states(), datetime.now().astimezone())
        print(render(checks))
        return 0 if all(c.ok for c in checks) else 1
    if args.before is None or args.after is None:
        ap.error("--before and --after are required unless --preflight")
    if args.wait_settled is not None and args.log is not None:
        # The wait moves "now" past the end of a captured log file, so the
        # planner would look stopped; the API log is read after the wait.
        ap.error("--wait-settled reads the log over the API; drop --log")

    before, after = load_dump(args.before), load_dump(args.after)
    if args.wait_settled is not None:
        after = wait_settled(before, after, args.wait_settled)
        if reason := not_settled(before, after):
            print(
                f"Still not settled after {args.wait_settled:.0f} s, judging anyway: {reason}"
            )
    elif reason := not_settled(before, after):
        print(f"NOT SETTLED: re-run later\n  {reason}")
        return NOT_SETTLED_EXIT

    checks = run_checks(
        before,
        after,
        args.log.read_text(errors="replace").splitlines()
        if args.log
        else fetch_live_log(),
        datetime.now(),
        component_dir=args.component_dir,
        allow_removed_attrs=frozenset(args.allow_removed_attr),
        allow_removed_entities=frozenset(args.allow_removed_entity),
        allow_dw_entry_move=args.allow_dw_entry_move,
        expect_recompile=args.expect_recompile,
    )
    print(render(checks))
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
