"""
Guards the device_uptime availability math.

Every case here is a real failure mode measured against live OpenSearch, not a
hypothetical: the newly-onboarded switches really did read ~5% available, a
device really was renamed mid-series, and CKR-R01-TOR-A really is ~34 days from
a 32-bit counter wrap. Pure functions only — no cluster needed.
"""
from app.opensearch.device_uptime import (
    WRAP_GUARD,
    bucket_expected_polls,
    build_query,
    compute_availability,
    find_collector_gaps,
    format_uptime_long,
    format_uptime_short,
    resolve_range,
    scan_reboots,
    shape_result,
)

MINUTE = 60_000
HOUR = 60 * MINUTE


def _ticks(seconds: float) -> float:
    """seconds -> SNMP TimeTicks (hundredths of a second)."""
    return seconds * 100


# ── uptime formatting ────────────────────────────────────────────


def test_uptime_long_reads_like_the_spec():
    """The reviewer asked to see '7 days 10 hours 5 minutes' verbatim."""
    assert format_uptime_long(7 * 86_400 + 10 * 3_600 + 5 * 60) == "7 days 10 hours 5 minutes"


def test_uptime_long_drops_zero_units_and_gets_plurals_right():
    assert format_uptime_long(86_400 + 3_600) == "1 day 1 hour"
    assert format_uptime_long(2 * 86_400) == "2 days"
    assert format_uptime_long(30) == "less than a minute"


def test_uptime_short_is_compact():
    assert format_uptime_short(135 * 86_400 + 22 * 3_600) == "135d 22h"
    assert format_uptime_short(90 * 60) == "1h 30m"


# ── availability (§9) ────────────────────────────────────────────


def test_availability_is_uptime_over_window():
    # Full window, no downtime -> 100%.
    assert compute_availability(0, HOUR, first_seen_ms=0) == 100.0
    # 6 min of real downtime in a 1h window -> 90%.
    assert compute_availability(0, HOUR, first_seen_ms=0, device_down_seconds=360) == 90.0


def test_poll_gap_without_a_reset_is_not_downtime():
    """
    S1/S2: a Telegraf timeout or packet loss cannot dent availability while the
    counter keeps climbing — a missed poll is not a device outage. With no reset
    and no trailing silence, `device_down` is 0, so a device up the whole window
    reads 100% no matter how many polls Telegraf dropped.
    """
    assert compute_availability(0, 24 * HOUR, first_seen_ms=0, device_down_seconds=0) == 100.0


def test_newly_onboarded_device_is_not_reported_as_an_outage():
    """
    A device onboarded partway through the window is only accountable from its
    first sample. Without the clamp the live switches read 4.99%/6.72% while being
    100% healthy.
    """
    # 24h window, but the device only appeared 1h before the end -> judged over 1h.
    assert compute_availability(
        window_start_ms=0, window_end_ms=24 * HOUR, first_seen_ms=23 * HOUR,
    ) == 100.0             # not ~4%


def test_no_history_is_unknown_not_a_fabricated_number():
    """
    An empty effective window is "unknown", never a fabricated number — the
    reference doc's own §5 Case B returns 45% where truth is 99.996%.
    """
    assert compute_availability(0, HOUR, first_seen_ms=HOUR) is None


# ── reboot detection (§4) + wrap guard (§8) ──────────────────────


def test_counter_decrease_is_a_reboot_and_the_gap_is_the_downtime():
    points = [
        {"ts_ms": 0, "ticks": _ticks(10_000)},
        {"ts_ms": 5 * MINUTE, "ticks": _ticks(60)},      # rebooted: counter reset
    ]
    events, downtime = scan_reboots(points)
    assert [e["note"] for e in events] == [None]
    assert events[0]["at_ms"] == 5 * MINUTE
    assert downtime == 300                               # the polling gap, conservatively


def test_rising_counter_is_never_a_reboot():
    points = [{"ts_ms": i * MINUTE, "ticks": _ticks(1_000 + i * 60)} for i in range(5)]
    events, downtime = scan_reboots(points)
    assert events == [] and downtime == 0


def test_wrap_near_2_32_is_not_counted_as_a_reboot():
    """
    CKR-R01-TOR-A is at ~462d and wraps at ~497d (≈27 Aug 2026). Without this
    guard the wrap renders a phantom outage on a device that never rebooted.
    """
    points = [
        {"ts_ms": 0, "ticks": WRAP_GUARD + 1_000},
        {"ts_ms": MINUTE, "ticks": _ticks(30)},          # looks like a reset, is a wrap
    ]
    events, downtime = scan_reboots(points)
    assert [e["note"] for e in events] == ["possible counter wrap"]
    assert downtime == 0                                 # contributes no downtime


