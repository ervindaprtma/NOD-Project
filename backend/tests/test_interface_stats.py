"""
The Interface Bandwidth summary must average across the selected range.

It previously reported the last non-null sample, so a quiet final bucket made
a busy link look idle (WAN LinkNet over 1h: 0.63 Mbps shown vs 9.79 actual).
"""
from app.api.interface_stats import InterfaceTimelinePoint, _mean_value


def _tl(*vals):
    return [InterfaceTimelinePoint(timestamp=i * 60_000, in_mbps=v, out_mbps=v)
            for i, v in enumerate(vals)]


def test_mean_not_last_sample():
    assert _mean_value(_tl(10.0, 20.0, 30.0, 0.0), "in_mbps") == 15.0


def test_gaps_are_skipped_not_counted_as_zero():
    """Counter resets and the seeded first bucket emit None — they must not drag the mean down."""
    assert _mean_value(_tl(10.0, None, 20.0), "in_mbps") == 15.0


def test_no_data_is_none_not_zero():
    assert _mean_value([], "in_mbps") is None
    assert _mean_value(_tl(None, None), "in_mbps") is None
