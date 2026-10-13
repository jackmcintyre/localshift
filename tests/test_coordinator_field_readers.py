"""Reader census for ``CoordinatorData`` fields (issues #981, #986, #1063).

A ``CoordinatorData`` field that nothing in production reads is dead weight
at best and, at worst, a "decorative" field: something writes it every cycle,
a test asserts on it, and no behaviour depends on it. Issue #981 found
eighteen of them. Every one had a test reader and none had a production
reader, which is why the census here counts production code only. A test
that asserts a field's value mirrors the field; it does not consume it.

The census itself lives in ``scripts/dead_code_gate.py`` (``field_reads`` /
``unread_field_findings``) so the same result feeds the shrink-only ratchet:
a field with no reader is reported as ``unread-field`` and must be either in
``dead_code_baseline.txt`` (dead, awaiting deletion) or, if a script reads it
on purpose, in ``vulture_whitelist.py`` with the script cited. This file
tests the walker and pins the properties that matter.

DO NOT make a field "live" by widening the census to ``tests/`` or
``scripts/``. If a script reads a field on purpose, say so in the whitelist,
where the gate checks that the script still does.
"""

from __future__ import annotations

from pathlib import Path

from scripts import dead_code_gate as gate

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / gate.DEFAULT_PACKAGE
DATA_MODULE = PACKAGE / gate.DEFAULT_DATA_MODULE


def _layout() -> gate.Layout:
    return gate.Layout(
        root=REPO,
        package=PACKAGE,
        whitelist=REPO / gate.DEFAULT_WHITELIST,
        baseline=REPO / gate.DEFAULT_BASELINE,
        data_module=DATA_MODULE,
        data_class=gate.DEFAULT_DATA_CLASS,
    )


def _unread() -> set[str]:
    return {f.name for f in gate.unread_field_findings(_layout())}


# ---------------------------------------------------------------------------
# The walker
# ---------------------------------------------------------------------------


def test_plain_reads_through_the_coordinator() -> None:
    source = "class S:\n    def v(self):\n        return self.coordinator.data.soc\n"
    assert "soc" in gate.field_reads(source)


def test_parameter_named_data_is_the_snapshot() -> None:
    source = "def f(data):\n    return data.soc + data.load_power_kw\n"
    assert {"soc", "load_power_kw"} <= gate.field_reads(source)


def test_parameter_annotated_coordinator_data_under_any_name() -> None:
    source = "def f(snapshot: CoordinatorData):\n    return snapshot.soc\n"
    assert "soc" in gate.field_reads(source)


def test_parameter_named_data_but_typed_as_a_dict_is_not() -> None:
    source = "def f(data: dict[str, Any]):\n    return data.soc\n"
    assert "soc" not in gate.field_reads(source)


def test_local_alias_of_the_snapshot() -> None:
    source = (
        "class S:\n"
        "    def v(self):\n"
        "        d = self.coordinator.data\n"
        "        return d.soc\n"
    )
    assert "soc" in gate.field_reads(source)


def test_loop_variable_named_d_is_not_the_snapshot() -> None:
    """Issue #1063: ``d`` over planner decisions used to count as a reader."""
    source = (
        "def f(decisions):\n"
        "    total = 0\n"
        "    for d in decisions:\n"
        "        total += d.grid_charge\n"
        "    return [d.predicted_soc_pct for d in decisions], total\n"
    )
    assert gate.field_reads(source) == set()


def test_comprehension_rebinding_an_alias_is_not_the_snapshot() -> None:
    """Even where ``d`` IS the snapshot, a comprehension's own ``d`` is not."""
    source = (
        "class S:\n"
        "    def v(self, decisions):\n"
        "        d = self.coordinator.data\n"
        "        soc = d.soc\n"
        "        return soc, [d.grid_charge for d in decisions]\n"
    )
    reads = gate.field_reads(source)
    assert "soc" in reads
    assert "grid_charge" not in reads


def test_write_is_not_a_read() -> None:
    source = "def f(data):\n    data.primary_decision = ''\n"
    assert "primary_decision" not in gate.field_reads(source)


def test_augmented_assignment_is_a_read() -> None:
    source = "def f(data):\n    data.grid_import_cost += 1.0\n"
    assert "grid_import_cost" in gate.field_reads(source)