def test_empty_buckets_are_skipped_not_read_as_a_reset():
    """min_doc_count:0 emits None ticks; a gap must not masquerade as a reboot."""
    points = [
        {"ts_ms": 0, "ticks": _ticks(1_000)},
        {"ts_ms": MINUTE, "ticks": None},
        {"ts_ms": 2 * MINUTE, "ticks": _ticks(1_120)},
    ]
    events, downtime = scan_reboots(points)
    assert events == [] and downtime == 0


# ── collector gap vs device gap (§2d) ────────────────────────────


def test_all_devices_silent_together_is_the_collector():
    polls = {
        "10.0.0.1": {0: 120, 1 * HOUR: 0, 2 * HOUR: 120},
        "10.0.0.2": {0: 120, 1 * HOUR: 0, 2 * HOUR: 120},
    }
    first_seen = {"10.0.0.1": 0, "10.0.0.2": 0}
    gaps = find_collector_gaps(polls, first_seen, bucket_ms=HOUR)
    assert len(gaps) == 1
    assert gaps[0]["start_ms"] == HOUR and gaps[0]["duration_seconds"] == 3_600


def test_one_device_silent_while_peers_report_is_that_device():
    """The whole point of the discriminator: don't blame Telegraf for one dead box."""
    polls = {
        "10.0.0.1": {0: 120, 1 * HOUR: 0, 2 * HOUR: 120},
        "10.0.0.2": {0: 120, 1 * HOUR: 120, 2 * HOUR: 120},
    }
    first_seen = {"10.0.0.1": 0, "10.0.0.2": 0}
    assert find_collector_gaps(polls, first_seen, bucket_ms=HOUR) == []


def test_a_device_is_not_expected_before_it_was_onboarded():
    """A bucket before a device's first sample must not count as its silence."""
    polls = {
        "10.0.0.1": {0: 120, 1 * HOUR: 120},
        "10.0.0.2": {0: 0, 1 * HOUR: 120},      # onboarded at 1h
    }
    first_seen = {"10.0.0.1": 0, "10.0.0.2": 1 * HOUR}
    assert find_collector_gaps(polls, first_seen, bucket_ms=HOUR) == []


def test_single_device_site_never_blames_the_collector():
    """With one device the signal is ambiguous, so it stays a device-level gap."""
    polls = {"10.0.0.1": {0: 120, 1 * HOUR: 0}}
    first_seen = {"10.0.0.1": 0}
    assert find_collector_gaps(polls, first_seen, bucket_ms=HOUR) == []


def test_consecutive_gap_buckets_merge_into_one_range():
    polls = {
        "10.0.0.1": {0: 120, 1 * HOUR: 0, 2 * HOUR: 0, 3 * HOUR: 120},
        "10.0.0.2": {0: 120, 1 * HOUR: 0, 2 * HOUR: 0, 3 * HOUR: 120},
    }
    first_seen = {"10.0.0.1": 0, "10.0.0.2": 0}
    gaps = find_collector_gaps(polls, first_seen, bucket_ms=HOUR)
    assert len(gaps) == 1 and gaps[0]["duration_seconds"] == 7_200


# ── query shape ──────────────────────────────────────────────────


def test_query_filters_on_site_tag_and_keeps_empty_buckets():
    body = build_query("dc", 0, HOUR, "15m")
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"measurement_name.keyword": "device_uptime"}} in filters   # Q-06
    assert {"term": {"tag.site.keyword": "dc"}} in filters
    assert any("range" in f and "@timestamp" in f["range"] for f in filters)    # Q-01

    series = body["aggs"]["by_device"]["aggs"]["series"]["date_histogram"]
    # calendar_interval rejects multiples like 15m with a 400 (the R-03 regression).
    assert "fixed_interval" in series and "calendar_interval" not in series
    # Empty buckets ARE the outage signal — dropping them breaks gap detection.
    assert series["min_doc_count"] == 0
    # Identity is the IP: hostnames get renamed (FG_DRC_LA), IPs do not.
    assert body["aggs"]["by_device"]["terms"]["field"] == "tag.source.keyword"
    assert body["aggs"]["by_device"]["terms"]["size"] == 200                    # Q-02


