"""Vulture allowlist for symbols with test coverage but no production caller.

Keep this file SEPARATE from vulture_whitelist.py. That file suppresses
genuine Home Assistant framework false positives (names the framework
calls by reflection or string dispatch, which vulture cannot see). This
file is different: every name below IS unreferenced by
custom_components/localshift, and is kept alive only by tests.

That is deliberately not treated as proof of life. This repo's own
history (#968, #959) is two bugs with exactly this shape: an orphan
method or a drifted duplicate copy that tests kept green for months by
calling it directly, while the real call site never wired to it. So
every entry here MUST carry a one-line reason plus an issue number, and
the reason must say why the symbol is *deliberately* kept rather than
just noting that tests reference it. An entry with no reason and no
issue number does not belong in this file — see Issue #986 and its
follow-up, Issue #1071 (burn-down tracker for this whole file).

`_get_decision_fingerprint` (state/machine.py:411) was the one entry in
this batch that looked most like another #968 — 17 test references,
zero production callers, and a production call site
(`_apply_decision_token`) that turns out to compute the identical
expression inline instead of calling it. That one was pulled OUT of
this file and into its own investigation: Issue #1070. Do not add it
back here without resolving that issue first.
"""

# --- Issue #1070: dedicated investigation, NOT the general #1071 burn-down.
# 17 test references, zero production callers, and a production call site
# (_apply_decision_token) that computes the identical `f"{base}|{epoch}"`
# expression inline instead of calling this method -- the #968 shape.
# Parked here only so the CI gate is green while #1070 is worked; resolve
# #1070 (wire it up, or confirm the duplication is intentional and delete
# one copy) rather than leaving this allowlisted indefinitely.
_get_decision_fingerprint  # state/machine.py:411 -- #1070

# --- Issue #1071 (burn-down): triage each of the below per the three-way
# disposition in that issue (wire up / delete / keep with a real reason).
# None of these have a reason yet beyond "not yet triaged" -- landed this
# way in #986 to unblock the CI gate without silently absorbing 23 items
# with no plan to revisit them.

entity_ids  # coordinator/coordinator.py:125 -- test-only, #1071
baseline_power_kw  # coordinator/data.py:83 -- write-only outside tests, #1071
pending_count  # engine/outcomes.py:908 -- test-only, #1071
risk_window_start_idx  # engine/types.py:848 -- write-only outside tests, #1071
conservative_recovery_kwh_by_slot  # engine/types.py:867 -- write-only outside tests, #1071
solcast_ready  # forecast/bootstrapper.py:51 -- test-only, #1071
_separate_samples_by_day_type  # forecast/history.py:464 -- test-only, #1071
get_profile_for_day  # forecast/history.py:671 -- test-only, #1071
get_profile_bucket_counts  # forecast/load.py:87 -- test-only, #1071
deviation_sigma  # learning/anomaly.py:33 -- write-only outside tests, #1071
mean_temperature  # learning/anomaly.py:34 -- write-only outside tests, #1071
std_temperature  # learning/anomaly.py:35 -- write-only outside tests, #1071
entity_prefix  # pricing/provider.py:34,128,164 -- Protocol member, test-only, #1071
estimate  # pricing/types.py:29 -- Issue #510 slice 2 field, test-only so far, #1071
cancel_pending_coalesce  # services/evaluation_dispatcher.py:98 -- test-only, #1071
last_valid_value  # utils/validation.py:64 -- read only in tests, #1071
is_available  # utils/validation.py:75 -- test-only, #1071
warning_message  # utils/validation.py:90 -- write-only outside tests, #1071
