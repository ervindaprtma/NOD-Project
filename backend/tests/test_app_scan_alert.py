"""Application Traffic Scan alert (appid_flow, metric_field 'app.<path>.<metric>').

Covers the "monitor all apps, fire on any over threshold" feature: the phase-1 per-app scan
query, the phase-2 who/where/whom enrichment query, the reducer (any-exceeds + driver + min_mbps
floor), group-cache-key isolation from per-path appid rules, and preview/ctx parity.
No live cluster — query-body asserts via a FakeClient.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.opensearch import traffic_flow as tf
from app.services import alert_engine as ae


class FakeClient:
    def __init__(self, response):
        self._response = response
        self.body = None

    async def search(self, index, body, **kw):
        self.body = body
        return self._response


def _rule(metric_field="app.internet.download_mbps", condition=">", threshold=50.0, **af):
    return SimpleNamespace(
        data_source="appid_flow", metric_field=metric_field, site_name="Site_FGT-DC",
        evaluation_window_minutes=5, condition=condition, threshold_value=threshold,
        appid_filter=af or None,
    )


# ── metric parsing / descriptor ──

def test_scan_metric_parse():
    assert ae._scan_metric_parse("app.internet.download_mbps") == ("internet", "internet", "download_mbps", "Download")
    # _wan is the all-paths aggregate → no path filter
    assert ae._scan_metric_parse("app.wan.total_mbps") == ("wan", None, "total_mbps", "Total")


def test_scan_descriptor_only_for_app_prefix():
    assert ae._scan_descriptor_for(_rule()) == {"path": "internet", "top_n": 10, "excludes": {}}
    # per-path (non-scan) appid rule → not scan
    assert ae._scan_descriptor_for(_rule(metric_field="traffic.internet.download_mbps")) is None


def test_scan_descriptor_carries_top_n_and_excludes():
    d = ae._scan_descriptor_for(_rule(top_n=25, min_mbps=2.0, app_not="Windows-Update"))
    assert d["top_n"] == 25 and d["excludes"] == {"app_not": "Windows-Update"}  # min_mbps not a query knob


# ── group-cache-key isolation (the collision guard) ──

def test_group_key_scan_isolated_from_perpath():
    scan_key = ae._group_key(_rule())                                        # app.internet.download_mbps
    perpath_key = ae._group_key(_rule(metric_field="traffic.internet.download_mbps"))
    assert scan_key != perpath_key
    assert scan_key[3][0] == "scan"           # marked slot → never shares the per-path dict result
    # two scan rules of the same shape still share ONE query
    assert ae._group_key(_rule()) == ae._group_key(_rule())


# ── reducer ──

def test_extract_app_scan_any_exceeds_and_driver():
    apps = [{"app": "YouTube", "download_mbps": 87.3}, {"app": "Zoom", "download_mbps": 61.0},
            {"app": "DNS", "download_mbps": 0.2}]
    r = _rule(threshold=50.0, min_mbps=1.0)
    v = ae._extract_app_scan(r, apps)
    assert v == 87.3                                    # driver = worst offender
    assert [a["app"] for a in r._scan_apps] == ["YouTube", "Zoom"]   # DNS floored out


def test_extract_app_scan_none_breach_returns_closest():
    apps = [{"app": "A", "download_mbps": 12.0}, {"app": "B", "download_mbps": 3.0}]
    r = _rule(threshold=50.0)
    v = ae._extract_app_scan(r, apps)
    assert v == 12.0 and r._scan_apps == []             # closest-to-threshold, nothing breaching


def test_extract_app_scan_non_list_is_safe():
    r = _rule()
    assert ae._extract_app_scan(r, None) == 0.0 and r._scan_apps == []


# ── phase-1 scan query ──

def test_app_scan_query_terms_on_app_and_path_and_excludes():
    resp = {"aggregations": {"by_app": {"buckets": [
        {"key": "YouTube", "upload_bytes": {"value": 1_000_000}, "download_bytes": {"value": 9_000_000}},
    ]}}}
    c = FakeClient(resp)
    out = asyncio.run(tf.appid_flow_app_scan(client=c, gte_ms=0, lte_ms=1000, path="internet",
                                             top_n=10, app_not="Windows-Update"))
    q = c.body["query"]["bool"]
    assert {"term": {"flow.traffic.path": "internet"}} in q["filter"]
    assert c.body["aggs"]["by_app"]["terms"]["field"] == "flow.application.name"
    assert any("Windows-Update" in str(m) for m in q.get("must_not", []))     # exclude twin honored
    assert out[0]["app"] == "YouTube" and round(out[0]["download_mbps"], 1) == 72.0  # 9MB*8/1s/1e6


# ── phase-2 enrichment query ──

def test_app_detail_internet_egress_zone_filtered_and_dims():
    resp = {"aggregations": {"by_app": {"buckets": [{
        "key": "YouTube",
        "upload_bytes": {"value": 0}, "download_bytes": {"value": 11_200_000_000},
        "src_ips": {"buckets": [{"key": "192.168.1.200", "upload_bytes": {"value": 0}, "download_bytes": {"value": 8_100_000_000}}]},
        "dst_orgs": {"buckets": [{"key": "Google LLC"}, {"key": "Fastly, Inc."}]},
        "egress": {"ifaces": {"buckets": [{"key": "WAN-LinkNet"}]}},
    }]}}}
    c = FakeClient(resp)
    out = asyncio.run(tf.appid_flow_app_detail(client=c, gte_ms=0, lte_ms=1000, path="internet",
                                               apps=["YouTube"], internet_path=True))
    sub = c.body["aggs"]["by_app"]["aggs"]
    assert sub["src_ips"]["terms"]["field"] == "flow.client.ip.addr"
    assert sub["dst_orgs"]["terms"]["field"] == "flow.server.as.org"        # never client.as.org
    # internet egress is scoped to out-zone=internet so the WAN link is named, not the LAN VLAN
    assert sub["egress"]["filter"] == {"term": {"flow.out.netif.sec.zone.name": "internet"}}
    d = out["YouTube"]
    assert d["egress"] == ["WAN-LinkNet"]
    assert d["dst_orgs"] == ["Google LLC", "Fastly, Inc."]
    assert d["src_ips"][0]["ip"] == "192.168.1.200"
    assert d["total_bytes"] == 11_200_000_000


def test_app_detail_noninternet_egress_plain_terms():
    resp = {"aggregations": {"by_app": {"buckets": []}}}
    c = FakeClient(resp)
    asyncio.run(tf.appid_flow_app_detail(client=c, gte_ms=0, lte_ms=1, path="inter-site",
                                         apps=["X"], internet_path=False))
    assert c.body["aggs"]["by_app"]["aggs"]["egress"]["terms"]["field"] == "flow.out.netif.alias"


def test_app_detail_empty_apps_no_query():
    c = FakeClient({"aggregations": {}})
    out = asyncio.run(tf.appid_flow_app_detail(client=c, apps=[]))
    assert out == {} and c.body is None                # never issued a query for zero apps


# ── preview/ctx parity ──

def test_scan_ctx_keys_in_sample_render_ctx():
    ctx = ae.sample_render_ctx()
    for k in ("scan_apps_text", "scan_volume_text", "scan_src_ips_text",
              "scan_egress_text", "scan_dst_orgs_text", "scan_apps",
              "scan_recovered_mbps", "scan_drop_mbps", "scan_recovered_known"):
        assert k in ctx


def test_scan_recovery_was_now_lookup_and_drop():
    """Resolve was→now: the fired app's current speed is looked up from the tick's per-app stash;
    drop = was − now; a fall out of the top-N reads unknown (no fake 0)."""
    rule = _rule(metric_field="app.internet.download_mbps", threshold=30.0)
    rule._scan_apps = [{"app": "ISAKMP", "download_mbps": 33.6}]   # fired at 33.6
    fired = rule._scan_apps[0]["app"]

    rule._scan_now = {"DNS": 8.0, "ISAKMP": 6.2}                    # still ranked
    now = (getattr(rule, "_scan_now", None) or {}).get(fired)
    assert now == 6.2
    assert round(max(0.0, 33.6 - now), 1) == 27.4                   # drop

    rule._scan_now = {"DNS": 8.0}                                   # fell out of the top-N
    assert (rule._scan_now or {}).get(fired) is None


def test_sample_ctx_scan_metric_label_matches_fire():
    # preview must show the SAME direction label the fire path uses ("Download", not the
    # dotted-path title-case) — else a scan template that previews clean renders differently on fire.
    ctx = ae.sample_render_ctx(metric_field="app.internet.download_mbps", data_source="appid_flow")
    assert ctx["metric_label"] == "Download" and ctx["metric_unit"] == "Mbps"


def test_scan_resolve_value_and_target_from_snapshot():
    """Clean-value parity at resolve: the recovery reports the app that FIRED and the value it
    fired at, from the snapshot — NOT this cycle's unrelated top-app number. Mirrors the exact
    override _advance_state_machine applies when rehydrating a scan rule's fire snapshot."""
    rule = _rule(metric_field="app.internet.download_mbps")
    snap = {"scan_apps": [{"app": "YouTube", "download_mbps": 87.3},
                          {"app": "Zoom", "download_mbps": 61.0}]}
    resolved_value = 12.3          # this cycle's max app (e.g. DNS) — must be overridden
    rule._target_name = "not-an-app-filter-label"
    if snap.get("scan_apps") is not None:
        rule._scan_apps = snap["scan_apps"] or []
        if rule._scan_apps:
            _pt, _pv, _mk, _dir = ae._scan_metric_parse(rule.metric_field)
            resolved_value = float(rule._scan_apps[0].get(_mk) or resolved_value)
            rule._target_name = rule._scan_apps[0].get("app") or rule._target_name
    assert resolved_value == 87.3 and rule._target_name == "YouTube"
