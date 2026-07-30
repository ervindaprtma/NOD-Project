"""§9.10 test_alert_state_machine.py

Tests the alert engine's state machine:
  INACTIVE -> PENDING -> FIRING -> RESOLVED
with sustained_for_minutes gating and the ALERT_RENOTIFY_INTERVAL_MINUTES
re-notify path.

TODO: full implementation requires a test DB (sqlite in-memory) and
mocks for _run_group_query + AlertLog writes. Skeleton kept for the
§9.10 scaffold so the ponytail plugin can review the test surface.
"""
import pytest

# These imports will work once the test infrastructure (conftest.py with
# an in-memory DB session + mocked OpenSearch) is in place. They are
# referenced as comments so the test reviewer knows what to wire up.

# from app.db.session import AsyncSessionLocal
# from app.services.alert_engine import _advance_state_machine
# from app.db.models import AlertRule, AlertState


@pytest.mark.skip(reason="§9.10 scaffold: needs test DB + OS mocks — see §9.10 in docs/alert_notification_design.md")
def test_inactive_to_pending_on_first_breach():
    """INACTIVE rule with first breach: state must transition to PENDING,
    pending_since must be set, NO AlertLog row written yet."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_pending_to_firing_after_sustained_for():
    """PENDING rule where breach has been sustained longer than
    sustained_for_minutes: state must transition to FIRING, AlertLog row
    written, SSE alert broadcast, notification enqueued."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_firing_renotify_after_interval():
    """FIRING rule still breaching after ALERT_RENOTIFY_INTERVAL_MINUTES:
    last_notified_at updates, SSE alert re-broadcasts, notification re-enqueued."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_firing_to_resolved_on_clear():
    """FIRING rule with breach cleared: state must transition to RESOLVED,
    AlertLog.resolved_at set, SSE resolved broadcast, NO outbound notification
    if notification_mode='peak_only' (per §11.4), YES if 'stateful'."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_pending_to_inactive_on_clear_before_sustained():
    """PENDING rule where breach clears BEFORE sustained_for_minutes elapses:
    state returns to INACTIVE, no AlertLog, no SSE alert."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_disabled_rule_is_not_evaluated():
    """Disabled rules must be skipped by evaluate_all_rules (line 456)."""
    pass


# ── Fire + Resolve notification mechanism (both directions must notify) ──────────
# Source-inspection guards (same style as test_report_queries.py): cheap, run without
# a DB, and pin the exact regressions — a resolve that silently stops notifying, or a
# batch message that can't tell firing from recovery.
import inspect

from app.services import alert_engine


def test_resolve_enqueues_a_recovery_notification():
    """FIRING→RESOLVED must enqueue a 'resolved' notification (not just SSE), and only
    for a rule that actually fired — a PENDING→RESOLVED never alerted, so no all-clear."""
    src = inspect.getsource(alert_engine._advance_state_machine)
    assert 'notify_queue.append((rule, metric_value, "resolved"))' in src, \
        "resolve path must send a recovery notification to the channels"
    assert "was_firing" in src, "recovery must be gated on the rule having actually FIRED"


def test_batch_notify_distinguishes_fire_from_resolve():
    """The batch message must render firing vs recovery differently: `event` in the
    template context, a RESOLVED hardcoded line, and an all-clear subject."""
    src = inspect.getsource(alert_engine._flush_batch_notify)
    assert '"event": event' in src, "templates need `event` to branch fire vs resolve"
    assert "[RESOLVED]" in src, "recovery needs its own hardcoded line"
    assert "Recovery Summary" in src, "an all-resolved batch should read as a recovery"
