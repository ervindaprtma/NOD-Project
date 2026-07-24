"""
The Interface Bandwidth summary reports Avg / Peak / Last across the range.

Avg previously reported the last non-null sample, so a quiet final bucket made
a busy link look idle (WAN LinkNet over 1h: 0.63 Mbps shown vs 9.79 actual).
"""
from app.api.interface_stats import InterfaceTimelinePoint, _summarize


def _tl(*vals):
    return [InterfaceTimelinePoint(timestamp=i * 60_000, in_mbps=v, out_mbps=v)
            for i, v in enumerate(vals)]


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
