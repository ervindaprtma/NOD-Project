"""
Composite rules (P3/P5): multi-metric AND/OR. Pure-function + stubbed-cache tests —
no cluster. Guards the two things that make composite usable: the flat extractor must
resolve interface_stats/device_uptime clauses via their target_key, and the AND/OR
(notify_when) combination must fire correctly.
"""
import asyncio
from types import SimpleNamespace

from app.services.alert_engine import (
    _clause_severity, _evaluate_composite_rule, _extract_per_rule_value_flat,
    _metric_label_unit,
)


def test_metric_label_unit_names_the_fired_metric():
    """The mislabel bug: a latency-driven SD-WAN alert rendered 'Packet Loss: 142% (%)'
    because the ctx exposed no metric name/unit. Each SD-WAN clause field maps to its own
    label+unit so a template stops hardcoding one."""
    assert _metric_label_unit("avg_packet_loss") == ("Packet Loss", "%")
    assert _metric_label_unit("max_latency") == ("Latency", "ms")
    assert _metric_label_unit("avg_jitter") == ("Jitter", "ms")
    # unknown field → present-but-empty unit (StrictUndefined-safe), prettified label
    label, unit = _metric_label_unit("some_new_field")
    assert unit == "" and label == "Some New Field"


def test_resolve_driver_picks_clause_nearest_its_own_limit():
    """Recovery bug: a recovered packet-loss-5% clause (now 2) must keep driving the
    message, not lose to a latency clause with a bigger raw value (26.53, limit 100) —
    which made the RECOVERED text read 'Packet Loss: 26.53% (limit > 100)'. Driver is
    chosen by per-clause severity (value vs its OWN threshold), unit-normalized."""
    loss = {"value": 2.0, "breached": False, "metric_field": "avg_packet_loss",
            "condition": ">", "threshold_value": 5.0}
    latency = {"value": 26.53, "breached": False, "metric_field": "avg_latency",
               "condition": ">", "threshold_value": 100.0}
    # 2/5 = 0.40  >  26.53/100 = 0.265 → packet loss is closer to its limit
    assert _clause_severity(loss) > _clause_severity(latency)
    driver = max([loss, latency], key=_clause_severity)  # mirrors the resolve path
    assert driver["metric_field"] == "avg_packet_loss" and driver["threshold_value"] == 5.0


def test_clause_severity_is_direction_aware():
    # "<" clause: lower value is worse (e.g. throughput floor). "==" is binary.
    below = {"value": 2.0, "threshold_value": 10.0, "condition": "<", "breached": True}
    assert _clause_severity(below) == 5.0            # 10/2
    down = {"value": 1.0, "threshold_value": 1.0, "condition": "==", "breached": True}
    up = {"value": 0.0, "threshold_value": 1.0, "condition": "==", "breached": False}
    assert _clause_severity(down) == 1.0 and _clause_severity(up) == 0.0


def test_flat_extractor_honors_interface_target_key():
    """An interface_stats clause carries the ifIndex in target_key; without honoring it the
    extractor returned 0 (any clause with it could never fire). Peak = max aggregation."""
    group = {
        "39": {"throughput_mbps": {"avg": 20.0, "max": 37.7}, "oper_status": 1.0},
        "3": {"throughput_mbps": {"avg": 1.0, "max": 2.0}, "oper_status": 1.0},
    }
    assert _extract_per_rule_value_flat("interface_stats", "iface.throughput_mbps", group, "39", "max") == 37.7
    assert _extract_per_rule_value_flat("interface_stats", "iface.throughput_mbps", group, "3", "avg") == 1.0
    # missing target_key → 0, never a false fire
    assert _extract_per_rule_value_flat("interface_stats", "iface.throughput_mbps", group, None, "max") == 0.0


def _rule(notify_when):
    # if throughput > 30 (true: 37.7) AND/OR sdwan latency > 999 (false: 13.5)
    return SimpleNamespace(
        clauses=[
            {"data_source": "interface_stats", "metric_field": "iface.throughput_mbps",
             "aggregation": "max", "condition": ">", "threshold_value": 30.0, "target_key": "39"},
            {"data_source": "sdwan_sla", "metric_field": "avg_latency_link1",
             "condition": ">", "threshold_value": 999.0},
        ],
        site_name="Site_FGT-DC", evaluation_window_minutes=15, notify_when=notify_when,
    )