def test_histogram_spans_the_whole_window_not_just_where_data_exists():
    """
    Without extended_bounds, empty buckets are only emitted BETWEEN the first and
    last doc — so a collector that dies mid-window produces no buckets at all for
    the dead stretch, and the outage is invisible to both the chart and gap
    detection. Measured: a 24h window returned 9 buckets instead of ~96.
    """
    body = build_query("dc", 1_000, 86_401_000, "15m")
    hist = body["aggs"]["by_device"]["aggs"]["series"]["date_histogram"]
    assert hist["extended_bounds"] == {"min": 1_000, "max": 86_401_000}


def test_partial_edge_buckets_expect_only_the_time_they_cover():
    """
    The trailing bucket is still in progress and the leading one starts mid-way;
    dividing either by a FULL interval invents a dip on a healthy device
    (measured 41-70% at the edges).
    """
    bucket = 3_600_000  # 1h buckets
    # A bucket wholly inside the window expects the full hour of polls.
    assert bucket_expected_polls(0, bucket, 0, 3_600_000, None) == 120
    # Window ends 15 min into the bucket -> only 15 min of expectation.
    assert bucket_expected_polls(0, bucket, 0, 900_000, None) == 30
    # Device only started reporting 15 min before the bucket ends.
    assert bucket_expected_polls(0, bucket, 0, 3_600_000, 2_700_000) == 30
    # Bucket entirely before the device existed -> expects nothing.
    assert bucket_expected_polls(0, bucket, 0, 3_600_000, 3_600_000) == 0


def test_bucket_before_a_device_existed_is_unknown_not_zero_percent():
    """A device cannot be 0% available in a window it did not yet exist in."""
    window_end = 2 * HOUR
    aggs = {"by_device": {"buckets": [
        _agg("10.0.0.5", "late-sw", _ticks(9_000),
             [(0, 0, None), (HOUR, 120, _ticks(9_000))], HOUR, window_end, 120),
    ]}}
    out = shape_result(aggs, 0, window_end, bucket_seconds=3_600, site_tag="dc", window="24h")
    series = out["devices"][0]["series"]
    assert series[0]["availability_pct"] is None      # before first_seen -> unknown
    assert series[1]["availability_pct"] == 100.0     # once reporting -> healthy


def test_explicit_range_overrides_the_named_window():
    """Drag-to-zoom passes gte/lte; it must win over `window`."""
    start, end, _ = resolve_range("365d", 1_000, 5_000, now_ms=9_999_999)
    assert (start, end) == (1_000, 5_000)

    start, end, interval = resolve_range("24h", None, None, now_ms=86_400_000)
    assert end - start == 86_400_000 and interval == "15m"


# ── end-to-end shaping ───────────────────────────────────────────


def _agg(key, hostname, ticks, series, first, last, polls):
    return {
        "key": key,
        "latest": {"hits": {"hits": [{"_source": {
            "device_uptime": {"sys_uptime": ticks},
            "tag": {"hostname": hostname, "vendor": "h3c", "site": "dc"},
        }}]}},
        "polls": {"value": polls},
        "first": {"value": first},
        "last": {"value": last},
        "series": {"buckets": [
            {"key": ts, "doc_count": n, "max_uptime": {"value": t}} for ts, n, t in series
        ]},
    }


def test_shape_result_reports_per_device_not_a_fleet_average():
    window_end = 2 * HOUR
    aggs = {"by_device": {"buckets": [
        _agg("10.0.0.1", "fw-a", _ticks(200_000),
             [(0, 120, _ticks(196_400)), (HOUR, 120, _ticks(200_000))], 0, window_end, 240),
        # onboarded at 1h — partial history, must still read healthy
        _agg("10.0.0.2", "sw-b", _ticks(9_000),
             [(0, 0, None), (HOUR, 120, _ticks(9_000))], HOUR, window_end, 120),
    ]}}
    out = shape_result(aggs, 0, window_end, bucket_seconds=3_600, site_tag="dc", window="24h")

    by_host = {d["hostname"]: d for d in out["devices"]}
    assert by_host["fw-a"]["availability_pct"] == 100.0
    assert by_host["sw-b"]["availability_pct"] == 100.0      # not 50% — clamped to first_seen
    assert by_host["sw-b"]["partial_history"] is True
    assert by_host["fw-a"]["uptime_human_long"] == "2 days 7 hours 33 minutes"

    # Q1: no synthetic fleet percentage — counts plus the most recently booted device.
    summary = out["summary"]
    assert "fleet_availability_pct" not in summary
    assert summary["devices_total"] == 2
    # "Lowest" ranks by uptime, not availability: sw-b (9000s) < fw-a (200000s).
    assert summary["lowest_uptime_device"]["hostname"] == "sw-b"
    assert summary["lowest_uptime_device"]["uptime_seconds"] == 9_000
    assert summary["history_sufficient"] is True


