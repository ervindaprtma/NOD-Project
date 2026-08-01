"""VPN capacity metrics — the extractor that turns the usage summary
{count, total_bytes, top_user_bytes} into the value a rule evaluates. No cluster."""
from app.services.alert_engine import _extract_vpn_usage

_SUMMARY = {"count": 7, "total_bytes": 5_500_000_000, "top_user_bytes": 2_100_000_000}


def test_volume_metrics_read_bytes():
    assert _extract_vpn_usage("total_bytes", _SUMMARY) == 5_500_000_000.0
    assert _extract_vpn_usage("top_user_bytes", _SUMMARY) == 2_100_000_000.0


def test_count_fields_and_legacy_names_read_count():
    # the legacy count field names (and anything not a volume metric) → the user/tunnel count
    assert _extract_vpn_usage("active_sslvpn_users_count", _SUMMARY) == 7.0
    assert _extract_vpn_usage("active_ipsec_users_count", _SUMMARY) == 7.0


def test_backward_compat_bare_number():
    # an old-style count-only group result (a bare int) still works
    assert _extract_vpn_usage("active_ipsec_users_count", 3) == 3.0
    assert _extract_vpn_usage("total_bytes", None) == 0.0


def test_missing_keys_default_to_zero():
    assert _extract_vpn_usage("total_bytes", {"count": 2}) == 0.0
    assert _extract_vpn_usage("active_sslvpn_users_count", {}) == 0.0
