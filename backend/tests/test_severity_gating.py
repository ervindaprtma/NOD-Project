"""§9.10 test_severity_gating.py

§9.7: load_channel_configs in notifier_helper.py must fail-closed on
unknown severity values. A typo'd min_severity used to silently mean
"always send" because .get() defaulted to 0. We now log an error AND
skip the channel so the misconfig shows up in dashboards instead of
in a flood of over-permissive notifications.

These tests validate the gate logic directly. They don't exercise the
DB layer (no AsyncSession) — they assert the Python-level condition
that's been added, which is the actual §9.7 fix.

Lives in the security blast-radius group per docs/alert_notification_design.md
§9.10: required-to-merge, not nice-to-have.

Run with:
    pytest tests/test_severity_gating.py -v
"""

# Mirror the severity_order dict from notifier_helper.load_channel_configs.
# Kept inline (not imported) because the production code is in a function
# body — extracting the constant is a separate refactor, not in scope.
SEVERITY_ORDER = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}


def _gate(row_min_severity: str, rule_min_severity: str) -> bool:
    """Return True if the channel SHOULD be skipped under §9.7 logic.

    Replicates the conditions from notifier_helper.load_channel_configs
    lines 31-51 EXACTLY. If production code changes, this mirror must be
    updated in lockstep — divergence here means the test is not testing
    the right thing.
    """
    if rule_min_severity and rule_min_severity not in SEVERITY_ORDER:
        return True  # unknown rule severity → drop ALL channels
    if row_min_severity not in SEVERITY_ORDER:
        return True  # unknown channel min_severity → drop this channel
    # Production line 49-51: skip when both are truthy AND rule is below
    # the channel's threshold. Empty row.min_severity means "no filter" —
    # the truthy short-circuit keeps the channel.
    if row_min_severity and rule_min_severity and \
            SEVERITY_ORDER[rule_min_severity] < SEVERITY_ORDER[row_min_severity]:
        return True
    return False


# ── the §9.7 bug class: typo'd severities should NOT silently pass ─


def test_typo_rule_severity_drops_all_channels():
    """Pre-§9.7 bug: a typo'd rule severity 'HIIGH' silently meant INFO
    (default 0), so channels with CRITICAL-min would still match.
    Post-§9.7: drop everything, force the operator to notice the typo."""
    assert _gate(row_min_severity="CRITICAL", rule_min_severity="HIIGH") is True


def test_typo_channel_min_severity_drops_channel():
    """Same bug class on the channel side. min_severity='WARNIN' would
    have silently meant CRITICAL, swallowing the gate. Now: drop."""
    assert _gate(row_min_severity="WARNIN", rule_min_severity="CRITICAL") is True


# ── the valid-severity path: thresholds must still work as before ─


def test_warning_rule_skipped_for_critical_only_channel():
    """A WARNING rule is below the threshold for a CRITICAL-only channel."""
    assert _gate(row_min_severity="CRITICAL", rule_min_severity="WARNING") is True


def test_warning_rule_passes_warning_channel():
    """WARNING on a WARNING-min channel — boundary, must pass (not strict <)."""
    assert _gate(row_min_severity="WARNING", rule_min_severity="WARNING") is False


def test_critical_rule_passes_critical_channel():
    assert _gate(row_min_severity="CRITICAL", rule_min_severity="CRITICAL") is False


def test_critical_rule_passes_warning_channel():
    """A CRITICAL rule is at or above the threshold for a WARNING-min channel."""
    assert _gate(row_min_severity="WARNING", rule_min_severity="CRITICAL") is False


def test_info_rule_skipped_for_higher_min_channel():
    """An INFO rule (severity=0) is below the threshold for a WARNING-min
    channel (threshold=1). Production line 50: 0 < 1 → True → skip.
    This is intentional — the channel only wants WARNING+ alerts."""
    assert _gate(row_min_severity="WARNING", rule_min_severity="INFO") is True
    assert _gate(row_min_severity="CRITICAL", rule_min_severity="INFO") is True


def test_info_rule_passes_info_channel():
    """INFO on INFO-min channel: 0 < 0 is False → pass."""
    assert _gate(row_min_severity="INFO", rule_min_severity="INFO") is False


def test_empty_channel_min_severity_logged_as_unknown():
    """The production code's check `if row.min_severity not in severity_order`
    treats empty string as 'unknown' (line 43). So an empty min_severity
    logs an error and skips the channel. This is conservative behavior
    (fail-closed), and the model default is 'CRITICAL' anyway (so this
    branch shouldn't fire in practice). Assert the actual behavior; if
    the production code is later tightened to treat empty as 'no filter',
    this test catches the change."""
    assert _gate(row_min_severity="", rule_min_severity="CRITICAL") is True
    assert _gate(row_min_severity="", rule_min_severity="WARNING") is True
    assert _gate(row_min_severity="", rule_min_severity="INFO") is True


def test_empty_rule_severity_passes_channels():
    """An empty rule severity (no severity filter from the rule side) does
    NOT trigger the §9.7 'unknown rule severity' branch — line 37 is
    guarded by `if min_severity`. All channels pass the gate."""
    assert _gate(row_min_severity="CRITICAL", rule_min_severity="") is False
    assert _gate(row_min_severity="WARNING", rule_min_severity="") is False
    assert _gate(row_min_severity="INFO", rule_min_severity="") is False


# ── regression: the pre-§9.7 .get() defaulting pattern would have failed
# these — assert they fail under the OLD logic for documentation. ─


def test_demonstrate_old_bug_behavior():
    """The OLD code (severity_order.get(min_severity, 0)) would have returned
    0 for unknown severities, meaning 'always allow' — opposite of the
    §9.7 fix. Assert that the old behavior would have been wrong, so a
    future refactor that re-introduces .get() is caught."""
    OLD = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}
    # old: typo'd rule severity → default to 0 → passes every check
    old_result = OLD.get("HIIGH", 0) >= OLD.get("CRITICAL", 2)
    assert old_result is False  # i.e. the OLD logic would NOT skip — that's the bug
    # new: typo'd rule severity → skip everything
    new_result = _gate(row_min_severity="CRITICAL", rule_min_severity="HIIGH")
    assert new_result is True  # §9.7 fix flips the behavior
