"""
Portfolio optimiser: suggested target weights for the current holdings.

Methods after QuantConnect Lean's portfolio optimisers (RiskParityPortfolioOptimizer,
MinimumVariancePortfolioOptimizer), Vibe-Trading's hierarchical risk parity, and equal weight.
Limits after ai-hedge-fund's risk stage: weight clamped by a cap stays in cash and is never
redistributed, and every clamp is recorded.

A suggestion is only a preview until it is saved as an inactive allocation profile and then
explicitly activated; nothing here changes targets or orders.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.optimize import minimize
from scipy.spatial.distance import squareform

from src.analytics.cost_calculator import compute_indian_delivery_charges

METHODS = ("risk_parity", "min_variance", "hrp", "equal_weight")
MIN_BARS = 200  # per symbol inside the lookback window
MAX_MISSING_SHARE = 0.05
SHRINKAGE = 0.1
ILL_CONDITIONED = 1e4


# ── Data ──────────────────────────────────────────────────────


@dataclass
class ReturnsData:
    returns: pd.DataFrame  # aligned daily log returns, one column per included symbol
    prices: pd.DataFrame  # aligned adjusted closes
    excluded: dict[str, str] = field(default_factory=dict)  # symbol -> reason


def load_returns(
    symbols: list[str],
    load_bars: Callable[[str], pd.DataFrame],
    lookback_bars: int = 252,
) -> ReturnsData:
    """Adjusted closes over the last `lookback_bars` trading days, validated per symbol."""
    closes: dict[str, pd.Series] = {}
    excluded: dict[str, str] = {}
    for sym in symbols:
        try:
            s = load_bars(sym)["Close"].dropna().astype(float)
        except Exception as exc:  # a bad fetch excludes the symbol, it never fails the preview
            excluded[sym] = f"price history unavailable ({exc.__class__.__name__})"
            continue
        if len(s) < MIN_BARS:
            excluded[sym] = f"only {len(s)} days of history (needs {MIN_BARS})"
            continue
        closes[sym] = s

    if not closes:
        return ReturnsData(pd.DataFrame(), pd.DataFrame(), excluded)

    frame = pd.DataFrame(closes).sort_index()
    window = frame.iloc[-(lookback_bars + 1) :]
    for sym in list(window.columns):
        missing = float(window[sym].isna().mean())
        if missing > MAX_MISSING_SHARE:
            excluded[sym] = (
                f"{missing:.0%} of days missing in the window (max {MAX_MISSING_SHARE:.0%})"
            )
            window = window.drop(columns=sym)
    prices = window.dropna()
    returns = np.log(prices / prices.shift(1)).dropna()
    flat = [s for s in returns.columns if not returns[s].std() > 0]  # suspended: zero variance
    for sym in flat:
        excluded[sym] = "price did not move in the window"
    return ReturnsData(returns.drop(columns=flat), prices.drop(columns=flat), excluded)


def covariance(returns: pd.DataFrame) -> np.ndarray:
    """Annualised covariance with diagonal shrinkage."""
    # ponytail: fixed diagonal shrinkage (λ=0.1); move to Ledoit-Wolf if estimates look unstable.
    sample = np.cov(returns.to_numpy(), rowvar=False) * 252
    sample = np.atleast_2d(sample)
    return (1 - SHRINKAGE) * sample + SHRINKAGE * np.diag(np.diag(sample))


# ── Methods ───────────────────────────────────────────────────


def equal_weight(n: int) -> np.ndarray:
    return np.full(n, 1.0 / n)


def risk_parity(cov: np.ndarray) -> np.ndarray:
    """Equal risk contribution via the convex form: min ½x'Σx − (1/n)·Σ log x, then normalise."""
    n = cov.shape[0]
    b = np.full(n, 1.0 / n)

    def objective(x):
        return 0.5 * x @ cov @ x - b @ np.log(x)

    def gradient(x):
        return cov @ x - b / x

    x0 = 1.0 / np.sqrt(np.diag(cov))
    res = minimize(objective, x0, jac=gradient, method="L-BFGS-B", bounds=[(1e-10, None)] * n)
    w = res.x / res.x.sum()
    return w


