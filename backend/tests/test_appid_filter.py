"""appid_flow app/protocol/port scoping — the pure helpers that decide query grouping and
the notification label. No cluster: just the filter → signature → label logic."""
from types import SimpleNamespace

from app.services.alert_engine import _appid_filter_for, _appid_sig, _appid_filter_label


def _rule(ds, appid_filter):
    return SimpleNamespace(data_source=ds, appid_filter=appid_filter)


def test_filter_only_applies_to_appid_and_drops_empties():
    assert _appid_filter_for(_rule("appid_flow", {"app": "YouTube", "protocol": "", "port": None})) == {"app": "YouTube"}
    assert _appid_filter_for(_rule("appid_flow", {})) is None
    assert _appid_filter_for(_rule("appid_flow", None)) is None
    # a filter on a non-appid rule is ignored (target_key is that source's selector, not this)
    assert _appid_filter_for(_rule("interface_stats", {"app": "x"})) is None


def test_signature_separates_distinct_filters_but_shares_unfiltered():
    # unfiltered rules → same (None) signature → share one grouped query
    assert _appid_sig(None) is None
    assert _appid_sig({}) is None
    # distinct filters → distinct signatures → separate queries
    a = _appid_sig({"app": "YouTube"})
    b = _appid_sig({"app": "Netflix"})
    c = _appid_sig({"app": "YouTube", "port": 443})
    assert a != b and a != c and b != c
    # order-independent, hashable (usable as a dict key)
    assert _appid_sig({"app": "YouTube", "port": 443}) == _appid_sig({"port": 443, "app": "YouTube"})
    _ = {a: 1, b: 2}  # must not raise


def test_label_is_human_readable_or_none():
    assert _appid_filter_label(None) is None
    assert _appid_filter_label({}) is None
    assert _appid_filter_label({"app": "YouTube"}) == "app=YouTube"
    assert _appid_filter_label({"app": "YouTube", "protocol": "TCP", "port": 443}) == "app=YouTube, proto=TCP, port=443"
    # port 0 is a real value, not "absent"
    assert _appid_filter_label({"port": 0}) == "port=0"