def test_shape_result_flags_wrap_risk_without_calling_it_a_reboot():
    window_end = 2 * HOUR
    aggs = {"by_device": {"buckets": [
        _agg("10.0.0.9", "tor-a", WRAP_GUARD + 5_000,
             [(0, 120, WRAP_GUARD + 1_000), (HOUR, 120, _ticks(30))], 0, window_end, 240),
    ]}}
    out = shape_result(aggs, 0, window_end, bucket_seconds=3_600, site_tag="dc", window="24h")
    device = out["devices"][0]
    assert device["wrap_risk"] is True
    assert device["reboot_count"] == 0                       # a wrap is not an outage
    assert device["total_downtime_seconds"] == 0
    assert out["summary"]["reboots_total"] == 0


# ── the field-failure scenarios (counter-based availability) ──────
# Availability trusts the sys_uptime counter: a Telegraf failure cannot dent it —
# only a reset (reboot) or a device that goes silent and never returns is downtime.


def _series(gte, bucket_ms, n, doc_of, tick_of):
    return [(gte + i * bucket_ms, doc_of(i), tick_of(i)) for i in range(n)]


def test_s1_telegraf_timeout_does_not_dent_a_counter_proven_uptime():
    """S1: device up 300d, Telegraf blacks out site-wide for 2h mid-window. The
    counter climbs across the gap -> no reset -> 100%, for BOTH devices."""
    bm, n, gte, lte = 900_000, 96, 0, 24 * HOUR
    base = _ticks(300 * 86_400)
    silent = set(range(40, 48))                              # 2h, both devices dark

    def dev(ip, host):
        ser = _series(gte, bm, n,
                      doc_of=lambda i: 0 if i in silent else 30,
                      tick_of=lambda i: None if i in silent else base + i * 90_000)
        return _agg(ip, host, base + (n - 1) * 90_000, ser,
                    gte + 15_000, lte - 30_000, (n - len(silent)) * 30)

    out = shape_result({"by_device": {"buckets": [dev("10.0.0.11", "a"), dev("10.0.0.12", "b")]}},
                       gte, lte, bucket_seconds=900, site_tag="dc", window="24h")
    for d in out["devices"]:
        assert d["availability_pct"] == 100.0
        assert d["reboot_count"] == 0
    assert out["summary"]["collector_gap_seconds"] == 7_200   # still surfaced for the chart


def test_s2_single_device_packet_loss_is_not_downtime():
    """S2: 20% packet loss to one device (peer healthy). The counter proves the
    device stayed up, so the dropped polls are not charged as downtime -> 100%."""
    bm, n, gte, lte = 900_000, 96, 0, 24 * HOUR
    base = _ticks(300 * 86_400)

    def dev(ip, host, keep):
        ser = _series(gte, bm, n, doc_of=lambda i: keep, tick_of=lambda i: base + i * 90_000)
        return _agg(ip, host, base + (n - 1) * 90_000, ser, gte + 15_000, lte - 30_000, n * keep)

    out = shape_result({"by_device": {"buckets": [dev("10.0.0.21", "lossy", 24),
                                                  dev("10.0.0.22", "peer", 30)]}},
                       gte, lte, bucket_seconds=900, site_tag="dc", window="24h")
    lossy = next(d for d in out["devices"] if d["hostname"] == "lossy")
    assert lossy["availability_pct"] == 100.0                 # was ~80% under poll-success
    assert lossy["reboot_count"] == 0
    assert lossy["excluded_collector_seconds"] == 0           # not a collector gap; still 100%


def test_s3_reboot_gap_is_real_downtime():
    """S3: stable Telegraf, device up ~50m, 5m of missing data only during a
    reboot. The reset IS counted -> availability drops, status=rebooted."""
    bm, n, gte, lte = 60_000, 60, 0, HOUR
    prior = _ticks(5 * 86_400)                               # up 5 days before the reboot
    gap = set(range(10, 15))                                 # 5 one-minute buckets = reboot outage

    def tick(i):
        if i in gap:
            return None
        return prior + i * 6_000 if i < 10 else (i - 10) * 6_000

    dev = _agg("10.0.0.31", "short50", (n - 1 - 10) * 6_000,
               _series(gte, bm, n, doc_of=lambda i: 0 if i in gap else 2, tick_of=tick),
               gte + 15_000, lte - 15_000, (n - len(gap)) * 2)
    base = _ticks(300 * 86_400)
    peer = _agg("10.0.0.32", "peer", base + (n - 1) * 6_000,
                _series(gte, bm, n, doc_of=lambda i: 2, tick_of=lambda i: base + i * 6_000),
                gte + 15_000, lte - 15_000, n * 2)
    out = shape_result({"by_device": {"buckets": [dev, peer]}},
                       gte, lte, bucket_seconds=60, site_tag="dc", window="1h")
    d = next(x for x in out["devices"] if x["hostname"] == "short50")
    assert d["reboot_count"] == 1
    assert d["status"] == "rebooted"
    assert d["total_downtime_seconds"] >= 300               # the ~5m reboot window
    assert 89.0 <= d["availability_pct"] <= 92.0            # the outage COUNTS


