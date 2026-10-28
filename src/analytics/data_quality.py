"""
One price-data check for everything that ranks, projects or alerts on a stock.

`assess` returns None when a symbol's adjusted closes are usable, otherwise a plain reason. Callers
show the reason next to the excluded symbol, so a bad feed can't quietly distort a result.
Staleness is measured against `as_of`: the freshest data in the batch, or the clock for one symbol.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

STALE_DAYS = 5  # a long weekend plus one exchange holiday is 4 to 5 calendar days
MAX_GAP_DAYS = 10  # between consecutive bars; a suspension or a failed fetch looks like this
GAP_WINDOW = 252  # only the last year of bars is checked for gaps
FLAT_DAYS = 20  # a price that has not moved in this many days is suspended, not stable


def freshest(series_by_symbol: dict[str, pd.Series]) -> date | None:
    """The newest last-bar date across the batch."""
    ends = [s.index[-1].date() for s in series_by_symbol.values() if len(s)]
    return max(ends) if ends else None


def assess(closes: pd.Series, min_bars: int, as_of: date) -> str | None:
    s = closes.dropna()
    if len(s) < min_bars:
        return f"only {len(s)} days of history (needs {min_bars})"
    age = (as_of - s.index[-1].date()).days
    if age > STALE_DAYS:
        return f"last price is {age} days old"
    gaps = s.index[-GAP_WINDOW:].to_series().diff().dt.days.dropna()
    if len(gaps) and gaps.max() > MAX_GAP_DAYS:
        return f"price history has a gap of {int(gaps.max())} days"
    if (s.tail(FLAT_DAYS + 1).diff().dropna() == 0).all():
        return f"price has not moved in the last {FLAT_DAYS} days"
    return None