def test_composite_and_or_combination():
    """One clause true, one false: AND (all) must NOT fire, OR (any) must fire. Uses a
    stubbed group_cache so no OpenSearch call happens."""
    cache = {
        ("interface_stats", "Site_FGT-DC", 15, None): {"39": {"throughput_mbps": {"avg": 30.0, "max": 37.7}}},
        ("sdwan_sla", "Site_FGT-DC", 15, None): {"avg_latency": [13.5], "status": [0.0]},
    }
    mv_all, fire_all, drv_all = asyncio.run(_evaluate_composite_rule(_rule("all"), cache))
    mv_any, fire_any, drv_any = asyncio.run(_evaluate_composite_rule(_rule("any"), cache))
    assert fire_all is False, "AND must not fire when only one clause breaches"
    assert fire_any is True, "OR must fire when any clause breaches"
    # reported metric value = the driving (breaching, max) clause — throughput here
    assert mv_all == 37.7 and mv_any == 37.7
    # driver carries the FIRING clause's own threshold, not the top-level mirror — this is
    # what the notification renders as "limit", so it must be the throughput clause's 30.
    assert drv_any["threshold_value"] == 30.0 and drv_any["metric_field"] == "iface.throughput_mbps"


def test_composite_holds_when_a_clause_cannot_read():
    """If any clause's group read is None (degraded/missing), the whole rule holds
    (None, False) — never evaluate a multi-metric rule on partial data."""
    cache = {
        ("interface_stats", "Site_FGT-DC", 15, None): {"39": {"throughput_mbps": {"avg": 30.0, "max": 37.7}}},
        ("sdwan_sla", "Site_FGT-DC", 15, None): None,  # degraded
    }
    mv, fire, drv = asyncio.run(_evaluate_composite_rule(_rule("any"), cache))
    assert mv is None and fire is False and drv is None


def test_schema_accepts_composite_rule():
    """AlertRuleCreate must accept kind=composite + clauses (AlertClause validated)."""
    from app.schemas.alert import AlertRuleCreate

    body = AlertRuleCreate(
        name="WAN busy AND latency bad", severity="WARNING",
        data_source="interface_stats", metric_field="iface.throughput_mbps",
        aggregation="max", condition=">", threshold_value=900.0,
        evaluation_window_minutes=5, sustained_for_minutes=2,
        kind="composite", notify_when="all",
        clauses=[
            {"data_source": "interface_stats", "metric_field": "iface.throughput_mbps",
             "condition": ">", "threshold_value": 900.0, "target_key": "39", "aggregation": "max"},
            {"data_source": "sdwan_sla", "metric_field": "avg_latency_link1",
             "condition": ">", "threshold_value": 80.0},
        ],
    )
    assert body.kind == "composite" and body.notify_when == "all"
    assert len(body.clauses) == 2 and body.clauses[0].target_key == "39"
    assert body.clauses[1].aggregation == "avg"  # default applied


def test_sdwan_full_link_report_renders_firing_and_resolved():
    """SD-WAN composite: one message lists EVERY link × metric (Latency/Jitter/Loss/State),
    marked 🔴 breached / 🟢 ok, on both firing and resolved. The seeded template drives it from
    the `sdwan_links` ctx var built from the per-clause reads."""
    from app.services import template_seeder as ts
    from app.services import alert_engine as ae

    body = next(t for t in ts.SEED_NOTIFICATION_TEMPLATES if t["name"] == "SD-WAN SLA Breach")["body_template"]
    ctx = ae.sample_render_ctx(metric_field="avg_latency", data_source="sdwan_sla", event="firing")

    fired = ae._render_template(body, ctx)
    assert "WAN LDP" in fired and "WAN iForte" in fired          # both links
    assert "Latency" in fired and "Jitter" in fired and "Packet Loss" in fired and "State" in fired
    assert "🔴" in fired and "🟢" in fired                        # mixed breached/ok
    assert "DOWN" in fired                                        # a link State=DOWN renders DOWN

    ctx["event"] = "resolved"
    for lk in ctx["sdwan_links"]:
        for m in lk["metrics"]:
            m["breached"] = False
            if m["is_status"]:
                m["up"] = True
    resolved = ae._render_template(body, ctx)
    assert "RECOVERED" in resolved and "🔴" not in resolved       # all healthy on resolve
    assert "WAN LDP" in resolved and "WAN iForte" in resolved


def test_sdwan_links_in_sample_render_ctx():
    from app.services.alert_engine import sample_render_ctx
    links = sample_render_ctx()["sdwan_links"]
    assert len(links) == 2
    assert [m["name"] for m in links[0]["metrics"]] == ["Latency", "Jitter", "Packet Loss", "State"]
