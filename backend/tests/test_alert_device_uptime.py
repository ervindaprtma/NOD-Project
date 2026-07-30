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
