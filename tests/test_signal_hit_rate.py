"""signal_hit_rate: BUY should beat the benchmark, SELL should lag it, HOLD is ignored."""

from unittest.mock import patch

from src.analytics.signal_recorder import signal_hit_rate


def test_hit_rate_counts_direction_and_skips_hold():
    rows = [("BUY", 0.02), ("STRONG_BUY", -0.01), ("SELL", -0.03), ("HOLD", 0.5)]
    with patch("src.db.repository.get_matured_signal_outcomes", return_value=rows):
        r = signal_hit_rate()
    assert r["20d"] == {"n": 3, "hits": 2, "hit_rate": 0.6667}


def test_hit_rate_none_without_sample():
    with patch("src.db.repository.get_matured_signal_outcomes", return_value=[]):
        assert signal_hit_rate()["5d"]["hit_rate"] is None
