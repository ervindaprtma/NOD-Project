"""§9.10 test_batch_notify.py

Tests _flush_batch_notify in alert_engine.py:
- §9.3 fix: batch summary is sent ONLY to channels in
  union(rule.notify_channels for rule in notify_queue) ∩ enabled configs
- Pre-§9.3: would have sent to every enabled channel regardless of which
  rules wanted which channels
- Batched message contains one line per fired rule

TODO: full implementation needs test DB + mocked notifier_helper.send_alert.
Skeleton kept for the §9.10 scaffold.
"""
import pytest


@pytest.mark.skip(reason="§9.10 scaffold: needs test DB + mocked send_alert — see §9.10 in docs/alert_notification_design.md")
def test_batch_only_calls_channels_in_fired_union():
    """Rule with notify_channels=['telegram'] must NOT trigger send_alert
    for whatsapp or smtp, even if those configs are enabled."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_batch_severity_gating():
    """Channel's min_severity gates whether the batch summary is sent to it
    (regression: §9.7 fail-closed applies here too)."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_batch_empty_queue_is_noop():
    """If no rules fired this cycle, _flush_batch_notify returns without
    touching the DB or the network."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_batch_one_line_per_rule():
    """The summary body has exactly len(notify_queue) rule lines, each
    rendered via _render_template if the rule has a template, else the
    hardcoded format."""
    pass


@pytest.mark.skip(reason="§9.10 scaffold")
def test_batch_template_render_failure_falls_back():
    """§9.5: if a rule's body_template fails to render, fall back to the
    hardcoded line — never lose the alert to a template typo."""
    pass


def test_batch_notify_decrypts_channel_config():
    """Regression: the batch dispatch must send the DECRYPTED channel config. It used to
    query NotificationConfig and pass row.config straight through — the bot token is Fernet-
    encrypted at rest, so Telegram got a garbage token and silently rejected every alert
    (fired but never delivered). It must route through load_channel_configs (which decrypts)."""
    import inspect
    from app.services import alert_engine

    src = inspect.getsource(alert_engine._flush_batch_notify)
    assert "load_channel_configs" in src, "batch send must use the decrypting loader"
    assert "ch.config" not in src, "must NOT pass the raw (encrypted) NotificationConfig.config"
