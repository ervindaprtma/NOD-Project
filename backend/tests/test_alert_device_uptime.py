"""
Track AL — guards the device_uptime alert extractor (§11).

Pure-function tests (no DB / no cluster): the extractor is where a wrong number
turns an infra event into a false alert, so its edge cases are pinned here.
"""
from app.services.alert_engine import _extract_device_uptime


def _dev(key, status="up", avail=100.0, reboots=0, uptime=1000, wrap=False):
    return {
        "device_key": key, "hostname": key, "status": status,
        "availability_pct": avail, "reboot_count": reboots,
        "uptime_seconds": uptime, "wrap_risk": wrap,
    }


def _result(devices):
    return {"summary": {}, "devices": devices}


def test_collector_gap_is_site_level_and_fires_on_gap():
    up = _result([_dev("a"), _dev("b")])
    assert _extract_device_uptime("collector_gap", None, up) == 0.0

    storm = _result([_dev("a", status="collector_gap"), _dev("b", status="collector_gap")])
    assert _extract_device_uptime("collector_gap", None, storm) == 1.0


def test_storm_suppresses_per_device_not_reporting():
    """During a Telegraf storm every device reads status 'collector_gap', NOT
    'not_reporting' — so per-device down rules stay quiet and only the one
    site-level collector_gap rule fires (§11.3 suppression, done in the data layer)."""
    storm = _result([_dev("a", status="collector_gap"), _dev("b", status="collector_gap")])
    assert _extract_device_uptime("not_reporting", None, storm) == 0.0
    assert _extract_device_uptime("not_reporting", "a", storm) == 0.0


def test_not_reporting_any_device_and_targeted():
    r = _result([_dev("a"), _dev("b", status="not_reporting")])
    assert _extract_device_uptime("not_reporting", None, r) == 1.0    # any device down
    assert _extract_device_uptime("not_reporting", "a", r) == 0.0     # this one is up
    assert _extract_device_uptime("not_reporting", "b", r) == 1.0     # this one is down


def test_absent_named_device_holds_not_zero():
    """A rule pinned to a device that isn't in the window must evaluate on nothing
    (None → engine holds), never a fabricated 0 that reads as a false all-clear."""
    r = _result([_dev("a")])
    assert _extract_device_uptime("not_reporting", "9.9.9.9", r) is None
    assert _extract_device_uptime("availability_pct", "9.9.9.9", r) is None


def test_availability_unknown_holds():
    """availability_pct None (insufficient history) must never breach an SLA — hold."""
    r = _result([_dev("a", avail=None)])
    assert _extract_device_uptime("availability_pct", "a", r) is None
    # min across devices skips the unknown one
    r2 = _result([_dev("a", avail=None), _dev("b", avail=98.0)])
    assert _extract_device_uptime("availability_pct", None, r2) == 98.0


def test_reduce_worst_case_across_devices():
    r = _result([_dev("a", avail=100.0, reboots=0, uptime=9000),
                 _dev("b", avail=95.5, reboots=2, uptime=120)])
    assert _extract_device_uptime("availability_pct", None, r) == 95.5   # min
    assert _extract_device_uptime("reboot_count", None, r) == 2.0        # max
    assert _extract_device_uptime("uptime_seconds", None, r) == 120.0    # min


def test_wrap_risk():
    assert _extract_device_uptime("wrap_risk", None, _result([_dev("a")])) == 0.0
    assert _extract_device_uptime("wrap_risk", None, _result([_dev("a", wrap=True)])) == 1.0


def test_no_devices_holds():
    assert _extract_device_uptime("not_reporting", None, _result([])) is None
    assert _extract_device_uptime("collector_gap", None, _result([])) == 0.0  # site: no gap


def test_device_availability_template_matches_field_catalog():
    """The 'Device Availability Uptime' template must select a data_source + metric
    that the engine actually honors, with an aggregation/condition the catalog allows —
    otherwise the template hands non-technical users a rule that can never fire.
    Guards drift between SEED_TEMPLATES and SEED_FIELD_CATALOG."""
    from app.services.template_seeder import SEED_FIELD_CATALOG, SEED_TEMPLATES

    tmpl = next(t for t in SEED_TEMPLATES if t["name"] == "Device Availability Uptime")
    lf = tmpl["locked_fields"]
    row = next(
        c for c in SEED_FIELD_CATALOG
        if c["data_source"] == lf["data_source"] and c["field_key"] == lf["metric_field"]
    )  # raises if the (data_source, metric) pair isn't a real catalog field
    assert lf["data_source"] == "device_uptime" and lf["metric_field"] == "availability_pct"
    assert lf["aggregation"] in row["valid_aggregations"]
    assert lf["condition"] in row["valid_conditions"]


