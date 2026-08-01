"""Preview↔fire context parity. The notification-template preview and the alert-template
preview must render any variable the fire-time notifier provides — else a template that
fires fine 422s in preview (or vice-versa). Both previews now build their ctx from
sample_render_ctx; this guards that it covers every documented variable."""
from app.services.alert_engine import _render_template, sample_render_ctx

# every variable a user template may reference, per docs + _flush_batch_notify
DOCUMENTED_VARS = [
    "name", "severity", "site_name", "metric_field", "condition", "threshold_value",
    "threshold", "metric_value", "data_source", "aggregation", "fired_at", "sent_at",
    "event", "event_label",
    "rule.name", "rule.severity", "rule.site_name", "rule.metric_field",
    "rule.condition", "rule.threshold_value",
    "link_max_mbps", "utilization_pct", "threshold_pct",
    "target_name", "target_key",
    "filter_app", "filter_proto", "filter_port", "filter_label",
    "vpn_active_users", "vpn_total_mb", "vpn_top_user_mb", "metric_mb", "threshold_mb",
]


def test_sample_ctx_renders_every_documented_variable_unguarded():
    """Each var must render through the preview ctx WITHOUT an `is defined` guard — that is
    the whole point of parity (a user shouldn't need to guard a variable that always exists
    at fire time)."""
    ctx = sample_render_ctx()
    for var in DOCUMENTED_VARS:
        _render_template("{{ " + var + " }}", ctx)  # raises → parity broken → test fails


def test_volume_vars_present_for_a_byte_metric():
    ctx = sample_render_ctx(metric_field="total_bytes", threshold_value=5_000_000_000, metric_value=5_500_000_000)
    assert _render_template("{{ metric_mb }}/{{ threshold_mb }}", ctx) == "5500.0/5000.0"


def test_resolve_event_flips_label():
    assert sample_render_ctx(event="resolved")["event_label"] == "Resolved"
    assert sample_render_ctx()["event_label"] == "Firing"
