"""
Guards the device-availability route contract.

The endpoint must never 500 on a bad OpenSearch read — the UI relies on an empty
payload plus meta.degraded to say "unknown", not "no devices". Validation is
checked here too so a typo'd site returns a named error rather than an empty page.
"""
import asyncio

import app.api.device_uptime as route
from app.api.device_uptime import (
    DeviceAvailabilityResponse,
    DeviceAvailabilitySummary,
    get_device_availability,
)


def _call(**kwargs):
    """Drive the coroutine with stdlib asyncio — no pytest-asyncio needed for one file."""
    params = {"site_name": "Site_FGT-DC", "window": "24h",
              "gte_ms": None, "lte_ms": None, "current_user": object()}
    params.update(kwargs)
    return asyncio.run(get_device_availability(**params))


def test_unknown_site_is_a_named_validation_error():
    resp = _call(site_name="Site_Does_Not_Exist")
    assert resp.success is False
    assert resp.error.code == "VALIDATION_ERROR"
    assert "Site_FGT-DC" in resp.error.message      # tells the caller what IS valid


def test_unknown_window_is_rejected():
    resp = _call(window="7 years")
    assert resp.success is False
    assert resp.error.code == "VALIDATION_ERROR"


def test_query_failure_returns_empty_payload_not_a_500(monkeypatch):
    """A dead cluster must degrade to 'no data', never crash the page."""
    async def boom(**_):
        raise RuntimeError("opensearch unreachable")

    monkeypatch.setattr(route.du_qb, "device_availability", boom)
    resp = _call()

    assert resp.success is True                     # 200, not 500
    assert resp.data.devices == []
    assert resp.data.summary.devices_total == 0
    assert resp.meta.degraded is True               # "unknown", not "no devices"
    assert any("query_failed" in reason for reason in resp.meta.partial_errors)


def test_successful_payload_is_passed_through(monkeypatch):
    async def fake(**_):
        return {
            "summary": {"window": "24h", "window_seconds": 86_400, "site": "dc",
                        "devices_total": 1, "devices_reporting": 1,
                        "devices_partial_history": 0, "devices_with_reboots": 0,
                        "lowest_uptime_device": {"hostname": "fw-a", "uptime_seconds": 1.0,
                                                 "uptime_human_short": "0m"},
                        "reboots_total": 0, "collector_gap_seconds": 0,
                        "collector_gaps": [], "history_start_ms": 1,
                        "history_sufficient": True},
            "devices": [{
                "device_key": "10.0.0.1", "hostname": "fw-a", "vendor": "fortigate",
                "site": "dc", "status": "up", "sys_uptime_ticks": 100,
                "uptime_seconds": 1.0, "uptime_human_long": "less than a minute",
                "uptime_human_short": "0m", "boot_time_ms": 0,
                "first_seen_ms": 1, "last_seen_ms": 2, "partial_history": False,
                "wrap_risk": False, "availability_pct": 99.9, "expected_polls": 100,
                "successful_polls": 99, "excluded_collector_seconds": 0,
                "reboots": [], "reboot_count": 0, "total_downtime_seconds": 0,
                "series": [],
            }],
        }

    monkeypatch.setattr(route.du_qb, "device_availability", fake)
    resp = _call()

    assert resp.success is True
    assert resp.meta.degraded is None               # clean read
    assert resp.data.devices[0].availability_pct == 99.9
    assert resp.data.summary.lowest_uptime_device["hostname"] == "fw-a"


def test_empty_response_has_no_fabricated_numbers():
    """The fallback payload must not imply a healthy fleet."""
    empty = DeviceAvailabilityResponse(summary=DeviceAvailabilitySummary(), devices=[])
    assert empty.summary.devices_total == 0
    assert empty.summary.lowest_uptime_device is None
    assert empty.summary.history_sufficient is False


def test_availability_is_nullable_not_zero_by_default():
    """
    None means 'unknown'. Defaulting to 0.0 would render a device as 0% available
    when we simply have no basis to judge it — the Case-B mistake in schema form.
    """
    from app.api.device_uptime import DeviceAvailabilityItem
    item = DeviceAvailabilityItem(device_key="10.0.0.1")
    assert item.availability_pct is None
