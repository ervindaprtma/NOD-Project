"""Device Reboot Monitor (kind="reboot") — the pure diff that turns two device-uptime
snapshots into reboot events. A reboot is an SNMP sys_uptime counter DECREASE. No cluster,
no DB."""
from app.services.alert_engine import _diff_reboots, _fmt_secs


def _d(uptime, hostname="FG_DC-01", downtime=0):
    return {"hostname": hostname, "uptime_seconds": float(uptime), "downtime_seconds": downtime}


def test_uptime_drop_is_a_reboot():
    prev = {"10.80.150.1": _d(3_600_000)}                      # up ~41 days
    current = {"10.80.150.1": _d(240, downtime=150)}           # back up 4 min ago
    events, new_state = _diff_reboots(prev, current, now_ms=5000)
    assert len(events) == 1
    key, info = events[0]
    assert key == "10.80.150.1"
    assert info["prev_uptime_seconds"] == 3_600_000            # pre-reboot uptime carried
    assert info["uptime_seconds"] == 240                       # new uptime carried
    assert info["downtime_seconds"] == 150                     # outage around the reset
    assert info["reboot_at"] == 5000
    assert new_state["10.80.150.1"]["uptime_seconds"] == 240   # state advances


def test_rising_uptime_is_not_a_reboot():
    prev = {"10.80.150.1": _d(1000)}
    current = {"10.80.150.1": _d(1060)}                        # +1 poll, no reset
    events, new_state = _diff_reboots(prev, current, now_ms=9000)
    assert events == []


def test_tick_jitter_within_one_second_does_not_fire():
    # SNMP tick rounding could shave <1s; the +1s guard must ignore it.
    prev = {"10.80.150.1": _d(1000.4)}
    current = {"10.80.150.1": _d(1000.0)}
    events, _ = _diff_reboots(prev, current, now_ms=9000)
    assert events == []


def test_new_device_is_baselined_silently():
    # A device appearing for the first time is onboarding, not a reboot.
    events, new_state = _diff_reboots({}, {"10.80.150.2": _d(500)}, now_ms=4200)
    assert events == []
    assert new_state["10.80.150.2"]["uptime_seconds"] == 500


def test_only_the_rebooted_device_fires():
    prev = {"a": _d(10_000, "A"), "b": _d(20_000, "B")}
    current = {"a": _d(10_060, "A"), "b": _d(30, "B", downtime=60)}   # only B reset
    events, _ = _diff_reboots(prev, current, now_ms=7000)
    assert [k for k, _ in events] == ["b"]


def test_fmt_secs():
    assert _fmt_secs(0) == "—"
    assert _fmt_secs(45) == "45s"
    assert _fmt_secs(150) == "2m 30s"
    assert _fmt_secs(7380) == "2h 3m"