def min_variance(cov: np.ndarray, cap: float) -> np.ndarray:
    """Long-only minimum variance; the cap is a bound whenever full investment is reachable."""
    n = cov.shape[0]
    upper = cap if n * cap >= 1.0 else 1.0
    res = minimize(
        lambda w: w @ cov @ w,
        equal_weight(n),
        jac=lambda w: 2 * cov @ w,
        method="SLSQP",
        bounds=[(0.0, upper)] * n,
        constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}],
        options={"ftol": 1e-12, "maxiter": 500},
    )
    w = np.clip(res.x, 0.0, None)
    return w / w.sum()


def hrp(cov: np.ndarray) -> np.ndarray:
    """Hierarchical risk parity (López de Prado): cluster, order, then bisect by inverse variance."""
    n = cov.shape[0]
    if n == 1:
        return np.ones(1)
    sd = np.sqrt(np.diag(cov))
    corr = np.clip(cov / np.outer(sd, sd), -1.0, 1.0)
    dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, None))
    np.fill_diagonal(dist, 0.0)
    order = list(leaves_list(linkage(squareform(dist, checks=False), method="single")))

    def cluster_var(idx: list[int]) -> float:
        sub = cov[np.ix_(idx, idx)]
        ivp = 1.0 / np.diag(sub)
        ivp /= ivp.sum()
        return float(ivp @ sub @ ivp)

    w = np.ones(n)
    clusters = [order]
    while clusters:
        nxt = []
        for c in clusters:
            if len(c) < 2:
                continue
            left, right = c[: len(c) // 2], c[len(c) // 2 :]
            vl, vr = cluster_var(left), cluster_var(right)
            alpha = 1.0 - vl / (vl + vr)
            w[left] *= alpha
            w[right] *= 1.0 - alpha
            nxt += [left, right]
        clusters = nxt
    return w / w.sum()


def weights_for(method: str, cov: np.ndarray, cap: float) -> np.ndarray:
    if method == "risk_parity":
        return risk_parity(cov)
    if method == "min_variance":
        return min_variance(cov, cap)
    if method == "hrp":
        return hrp(cov)
    if method == "equal_weight":
        return equal_weight(cov.shape[0])
    raise ValueError(f"Unknown method '{method}'. Use one of: {', '.join(METHODS)}")


# ── Limits ────────────────────────────────────────────────────


@dataclass
class LimitResult:
    weights: np.ndarray  # fractions of the portfolio, summing to 1 − total cash
    clamps: list[dict]
    intentional_cash: float  # the cash floor
    forced_cash: float  # caused by caps
    feasibility: str  # ok | clamped | infeasible_full_investment


def apply_limits(
    symbols: list[str], weights: np.ndarray, cap: float, cash_floor: float
) -> LimitResult:
    investable = 1.0 - cash_floor
    scaled = weights * investable
    clamps = []
    clamped = scaled.copy()
    for i, sym in enumerate(symbols):
        if clamped[i] > cap + 1e-12:
            clamps.append(
                {
                    "symbol": sym,
                    "before_pct": round(clamped[i] * 100, 2),
                    "after_pct": round(cap * 100, 2),
                }
            )
            clamped[i] = cap
    forced = max(investable - float(clamped.sum()), 0.0)
    if len(symbols) * cap < investable - 1e-9:
        feasibility = "infeasible_full_investment"
    else:
        feasibility = "clamped" if clamps else "ok"
    return LimitResult(clamped, clamps, cash_floor, forced, feasibility)


def risk_contributions(cov: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Each holding's share of portfolio variance (sums to 1 over the invested part)."""
    total = float(w @ cov @ w)
    return (w * (cov @ w)) / total if total > 0 else np.zeros_like(w)


# ── Walk-forward check ────────────────────────────────────────


@dataclass
class WalkForwardResult:
    method: str
    cagr_pct: float | None
    volatility_pct: float | None
    max_drawdown_pct: float | None
    sharpe: float | None
    turnover_pct_per_year: float | None
    costs_pct: float | None
    rebalances: int
    years: float
    note: str | None = None


def _trade_cost_fraction(trades: np.ndarray, value: float, slippage_bps: float) -> float:
    """Indian delivery charges plus slippage on each trade, as a fraction of portfolio value."""
    cost = 0.0
    for t in trades:
        amount = abs(t) * value
        if amount < 1.0:
            continue
        side = "BUY" if t > 0 else "SELL"
        charges = compute_indian_delivery_charges(
            side=side, trade_value=amount, is_first_sell_of_scrip=True
        )
        cost += float(charges.total_charges) + amount * slippage_bps / 10_000
    return cost / value


def walk_forward_allocation(
    prices: pd.DataFrame,
    method: str,
    cap: float,
    cash_floor: float,
    train: int = 252,
    rebalance_every: int = 21,
    slippage_bps: float = 10.0,
    notional: float = 1_000_000.0,
    rf_annual: float = 0.065,
    fixed_weights: np.ndarray | None = None,
) -> WalkForwardResult:
    """
    Rebalance monthly: estimate on the trailing `train` days only (no look-ahead), trade to the
    target with Indian delivery charges and slippage, hold, repeat. `fixed_weights` replays a
    static target (the current profile) under the same costs.
    """
    simple = prices.pct_change().dropna()
    if len(simple) < train + rebalance_every:
        return WalkForwardResult(
            method,
            None,
            None,
            None,
            None,
            None,
            None,
            0,
            0.0,
            note=f"needs {train + rebalance_every} days of common history",
        )
    n = prices.shape[1]
    w = np.zeros(n)  # start in cash
    value = 1.0
    path, turnover, costs, rebalances = [], 0.0, 0.0, 0
    log_rets = np.log(prices / prices.shift(1)).dropna()
    for t in range(train, len(simple)):
        if (t - train) % rebalance_every == 0:
            if fixed_weights is not None:
                target = fixed_weights
            else:
                cov = covariance(log_rets.iloc[t - train : t])
                target = apply_limits(
                    list(prices.columns), weights_for(method, cov, cap), cap, cash_floor
                ).weights
            trades = target - w
            frac = _trade_cost_fraction(trades, value * notional, slippage_bps)
            value *= 1.0 - frac
            costs += frac
            turnover += float(np.abs(trades).sum()) / 2.0
            w = target.copy()
            rebalances += 1
        r = simple.iloc[t].to_numpy()
        growth = float(w @ (1.0 + r)) + (1.0 - float(w.sum()))  # cash earns nothing
        # ponytail: idle cash earns 0; add the risk-free rate if cash balances get large
        new_w = w * (1.0 + r) / growth if growth > 0 else w
        value *= growth
        w = new_w
        path.append(value)

    series = np.concatenate([[1.0], path])  # from the starting capital, so costs count
    years = len(path) / 252.0
    daily = np.diff(np.log(series))
    vol = float(np.std(daily, ddof=1) * np.sqrt(252)) if len(daily) > 1 else 0.0
    cagr = series[-1] ** (1 / years) - 1 if years > 0 and series[-1] > 0 else None
    dd = float(np.min(series / np.maximum.accumulate(series) - 1.0))
    sharpe = ((cagr - rf_annual) / vol) if (cagr is not None and vol > 1e-9) else None
    return WalkForwardResult(
        method=method,
        cagr_pct=round(cagr * 100, 2) if cagr is not None else None,
        volatility_pct=round(vol * 100, 2),
        max_drawdown_pct=round(dd * 100, 2),
        sharpe=round(sharpe, 2) if sharpe is not None else None,
        turnover_pct_per_year=round(turnover / years * 100, 1) if years else None,
        costs_pct=round(costs * 100, 3),
        rebalances=rebalances,
        years=round(years, 2),
    )


# ── Suggestion (what the API returns) ─────────────────────────


def build_suggestion(
    holdings: list[dict],
    method: str,
    load_bars: Callable[[str], pd.DataFrame],
    cap_pct: float = 15.0,
    cash_floor_pct: float = 2.0,
    lookback_bars: int = 252,
    aum: float = 1_000_000.0,
    current_targets_pct: dict[str, float] | None = None,
    walk_forward: bool = True,
) -> dict:
    """
    `holdings`: [{"symbol", "instrument_token", "weight_pct"}] for the current portfolio.
    Returns weights, risk shares, clamps, feasibility, data exclusions and the walk-forward table.
    """
    if method not in METHODS:
        raise ValueError(f"Unknown method '{method}'. Use one of: {', '.join(METHODS)}")
    cap, floor = cap_pct / 100.0, cash_floor_pct / 100.0
    load_bars = functools.cache(load_bars)  # the window and the walk-forward read the same bars
    data = load_returns([h["symbol"] for h in holdings], load_bars, lookback_bars)
    symbols = list(data.returns.columns)
    result: dict = {
        "method": method,
        "cap_pct": cap_pct,
        "cash_floor_pct": cash_floor_pct,
        "excluded": [{"symbol": s, "reason": r} for s, r in data.excluded.items()],
        "lookback_bars": len(data.returns),
        "date_range": [str(data.returns.index[0].date()), str(data.returns.index[-1].date())]
        if len(data.returns)
        else None,
    }
    by_symbol = {h["symbol"]: h for h in holdings}
    # Holdings without usable history keep their current weight; the rest is optimised.
    kept = [by_symbol[s] for s in data.excluded if s in by_symbol]
    held_out = sum(float(h.get("weight_pct") or 0) for h in kept) / 100.0
    if len(symbols) < 2 or floor + held_out >= 1.0:
        result.update(status="not_enough_holdings", weights=[], walk_forward=[])
        result["message"] = "Needs at least two holdings with enough price history."
        return result

    cov = covariance(data.returns)
    cond = float(np.linalg.cond(cov))
    limited = apply_limits(symbols, weights_for(method, cov, cap), cap, floor + held_out)
    shares = risk_contributions(cov, limited.weights)
    result.update(
        status="ok",
        feasibility=limited.feasibility,
        clamps=limited.clamps,
        intentional_cash_pct=round(floor * 100, 2),
        kept_at_current_pct=round(held_out * 100, 2),
        forced_cash_pct=round(limited.forced_cash * 100, 2),
        portfolio_volatility_pct=round(
            float(np.sqrt(limited.weights @ cov @ limited.weights)) * 100, 2
        ),
        condition_number=round(cond, 1),
        ill_conditioned=cond > ILL_CONDITIONED,
        weights=[
            {
                "symbol": s,
                "instrument_token": by_symbol[s]["instrument_token"],
                "current_pct": round(float(by_symbol[s].get("weight_pct") or 0), 2),
                "suggested_pct": round(float(limited.weights[i]) * 100, 2),
                "risk_share_pct": round(float(shares[i]) * 100, 2),
                "kept": False,
            }
            for i, s in enumerate(symbols)
        ]
        + [
            {
                "symbol": h["symbol"],
                "instrument_token": h["instrument_token"],
                "current_pct": round(float(h.get("weight_pct") or 0), 2),
                "suggested_pct": round(float(h.get("weight_pct") or 0), 2),
                "risk_share_pct": None,
                "kept": True,
            }
            for h in kept
        ],
    )

    if walk_forward:
        # All aligned adjusted history for the included symbols: the check needs years
        prices = (
            pd.DataFrame({s: load_bars(s)["Close"].astype(float) for s in symbols})
            .sort_index()
            .dropna()
        )
        rows = [walk_forward_allocation(prices, m, cap, floor, notional=aum) for m in METHODS]
        fixed = np.array([(current_targets_pct or {}).get(s, 0.0) / 100.0 for s in symbols])
        if fixed.sum() > 0:
            rows.append(
                walk_forward_allocation(
                    prices, "current_targets", cap, floor, notional=aum, fixed_weights=fixed
                )
            )
        result["walk_forward"] = [r.__dict__ for r in rows]
    return result