def test_device_that_goes_silent_and_never_returns_is_charged_as_downtime():
    """A trailing silence the counter can't vouch for (peer still up, so not a
    collector gap) is real downtime until window end — not a free 100%."""
    bm, n, gte, lte = 900_000, 96, 0, 24 * HOUR
    base = _ticks(300 * 86_400)
    dead_after = 88                                          # silent for the last 8 buckets (2h)
    dead = _agg("10.0.0.41", "dead", base + (dead_after - 1) * 90_000,
                _series(gte, bm, n,
                        doc_of=lambda i: 0 if i >= dead_after else 30,
                        tick_of=lambda i: None if i >= dead_after else base + i * 90_000),
                gte + 15_000, gte + (dead_after - 1) * bm, dead_after * 30)
    peer = _agg("10.0.0.42", "peer", base + (n - 1) * 90_000,
                _series(gte, bm, n, doc_of=lambda i: 30, tick_of=lambda i: base + i * 90_000),
                gte + 15_000, lte - 30_000, n * 30)
    out = shape_result({"by_device": {"buckets": [dead, peer]}},
                       gte, lte, bucket_seconds=900, site_tag="dc", window="24h")
    d = next(x for x in out["devices"] if x["hostname"] == "dead")
    assert d["status"] == "not_reporting"
    assert d["availability_pct"] < 95.0                     # ~2h of the 24h charged as down
    assert d["total_downtime_seconds"] >= 2 * 3_600 - 900


def test_short_silence_under_the_threshold_is_not_a_false_issue():
    """A gap shorter than STALE_AFTER_MS (300s) is normal poll jitter (a dropped SNMP
    scrape or two), NOT an outage: the device must read `up` at 100%, never
    `not_reporting`. Guards the exact false alarm the 300s threshold exists to prevent —
    at the old 60s threshold this same device would have been flagged down."""
    bm, n, gte, lte = 900_000, 96, 0, 24 * HOUR
    base = _ticks(100 * 86_400)
    last = lte - 200_000                                    # last sample 200s before end (< 300s)
    dev = _agg("10.0.0.51", "jittery", base + (n - 1) * 900,
               _series(gte, bm, n, doc_of=lambda i: 30, tick_of=lambda i: base + i * 900),
               gte + 15_000, last, n * 30)
    d = shape_result({"by_device": {"buckets": [dev]}},
                     gte, lte, bucket_seconds=900, site_tag="dc", window="24h")["devices"][0]
    assert d["status"] == "up"                              # not "not_reporting"
    assert d["availability_pct"] == 100.0                   # a dropped poll is not downtime
    assert d["total_downtime_seconds"] == 0


def test_reboot_in_final_bucket_uses_last_ticks_not_max():
    """Regression: a reboot in the FINAL bucket was hidden by max-per-bucket (the bucket's
    max is the pre-reboot high and there's no next bucket to show the drop), so a recent
    reboot vanished from coarse 7d/30d views. scan_reboots must use `last_ticks` (end-of-
    bucket value): the reboot then shows as last dropping in its own bucket."""
    # bucket 0/1: healthy & rising; bucket 2 (final): rebooted mid-bucket → max still the
    # pre-reboot high, but last_ticks is the post-reboot low.
    points = [
        {"ts_ms": 0,          "ticks": _ticks(10_000), "last_ticks": _ticks(10_000)},
        {"ts_ms": 6 * MINUTE, "ticks": _ticks(10_360), "last_ticks": _ticks(10_360)},
        {"ts_ms": 12 * MINUTE, "ticks": _ticks(10_500), "last_ticks": _ticks(120)},  # max hides it
    ]
    events, _ = scan_reboots(points)
    assert len(events) == 1 and events[0]["note"] is None, "final-bucket reboot must be caught"

    # And the old max-only signal would have missed it (max is monotonic 10000→10360→10500).
    max_only = [{"ts_ms": p["ts_ms"], "ticks": p["ticks"]} for p in points]
    assert len(scan_reboots(max_only)[0]) == 0
