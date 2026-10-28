"""
Portfolio projection, years ahead.

After theanh97/Monte-Carlo-Simulation-for-Stock-Market, which simulates one stock with normally
distributed daily returns. Here the whole portfolio is projected by replaying blocks of whole
past trading days: every holding moves as it did on the same real day, so correlations and fat
tails carry over without being modelled. NIFTY 50 TRI is replayed on the same days, so the chance
of beating it compares like with like. A replay of the chosen history, not a forecast.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import pandas as pd

from src.analytics.data_quality import assess, freshest

BLOCK_DAYS = 20
MIN_COMMON_DAYS = 250
CHUNK = 500
YEAR = 252


def daily_returns(
    holdings: list[dict],
    load_bars: Callable[[str], pd.DataFrame],
    lookback_years: int,
    benchmark: str | None = "NIFTY 50 TRI",
) -> dict:
    """Aligned daily returns of each holding (adjusted closes) over the lookback window.
    `holdings`: [{"symbol", "value"}]. A symbol without enough history is left out, with why."""
    window = lookback_years * YEAR
    loaded, excluded = {}, []
    for h in holdings:
        try:
            loaded[h["symbol"]] = (
                load_bars(h["symbol"])["Close"].dropna().astype(float).tail(window + 1)
            )
        except Exception as exc:
            excluded.append({"symbol": h["symbol"], "reason": f"no price history ({exc})"})
    as_of = freshest(loaded)
    closes = {}
    for sym, c in loaded.items():
        reason = assess(c, MIN_COMMON_DAYS + 1, as_of)
        if reason:
            excluded.append({"symbol": sym, "reason": reason})
        else:
            closes[sym] = c
    if not closes:
        raise ValueError("No holding has a year of price history")
    prices = pd.DataFrame(closes).dropna()
    bench = None
    if benchmark:
        try:
            b = load_bars(benchmark)["Close"].dropna().astype(float)
            joined = prices.join(b.rename("__bench__"), how="inner")
            if len(joined) > MIN_COMMON_DAYS:
                prices, bench = joined.drop(columns="__bench__"), joined["__bench__"]
        except Exception:
            bench = None  # projection still runs; the index comparison is left out
    if len(prices) < MIN_COMMON_DAYS + 1:
        raise ValueError(f"Only {len(prices)} trading days shared by all holdings")
    values = {h["symbol"]: float(h["value"]) for h in holdings if h["symbol"] in prices}
    total = sum(values.values())
    weights = np.array([values[s] / total for s in prices.columns])
    returns = prices.pct_change().iloc[1:]
    return {
        "portfolio": returns.to_numpy() @ weights,
        "benchmark": bench.pct_change().iloc[1:].to_numpy() if bench is not None else None,
        "from": str(returns.index[0].date()),
        "to": str(returns.index[-1].date()),
        "symbols": list(prices.columns),
        "excluded": excluded,
        "covered_value": total,
    }


def project(
    port_r: np.ndarray,
    bench_r: np.ndarray | None,
    start_value: float,
    invested_share: float,
    years: int,
    simulations: int,
    seed: int,
) -> dict:
    """End values, yearly fan, drawdowns and the chance of beating the benchmark.
    `invested_share` scales the daily return for the part held in cash, which earns nothing."""
    horizon = years * YEAR
    log_p = np.log1p(port_r * invested_share)
    log_b = np.log1p(bench_r) if bench_r is not None else None
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(horizon / BLOCK_DAYS)
    ends, ends_b, yearly, drawdowns = [], [], [], []
    for done in range(0, simulations, CHUNK):
        k = min(CHUNK, simulations - done)
        starts = rng.integers(0, len(log_p) - BLOCK_DAYS + 1, size=(k, n_blocks))
        idx = (starts[:, :, None] + np.arange(BLOCK_DAYS)).reshape(k, -1)[:, :horizon]
        cum = np.cumsum(log_p[idx], axis=1)
        ends.append(cum[:, -1])
        yearly.append(cum[:, YEAR - 1 :: YEAR])
        peak = np.maximum.accumulate(np.maximum(cum, 0.0), axis=1)  # the start counts as a peak
        drawdowns.append((cum - peak).min(axis=1))
        if log_b is not None:
            ends_b.append(np.cumsum(log_b[idx], axis=1)[:, -1])
    end_log = np.concatenate(ends)
    end = start_value * np.exp(end_log)
    years_cum = np.concatenate(yearly)
    dd = np.expm1(np.concatenate(drawdowns))
    cagr = np.expm1(end_log / years)

    def pcts(a, scale=1.0, nd=0):
        return {
            f"p{q}": round(float(np.percentile(a, q)) * scale, nd)
            for q in (5, 10, 25, 50, 75, 90, 95)
        }

    lo, hi = np.percentile(end, [0.5, 99.5])
    counts, edges = np.histogram(end, bins=30, range=(lo, hi))
    hist_years = len(port_r) / YEAR
    out = {
        "start_value": round(start_value, 0),
        "years": years,
        "simulations": simulations,
        "seed": seed,
        "block_days": BLOCK_DAYS,
        "end_value": pcts(end),
        "cagr_pct": pcts(cagr, 100, 2),
        "prob_end_below_start": round(float((end < start_value).mean()), 4),
        "max_drawdown_pct": {
            "median": round(float(np.median(dd)) * 100, 1),
            "worst_10pct": round(float(np.percentile(dd, 10)) * 100, 1),
        },
        "yearly": [
            {
                "year": y + 1,
                "p10": round(start_value * math.exp(np.percentile(years_cum[:, y], 10)), 0),
                "p50": round(start_value * math.exp(np.percentile(years_cum[:, y], 50)), 0),
                "p90": round(start_value * math.exp(np.percentile(years_cum[:, y], 90)), 0),
            }
            for y in range(years)
        ],
        "histogram": {
            "counts": counts.tolist(),
            "edges": [round(float(x), 0) for x in edges],
            "outside_range": int(((end < lo) | (end > hi)).sum()),
        },
        "history_cagr_pct": round(
            float(np.expm1(log_p.sum() / hist_years)) * 100 if hist_years else 0.0, 2
        ),
        "prob_beats_benchmark": None,
    }
    if ends_b:
        # benchmark fully invested; compare growth, not rupees
        out["prob_beats_benchmark"] = round(float((end_log > np.concatenate(ends_b)).mean()), 4)
    return out
