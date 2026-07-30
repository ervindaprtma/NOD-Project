"""
Composite rules (P3/P5): multi-metric AND/OR. Pure-function + stubbed-cache tests —
no cluster. Guards the two things that make composite usable: the flat extractor must
resolve interface_stats/device_uptime clauses via their target_key, and the AND/OR
(notify_when) combination must fire correctly.
"""
import asyncio
from types import SimpleNamespace

from app.services.alert_engine import _evaluate_composite_rule, _extract_per_rule_value_flat


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
        ("interface_stats", "Site_FGT-DC", 15): {"39": {"throughput_mbps": {"avg": 30.0, "max": 37.7}}},
        ("sdwan_sla", "Site_FGT-DC", 15): {"avg_latency": [13.5], "status": [0.0]},
    }
    mv_all, fire_all = asyncio.run(_evaluate_composite_rule(_rule("all"), cache))
    mv_any, fire_any = asyncio.run(_evaluate_composite_rule(_rule("any"), cache))
    assert fire_all is False, "AND must not fire when only one clause breaches"
    assert fire_any is True, "OR must fire when any clause breaches"
    # reported metric value = max across clauses (throughput dominates here)
    assert mv_all == 37.7 and mv_any == 37.7


def test_composite_holds_when_a_clause_cannot_read():
    """If any clause's group read is None (degraded/missing), the whole rule holds
    (None, False) — never evaluate a multi-metric rule on partial data."""
    cache = {
        ("interface_stats", "Site_FGT-DC", 15): {"39": {"throughput_mbps": {"avg": 30.0, "max": 37.7}}},
        ("sdwan_sla", "Site_FGT-DC", 15): None,  # degraded
    }
    mv, fire = asyncio.run(_evaluate_composite_rule(_rule("any"), cache))
    assert mv is None and fire is False


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
