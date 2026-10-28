"""Shared price-data checks: thin history, stale last bar, gaps, and suspended (flat) prices."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from src.analytics.data_quality import assess, freshest

AS_OF = date(2026, 10, 27)


def _series(n=300, end="2026-10-27", drop=None):
    idx = pd.bdate_range(end=end, periods=n)
    s = pd.Series(100 + np.sin(np.arange(n)), index=idx)  # moves every day
    return s.drop(s.index[drop]) if drop is not None else s


def test_healthy_series_passes():
    assert assess(_series(), min_bars=260, as_of=AS_OF) is None


def test_thin_history_is_reported_with_the_counts():
    assert (
        assess(_series(n=120), min_bars=260, as_of=AS_OF) == "only 120 days of history (needs 260)"
    )


def test_stale_last_price_is_reported_in_days():
    reason = assess(_series(end="2026-10-16"), min_bars=260, as_of=AS_OF)
    assert reason == "last price is 11 days old"


def test_a_long_weekend_or_holiday_is_not_stale():
    assert assess(_series(end="2026-10-22"), min_bars=260, as_of=AS_OF) is None  # 5 days


def test_a_missing_stretch_inside_the_window_is_reported():
    s = _series()
    reason = assess(s.drop(s.index[-120:-100]), min_bars=260, as_of=AS_OF)
    assert reason is not None and reason.startswith("price history has a gap of ")


def test_a_gap_older_than_a_year_is_ignored():
    s = _series(n=600)
    assert assess(s.drop(s.index[10:40]), min_bars=260, as_of=AS_OF) is None


def test_a_price_that_stopped_moving_is_suspended():
    s = _series()
    s.iloc[-25:] = 100.0
    assert assess(s, min_bars=260, as_of=AS_OF) == "price has not moved in the last 20 days"


def test_freshest_is_the_newest_last_bar_across_series():
    a, b = _series(end="2026-10-27"), _series(end="2026-10-20")
    assert freshest({"A": a, "B": b}) == date(2026, 10, 27)
    assert freshest({}) is None