def test_subscript_and_method_call_through_a_field_are_reads() -> None:
    source = (
        "def f(data):\n"
        "    data.optimizer_summary['k'] = 1\n"
        "    data.decision_log.append(2)\n"
    )
    assert {"optimizer_summary", "decision_log"} <= gate.field_reads(source)


def test_getattr_with_a_literal_name() -> None:
    source = "def f(data):\n    return getattr(data, 'away_active', False)\n"
    assert "away_active" in gate.field_reads(source)


def test_getattr_over_a_module_level_table() -> None:
    """engine/outcomes.py and utils/costs.py read meters this way."""
    source = (
        "_KEYS = ('grid_import_kwh_today', 'grid_export_kwh_today')\n"
        "def snap(data):\n"
        "    labels = {'not_a_field': 1}\n"
        "    return {k: getattr(data, k) for k in _KEYS}, labels\n"
    )
    reads = gate.field_reads(source)
    assert {"grid_import_kwh_today", "grid_export_kwh_today"} <= reads
    # A string that is neither in a table nor passed to the helper is not a read.
    assert "not_a_field" not in reads


def test_getattr_helper_called_with_literal_names() -> None:
    """sensors/optimizer.py: ``_summary_or_attr(summary, d, "key")``."""
    source = (
        "def _pick(summary, data, key):\n"
        "    return getattr(data, key, None)\n"
        "def attrs(summary, d):\n"
        "    return {'label': _pick(summary, d, 'dw_entry_actual_at')}\n"
    )
    reads = gate.field_reads(source)
    assert "dw_entry_actual_at" in reads
    assert "label" not in reads


def test_table_strings_only_count_when_there_is_a_dynamic_getattr() -> None:
    source = "_KEYS = ('soc',)\ndef f(data):\n    return data.load_power_kw\n"
    assert gate.field_reads(source) == {"load_power_kw"}


# ---------------------------------------------------------------------------
# The real package
# ---------------------------------------------------------------------------


def test_declared_fields_are_found() -> None:
    fields = gate.declared_fields(DATA_MODULE.read_text(), gate.DEFAULT_DATA_CLASS)
    assert len(fields) > 100
    assert {"soc", "optimizer_summary", "optimizer_result"} <= set(fields)


def test_core_fields_are_seen_as_read() -> None:
    """Sanity: the census is not reporting live fields as unread.

    One per access style: plain attribute, the daily-totals string table
    (utils/costs.py), the energy-meter table (engine/outcomes.py), a literal
    getattr, and the sensors' summary-or-attribute helper.
    """
    unread = _unread()
    for field in (
        "soc",
        "optimizer_summary",
        "feed_in_forecast",
        "solar_kwh_today",
        "grid_import_kwh_today",
        "away_active",
        "dw_entry_actual_at",
    ):
        assert field not in unread, f"{field} is live but reported unread"


def test_optimizer_result_has_no_production_reader_and_is_whitelisted() -> None:
    """``optimizer_result`` is read by scripts and the scenario tripwire only.

    It must stay classified as live. Run C (2026-09-08) removed a parameter a
    name-based scan called dead, which raised inside the facade's broad
    ``except`` and silently disabled the inline optimizer; the only thing that
    noticed was a scenario test asserting ``optimizer_result is not None``.
    """
    assert "optimizer_result" in _unread(), (
        "optimizer_result now has a production reader: remove its entry from "
        "vulture_whitelist.py"
    )
    layout = _layout()
    result = gate.run(layout)
    target = gate.Finding(
        gate.UNREAD_FIELD,
        gate.DEFAULT_DATA_MODULE,
        f"{gate.DEFAULT_DATA_CLASS}.optimizer_result",
    )
    assert target in result.suppressed
    assert target not in result.baseline


def test_every_unread_field_is_accounted_for() -> None:
    """Unread fields are baselined debt or whitelisted, never unnoticed."""
    result = gate.run(_layout())
    unread = {f for f in result.findings if f.kind == gate.UNREAD_FIELD}
    assert unread, "the census found nothing: it is not running"
    stray = unread - result.suppressed - set(result.baseline)
    assert not stray, (
        "CoordinatorData fields with no production reader that are in neither "
        "dead_code_baseline.txt nor vulture_whitelist.py. Give the field a "
        "reader or delete it (and its writer):\n"
        + "\n".join(f"  {f.qualname}" for f in sorted(stray))
    )
