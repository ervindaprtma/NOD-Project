"""
The Interface Bandwidth summary reports Avg / Peak / Last across the range.

Avg previously reported the last non-null sample, so a quiet final bucket made
a busy link look idle (WAN LinkNet over 1h: 0.63 Mbps shown vs 9.79 actual).
"""
from app.api.interface_stats import (
    InterfaceTimelinePoint, _summarize, _compute_throughput_timeline,
)


def _tl(*vals):
    return [InterfaceTimelinePoint(timestamp=i * 60_000, in_mbps=v, out_mbps=v)
            for i, v in enumerate(vals)]


def _oct_bucket(ts_ms, octets):
    """A raw timeline bucket at ts_ms carrying a cumulative counter value (both directions)."""
    return {"key": ts_ms, "max_in_octets": {"value": octets}, "max_out_octets": {"value": octets}}


def test_avg_is_the_mean_not_the_last_sample():
    assert _summarize(_tl(10.0, 20.0, 30.0, 0.0), "in_mbps") == (15.0, 30.0, 0.0)


def test_last_is_final_bucket_not_peak():
    """The regression that started this: a quiet tail must not become the headline."""
    assert _summarize(_tl(10.0, 90.0, 0.6), "in_mbps") == (33.53, 90.0, 0.6)


def test_gaps_are_skipped_not_counted_as_zero():
    """Counter resets and the seeded first bucket emit None — max() would also raise on them."""
    assert _summarize(_tl(10.0, None, 20.0), "in_mbps") == (15.0, 20.0, 20.0)


def test_no_data_is_none_not_zero():
    assert _summarize([], "in_mbps") == (None, None, None)
    assert _summarize(_tl(None, None), "in_mbps") == (None, None, None)


# ── Data-loss gaps (the 83× spike bug) ──────────────────────────────────────
# min_doc_count=1 drops empty buckets during an outage, so the bucket after the gap
# carried the WHOLE outage's counter delta ÷ one interval → a false spike.

def test_gap_does_not_produce_a_spike():
    """15m interval (900s). Contiguous +112.5MB/900s bucket = 1 Mbps. Then a 20h gap with a
    huge counter jump: the resume bucket must NOT be charged to one interval (was 83× too high)."""
    step = 900_000  # 15m
    octs = 112_500_000  # +112.5 MB over 900s = 1 Mbps
    buckets = [
        _oct_bucket(0, 0),
        _oct_bucket(step, octs),          # +1 Mbps  (baseline consumed the first bucket)
        _oct_bucket(step * 2, 2 * octs),  # +1 Mbps
        # 20h gap: next real bucket jumps ~20 GB but is 20h later, not 15m
        _oct_bucket(step * 2 + 20 * 3_600_000, 2 * octs + 20_000_000_000),
        _oct_bucket(step * 3 + 20 * 3_600_000, 2 * octs + 20_000_000_000 + octs),  # +1 Mbps again
    ]
    pts, tin, tout, gaps = _compute_throughput_timeline(buckets, interval_seconds=900)
    rates = [p.in_mbps for p in pts if p.in_mbps is not None]
    assert max(rates) < 2.0                       # NO 83× spike — every real rate ~1 Mbps
    assert len(gaps) == 1                          # the outage is recorded
    assert gaps[0].start_ms == step * 2 and gaps[0].end_ms == step * 2 + 20 * 3_600_000
    # the gap boundary bucket emits a null point (breaks the line, not a fake value)
    assert any(p.in_mbps is None for p in pts)


def test_gap_delta_excluded_from_volume():
    """The 20 GB counter jump across the gap must not inflate the volume total either."""
    step = 900_000
    buckets = [
        _oct_bucket(0, 0),
        _oct_bucket(step, 900_000_000),                                  # +900 MB (real)
        _oct_bucket(step + 20 * 3_600_000, 900_000_000 + 50_000_000_000),  # +50 GB across a 20h gap
    ]
    _pts, tin, _tout, gaps = _compute_throughput_timeline(buckets, interval_seconds=900)
    assert len(gaps) == 1
    assert tin == 900_000_000                        # only the real, non-gap delta counted


def test_contiguous_buckets_unchanged():
    """No gap → behaves exactly as before (baseline first bucket, then per-interval rates)."""
    step = 900_000
    octs = 112_500_000  # 1 Mbps per 900s
    buckets = [_oct_bucket(i * step, i * octs) for i in range(4)]
    pts, tin, _tout, gaps = _compute_throughput_timeline(buckets, interval_seconds=900)
    assert gaps == []
    assert all(abs(p.in_mbps - 1.0) < 1e-6 for p in pts)   # 3 real points, all 1 Mbps
    assert tin == 3 * octs
