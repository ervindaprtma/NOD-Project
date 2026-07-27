"""Guards VPN session reconstruction (sslvpn.sessionize) — pure, no cluster.

Implements the agreed model: gap >5min splits a reconnect (rule 2), a run whose
last sample is older than the gap is 'ended' (rule 3), a run over 9h is split
(rule 4); the daily 00:00 boundary (rule 1) is enforced inside sessionize. Buckets
are keyed by (username, device).
"""
from app.opensearch.sslvpn import sessionize

MIN = 60_000
T0 = 1_785_000_000_000
U = ("u", "FGT-DC")   # (username, device) key


def test_single_continuous_run_is_one_session():
    buckets = {U: [T0 + i * MIN for i in range(10)]}        # 10 back-to-back minutes
    out = sessionize(buckets, now_ms=T0 + 10 * MIN)
    assert len(out) == 1
    assert out[0]["username"] == "u" and out[0]["device"] == "FGT-DC"
    assert out[0]["session_started"] == T0
    assert out[0]["last_seen"] == T0 + 9 * MIN + MIN        # last bucket + interval
    assert out[0]["status"] == "active"


def test_gap_over_five_minutes_splits_a_reconnect():
    run1 = [T0 + i * MIN for i in range(5)]                 # minutes 0..4
    run2 = [T0 + (10 + i) * MIN for i in range(5)]          # minutes 10..14 (6-min gap)
    out = sessionize({U: run1 + run2}, now_ms=T0 + 20 * MIN)
    assert len(out) == 2
    assert out[0]["session_started"] == T0
    assert out[1]["session_started"] == T0 + 10 * MIN


def test_gap_within_five_minutes_stays_one_session():
    # 4-min gap (minute4 -> minute8) is under the threshold -> not a new session.
    stamps = [T0, T0 + MIN, T0 + 2 * MIN, T0 + 3 * MIN, T0 + 4 * MIN, T0 + 8 * MIN, T0 + 9 * MIN]
    assert len(sessionize({U: stamps}, now_ms=T0 + 10 * MIN)) == 1


def test_status_active_only_within_the_gap_of_now():
    active = sessionize({U: [T0]}, now_ms=T0 + 2 * MIN)     # last_seen T0+1m, now T0+2m
    assert active[0]["status"] == "active"
    ended = sessionize({U: [T0]}, now_ms=T0 + 10 * MIN)     # silent >5min -> closed
    assert ended[0]["status"] == "ended"


def test_bytes_are_the_run_max_cumulative_counter():
    byb = {U: {T0: (100, 10), T0 + MIN: (300, 30)}}
    out = sessionize({U: [T0, T0 + MIN]}, now_ms=T0 + 2 * MIN, bytes_by_bucket=byb)
    assert (out[0]["bytes_in"], out[0]["bytes_out"]) == (300, 30)


def test_same_user_on_two_devices_stays_separate():
    out = sessionize({("u", "FGT-DC"): [T0], ("u", "FGT-DRC"): [T0]}, now_ms=T0 + 2 * MIN)
    assert sorted((s["username"], s["device"]) for s in out) == [("u", "FGT-DC"), ("u", "FGT-DRC")]


def test_multiple_users_and_per_user_reconnects():
    out = sessionize({("a", "d"): [T0], ("b", "d"): [T0, T0 + 10 * MIN]}, now_ms=T0 + 11 * MIN)
    assert sorted(s["username"] for s in out) == ["a", "b", "b"]  # b reconnected -> 2 rows


def test_session_never_spans_midnight_wib():
    # 2026-07-27 00:00 WIB. Two samples 3 min apart (under the gap threshold) but on
    # opposite sides of midnight must still split into two sessions (rule 1).
    wib_mid = 1_785_085_200_000
    out = sessionize({U: [wib_mid - 2 * MIN, wib_mid + MIN]}, now_ms=wib_mid + 10 * MIN)
    assert len(out) == 2
    assert out[0]["session_started"] == wib_mid - 2 * MIN
    assert out[1]["session_started"] == wib_mid + MIN


def test_exact_start_from_session_duration():
    # §3.5b: with the device's session age, the login second is exact — login =
    # last_sample_end − duration — not the first observed bucket edge.
    buckets = [T0, T0 + MIN, T0 + 2 * MIN]
    durs = {U: {T0: 180, T0 + MIN: 240, T0 + 2 * MIN: 300}}  # 3m,4m,5m of uptime
    out = sessionize({U: buckets}, now_ms=T0 + 3 * MIN, dur_by_bucket=durs)
    assert len(out) == 1
    # login = (last_bucket T0+2m, +1m interval) − 300s = T0 − 120s
    assert out[0]["session_started"] == (T0 + 3 * MIN) - 300_000


def test_session_capped_at_nine_hours():
    # 9.5h of samples 4 min apart (under the 5-min gap, same day) is one continuous
    # run — but SSL VPN sessions max out at 9h, so it must split into two rows.
    base = 1_785_106_800_000  # 2026-07-27 06:00 WIB (well clear of midnight)
    stamps = [base + i * 4 * MIN for i in range(int(9.5 * 60 / 4) + 1)]
    out = sessionize({U: stamps}, now_ms=base + 11 * 3600 * 1000)
    assert len(out) == 2
    assert out[0]["last_seen"] - out[0]["session_started"] <= 9 * 3600 * 1000 + MIN


def test_duration_drop_splits_a_fast_reconnect_without_a_gap():
    # Consecutive 60s buckets, no >5min gap, same day — but session_duration RESETS,
    # so the tunnel was rebuilt: must split (the gap rule alone cannot see this).
    buckets = [T0, T0 + MIN, T0 + 2 * MIN, T0 + 3 * MIN]
    durs = {U: {T0: 600, T0 + MIN: 660, T0 + 2 * MIN: 30, T0 + 3 * MIN: 90}}
    out = sessionize({U: buckets}, now_ms=T0 + 4 * MIN, dur_by_bucket=durs)
    assert len(out) == 2
    assert out[1]["session_started"] > out[0]["session_started"]
