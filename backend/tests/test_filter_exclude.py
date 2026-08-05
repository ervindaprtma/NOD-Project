"""Exclude-value (must_not) filters — query-body asserts, no cluster.

Covers the core of the exclude-filter feature: _base_filters returns a (filter, must_not)
pair, _bool_query omits must_not when empty (byte-identical to before), raw_flows routes
*_not keys to must_not, and pack_excludes parses/renames the wire params.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from starlette.datastructures import QueryParams

from app.opensearch._common import _bool_query
from app.opensearch import traffic_flow as tf
from app.opensearch import traffic_inbound as ti
from app.opensearch import traffic_internal as tn
from app.api._safe import pack_excludes


def test_base_filters_returns_pair_and_routes_excludes():
    filt, excl = tf._base_filters(0, 1, "Site_FGT-DC", app_filter="Zoom",
                                  app_filter_not="Teams", protocol_not="udp", dst_port_not=445)
    # include side still has the positive clause; exclude side has exactly the three twins
    assert any("Zoom" in str(c) for c in filt)
    assert len(excl) == 3
    assert {"term": {"flow.server.l4.port.id": 445}} in excl
    # proto twin is upper-cased like its positive counterpart
    assert {"term": {"l4.proto.name": "UDP"}} in excl


def test_bool_query_omits_must_not_when_empty():
    filt, excl = tf._base_filters(0, 1, "Site_FGT-DC", app_filter="Zoom")
    assert excl == []
    q = _bool_query(filt, excl)
    assert "must_not" not in q["bool"]          # byte-identical to pre-feature query
    q2 = _bool_query(filt, [{"term": {"x": 1}}])
    assert q2["bool"]["must_not"] == [{"term": {"x": 1}}]


def test_inbound_service_exclude_uses_name_or_port_block():
    # _service_filter (name wildcard OR resolved port) reused unchanged in must_not
    filt, excl = ti._base_filters(0, 1, "Site_FGT-DRC", app_filter_not="HTTPS")
    assert len(excl) == 1 and "bool" in excl[0] and "should" in excl[0]["bool"]


def test_internal_service_filter_not_key():
    filt, excl = tn._base_filters(0, 1, "Site_FGT_Office",
                                  service_filter_not="RDP", client_ip_not="10.0.0.5", dst_port_not=3389)
    assert len(excl) == 3
    assert {"term": {"flow.client.ip.addr": "10.0.0.5"}} in excl
    assert {"term": {"flow.server.l4.port.id": 3389}} in excl


def test_raw_flows_builds_must_not_including_iface_should_block():
    captured = {}

    class FakeClient:
        async def search(self, index, body, request_timeout=30):
            captured["body"] = body
            return {"hits": {"hits": [], "total": {"value": 0}},
                    "aggregations": {"session_count": {"value": 0}}}

    filters = {"application": ["Zoom"], "application_not": ["Teams"],
               "egress_interface_not": ["wan1"], "dst_port_not": [445]}
    asyncio.run(tf.raw_flows(client=FakeClient(), gte_ms=0, lte_ms=1, filters=filters))
    mn = captured["body"]["query"]["bool"]["must_not"]
    # include stayed on the filter side
    assert {"terms": {"flow.application.name": ["Zoom"]}} in captured["body"]["query"]["bool"]["filter"]
    # excludes: app term, dst_port term, and the iface name-OR-alias should block
    assert {"terms": {"flow.application.name": ["Teams"]}} in mn
    assert {"terms": {"flow.server.l4.port.id": [445]}} in mn
    assert any("should" in c.get("bool", {}) for c in mn)


def test_pack_excludes_parses_coerces_and_renames():
    req = SimpleNamespace(query_params=QueryParams(
        "app_filter=Zoom&app_filter_not=Teams&dst_port_not=445&protocol_not=&client_ip_not=10.0.0.5"))
    ex = pack_excludes(req, rename={"app_filter_not": "service_filter_not"})
    assert ex["service_filter_not"] == "Teams"       # renamed
    assert ex["dst_port_not"] == 445                 # coerced to int
    assert ex["client_ip_not"] == "10.0.0.5"
    assert "app_filter" not in ex                     # positive param ignored
    assert "protocol_not" not in ex                   # empty dropped


def test_pack_excludes_empty_is_identity():
    req = SimpleNamespace(query_params=QueryParams("site_name=Site_FGT-DC&app_filter=Zoom"))
    assert pack_excludes(req) == {}


def test_appid_alert_summary_routes_excludes_to_must_not():
    captured = {}

    class FakeClient:
        async def search(self, index, body, **kw):
            captured["body"] = body
            return {"aggregations": {"by_path": {"buckets": []}, "up_all": {"value": 0}, "down_all": {"value": 0}}}

    asyncio.run(tf.appid_flow_alert_summary(client=FakeClient(), gte_ms=0, lte_ms=1000,
                app_filter="internet", app_not="Windows-Update", protocol_not="udp", port_not=445))
    b = captured["body"]["query"]["bool"]
    mn = b["must_not"]
    assert any("Windows-Update" in str(c) for c in mn)
    assert {"term": {"l4.proto.name": "UDP"}} in mn          # upper-cased like include
    assert {"term": {"flow.server.l4.port.id": 445}} in mn


def test_appid_label_renders_negations():
    from app.services.alert_engine import _appid_filter_label
    lbl = _appid_filter_label({"app": "YouTube", "app_not": "Teams", "port_not": 445})
    assert "not app=Teams" in lbl and "not port=445" in lbl and "app=YouTube" in lbl


def test_unknown_not_param_never_crashes_the_query():
    """A stale bookmark / URL edit can send an unexpected *_not param through pack_excludes →
    **exclude. The builder must swallow it (no TypeError → no false 'data unavailable')."""
    for qb in (tf, ti, tn):
        filt, excl = qb._base_filters(0, 1, "Site_FGT-DC", bogus_not="x", another_weird_not="y")
        assert isinstance(filt, list) and excl == []   # unknown keys ignored, valid query


def test_appid_ctx_keys_in_sample_render_ctx():
    """Preview-parity hard rule: every fire-time ctx key must exist in sample_render_ctx."""
    from app.services.alert_engine import sample_render_ctx
    ctx = sample_render_ctx()
    for k in ("filter_app_not", "filter_proto_not", "filter_port_not"):
        assert k in ctx