def test_interface_spike_template_uses_peak_aggregation_and_matches_catalog():
    """Interface Bandwidth Spike must use the PEAK (max) aggregation — that IS the spike
    logic (_extract_interface_stats returns the window max) — and select a data_source +
    metric the catalog honors with a valid condition."""
    from app.services.template_seeder import SEED_FIELD_CATALOG, SEED_TEMPLATES

    tmpl = next(t for t in SEED_TEMPLATES if t["name"] == "Interface Bandwidth Spike")
    lf = tmpl["locked_fields"]
    assert lf["data_source"] == "interface_stats"
    assert lf["aggregation"] == "max", "spike detection = window peak, not average"
    # catalog field_key carries the full "iface.<base>" form, same as metric_field
    row = next(
        c for c in SEED_FIELD_CATALOG
        if c["data_source"] == lf["data_source"] and c["field_key"] == lf["metric_field"]
    )
    assert lf["aggregation"] in row["valid_aggregations"]
    assert lf["condition"] in row["valid_conditions"]


def test_application_throughput_template_uses_per_path_mbps_and_matches_catalog():
    """Application Throughput Spike must point at a per-path Mbps metric the catalog + engine
    actually honor. The legacy 'app_total_bytes' used to map to _wan.total_bytes (5-min cumulative
    bytes), which rendered as '0' for sparse flows and looked like an outage — and which the
    newer catalog no longer lists as a field, so a free-text legacy rule returned 0 from the
    extractor for callers that did key-by-key catalog lookups. Guard keeps it on traffic.*_mbps."""
    from app.services.template_seeder import SEED_FIELD_CATALOG, SEED_TEMPLATES

    tmpl = next(t for t in SEED_TEMPLATES if t["name"] == "Application Throughput Spike")
    lf = tmpl["locked_fields"]
    assert lf["data_source"] == "appid_flow"
    assert lf["metric_field"].startswith("traffic.") and lf["metric_field"].endswith("_mbps"), (
        "Must be a per-path Mbps key so the extractor returns a real Mbps value to test/dry-run."
    )
    row = next(
        c for c in SEED_FIELD_CATALOG
        if c["data_source"] == lf["data_source"] and c["field_key"] == lf["metric_field"]
    )  # raises if not in catalog
    assert lf["condition"] in row["valid_conditions"]
    # unit must be Mbps — a bytes-based template mis-thresholds by 1e6
    assert row.get("unit") == "Mbps"


def test_retired_templates_are_gone_and_registered():
    """VPN Tunnel Down / WAN Congestion are retired: absent from the seed list and listed
    for deletion so already-seeded DBs drop them too."""
    from app.services.template_seeder import RETIRED_TEMPLATE_NAMES, SEED_TEMPLATES

    seed_names = {t["name"] for t in SEED_TEMPLATES}
    assert {"VPN Tunnel Down", "WAN Congestion"} <= RETIRED_TEMPLATE_NAMES
    assert not (RETIRED_TEMPLATE_NAMES & seed_names), "a retired template must not also be re-seeded"


def test_seeded_notification_templates_render_for_both_events():
    """Every seeded notification template must render for firing AND resolved with the
    fire-time/preview context — else the preview 422s and the alert falls to fallback.
    Regression guard: the HTML templates branch on `event`, so `event` must be in ctx."""
    from app.services.alert_engine import _render_template
    from app.services.template_seeder import SEED_NOTIFICATION_TEMPLATES

    def ctx(event):
        rc = {"name": "T", "severity": "WARNING", "site_name": "Site_FGT-DC",
              "metric_field": "cpu.usage", "condition": ">", "threshold_value": 80.0}
        return {"rule": rc, **rc, "threshold": rc["threshold_value"], "metric_value": 95.5,
                "data_source": "device_uptime", "aggregation": "min",
                "fired_at": "01 Jan 2026 12:00:00 WIB", "event": event,
                "event_label": "Resolved" if event == "resolved" else "Firing"}

    for t in SEED_NOTIFICATION_TEMPLATES:
        for event in ("firing", "resolved"):
            for field in ("subject_template", "body_template", "line_template"):
                txt = t.get(field)
                if txt:
                    _render_template(txt, ctx(event))  # raises → test fails


