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
