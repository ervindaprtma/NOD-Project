"""VPN Session Monitor (kind="session") — the pure diff that turns two active-session
snapshots into connect/disconnect events. No cluster, no DB."""
from app.services.alert_engine import _diff_sessions, _fmt_duration


def _s(remote, active): return {"remote_ip": remote, "active_ip": active, "device": "FG"}


def test_connect_and_disconnect_detected():
    prev = {"alice": {**_s("1.1.1.1", "10.0.0.1"), "started_at": 1000}}
    current = {"alice": _s("1.1.1.1", "10.0.0.1"), "bob": _s("2.2.2.2", "10.0.0.2")}
    events, new_state = _diff_sessions(prev, current, now_ms=5000)
    kinds = {(e, u) for e, u, _ in events}
    assert ("connected", "bob") in kinds
    assert not any(u == "alice" for _, u, _ in events)  # alice stayed → no event


def test_disconnect_carries_original_start_and_ended_now():
    prev = {"alice": {**_s("1.1.1.1", "10.0.0.1"), "started_at": 1000,
                      "bytes_in": 524_000_000, "bytes_out": 88_000_000}}
    events, new_state = _diff_sessions(prev, current={}, now_ms=9000)
    assert len(events) == 1
    ev, user, info = events[0]
    assert ev == "disconnected" and user == "alice"
    assert info["started_at"] == 1000 and info["ended_at"] == 9000  # true session span
    # the session's consumed volume rides along on disconnect (last-known counters)
    assert info["bytes_in"] == 524_000_000 and info["bytes_out"] == 88_000_000
    assert new_state == {}


def test_stay_connected_preserves_original_started_at():
    prev = {"alice": {**_s("1.1.1.1", "10.0.0.1"), "started_at": 1000}}
    current = {"alice": _s("1.1.1.1", "10.0.0.1")}
    events, new_state = _diff_sessions(prev, current, now_ms=8000)
    assert events == []
    assert new_state["alice"]["started_at"] == 1000  # NOT reset to 8000


def test_new_user_started_at_is_now():
    events, new_state = _diff_sessions({}, {"bob": _s("2.2.2.2", "10.0.0.2")}, now_ms=4200)
    assert events[0][0] == "connected"
    assert new_state["bob"]["started_at"] == 4200


def test_duration_formatting():
    assert _fmt_duration(1000, 1000 + 23 * 60 * 1000) == "23m 0s"
    assert _fmt_duration(1000, 6000) == "5s"
    assert _fmt_duration(1000, 1000 + 2 * 3600 * 1000) == "2h 0m"
    assert _fmt_duration(None, 5000) == "—"        # missing start → dash
    assert _fmt_duration(9000, 5000) == "—"        # end before start → dash