def test_sdwan_link_status_parses_and_is_in_catalog():
    """SD-WAN link Up/Down: metric_field status_linkN parses to base 'status' (which
    sla_summary now returns), and the catalog exposes it so the UI can offer Down/Up."""
    from app.services.alert_engine import _parse_sdwan_metric_field
    from app.services.template_seeder import SEED_FIELD_CATALOG

    assert _parse_sdwan_metric_field("status_link1") == ("status", 0)
    assert _parse_sdwan_metric_field("status_link2") == ("status", 1)
    cat = {(c["data_source"], c["field_key"]) for c in SEED_FIELD_CATALOG}
    assert ("sdwan_sla", "status_link1") in cat
    assert ("sdwan_sla", "status_link2") in cat


def test_interface_throughput_mbps_metric_exists():
    """The Mbps-based throughput metric backs both the absolute and %-of-link-max modes."""
    from app.services.template_seeder import SEED_FIELD_CATALOG

    row = next(c for c in SEED_FIELD_CATALOG
               if c["data_source"] == "interface_stats" and c["field_key"] == "iface.throughput_mbps")
    assert row["unit"] == "Mbps" and "max" in row["valid_aggregations"]


def test_sslvpn_measurement_mapping():
    """SSL VPN alerts: the operator picks a plain site (Site_FGT-DC), but the data lives
    under the per-site *_SSLVPN measurement. The mapping must add the suffix (idempotently)
    or the cardinality filters on a non-SSLVPN measurement and reads 0 users forever."""
    from app.opensearch.sslvpn import sslvpn_measurement_for_site

    assert sslvpn_measurement_for_site("Site_FGT-DC") == "Site_FGT-DC_SSLVPN"
    assert sslvpn_measurement_for_site("Site_FGT-DRC") == "Site_FGT-DRC_SSLVPN"
    assert sslvpn_measurement_for_site("Site_FGT-DC_SSLVPN") == "Site_FGT-DC_SSLVPN"  # idempotent
    assert sslvpn_measurement_for_site(None) == "Site_FGT-DC_SSLVPN"


def test_ipsec_count_targets_live_telegraf_source():
    """IPsec active-user count must read the live `ipsec_user` measurement in telegraf-index*,
    not the retired dedicated `ipsec-*` index (which went stale and made the '< 2 tunnels'
    CRITICAL rule false-fire on a fabricated 0). Captures the actual index + query it issues."""
    import asyncio
    from app.opensearch import ipsec as ip

    captured = {}

    async def fake_search(client, index, body):
        captured["index"] = index
        captured["body"] = body
        return {"aggregations": {"active_users": {"value": 3}}}

    orig = ip.safe_search
    ip.safe_search = fake_search
    try:
        n = asyncio.run(ip.active_ipsec_users_count(gte_ms=1, lte_ms=2))
    finally:
        ip.safe_search = orig

    assert n == 3
    assert captured["index"] == "telegraf-index*"
    terms = [f for f in captured["body"]["query"]["bool"]["filter"] if "term" in f]
    assert {"term": {"measurement_name.keyword": "ipsec_user"}} in terms


def test_ha_num_active_counts_reporting_members():
    """ha_resource `num_active` = number of HA members currently reporting (len of the
    device list current_device_status returns). It was unhandled by the extractor — only
    ha_member.* was — so it read 0.0 forever and a '< 2 members' rule perma-fired. Both the
    rule-object and flat (composite) extractors must count members, not return 0."""
    from types import SimpleNamespace
    from app.services.alert_engine import _extract_per_rule_value, _extract_per_rule_value_flat

    members = [{"cpu_usage": 3.0}, {"cpu_usage": 5.0}]  # 2 reporting HA members
    rule = SimpleNamespace(id=0, data_source="ha_resource", metric_field="num_active",
                           site_name="Site_FGT-DC", target_key=None, aggregation="avg")
    assert _extract_per_rule_value(rule, members) == 2.0
    assert _extract_per_rule_value_flat("ha_resource", "num_active", members) == 2.0
    # a dropped member shrinks the count below the threshold
    assert _extract_per_rule_value(rule, members[:1]) == 1.0
    assert _extract_per_rule_value(rule, []) == 0.0  # nothing reporting
