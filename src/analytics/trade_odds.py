"""
Stop and target odds for a delivery (long) position.

After the Time Machine in Jakeschincariol/day-trader-skill (MIT), moved from 5-minute bars to
daily bars and from a US day trade to an NSE delivery holding. Two views of the same plan:

- Replays: thousands of futures built from the stock's own recent days, drawn in blocks of
  consecutive days so a volatile week stays volatile. Each day is checked for the stop before the
  target, and a day that touches both counts as the stop.
- History: the same plan (stop and target as the same percentages) started on every past day.
  That is what really happened, though overlapping windows are not independent trials.

Odds are judged against break-even after Indian delivery charges, not against a coin flip.
A simulation from the stock's past, not a prediction.
"""

from __future__ import annotations

import math
from decimal import Decimal

import numpy as np
import pandas as pd

from src.analytics.cost_calculator import compute_indian_delivery_charges

MIN_POOL_DAYS = 250
BLOCK_DAYS = 5
NOMINAL_POSITION_INR = 100_000.0  # charges assumption when no max loss is given


def _ratios(bars: pd.DataFrame) -> np.ndarray:
    """(n-1, 4) open, high, low, close over the previous close; row k-1 is day k."""
    df = bars[["Open", "High", "Low", "Close"]].astype(float)
    r = df.div(df["Close"].shift(1), axis=0).iloc[1:].to_numpy()
    if not (np.isfinite(r).all() and (r > 0).all()):
        raise ValueError("Price history has zero or missing values")
    # Vendor bars sometimes put the close outside the high-low range
    r[:, 1] = r[:, [0, 1, 3]].max(axis=1)
    r[:, 2] = r[:, [0, 2, 3]].min(axis=1)
    return r


def _outcomes(r: np.ndarray, entry: np.ndarray, stop: np.ndarray, target: np.ndarray) -> dict:
    """
    r: (paths, days, 4) daily ratios; entry/stop/target: (paths,) prices.
    A gap below the stop fills at the open (worse); the target fills at the target.
    """
    paths, days = r.shape[:2]
    closes = entry[:, None] * np.cumprod(r[:, :, 3], axis=1)
    prev = np.concatenate([entry[:, None], closes[:, :-1]], axis=1)
    stop_hit = prev * r[:, :, 2] <= stop[:, None]
    target_hit = prev * r[:, :, 1] >= target[:, None]
    stop_day = np.where(stop_hit.any(1), stop_hit.argmax(1), days)
    target_day = np.where(target_hit.any(1), target_hit.argmax(1), days)
    stop_first = (stop_day < days) & (stop_day <= target_day)
    target_first = (target_day < days) & (target_day < stop_day)
    rows = np.arange(paths)
    gap_open = (
        prev[rows, np.minimum(stop_day, days - 1)] * r[rows, np.minimum(stop_day, days - 1), 0]
    )
    exit_price = np.where(
        stop_first,
        np.minimum(stop, gap_open),
        np.where(target_first, target, closes[:, -1]),
    )
    return {
        "stop_first": stop_first,
        "target_first": target_first,
        "exit_price": exit_price,
        "stop_day": stop_day,
        "target_day": target_day,
        "closes": closes,
    }


def _charges(side: str, value: float) -> float:
    return float(compute_indian_delivery_charges(side, Decimal(str(round(value, 2)))).total_charges)


def _loss_at_stop(qty: int, entry: float, stop: float) -> float:
    return qty * (entry - stop) + _charges("BUY", qty * entry) + _charges("SELL", qty * stop)


def size_position(
    entry: float,
    stop: float,
    target: float,
    max_loss_inr: float,
    cash_inr: float | None,
    net_worth_inr: float,
    held_value_inr: float,
    cap_pct: float,
) -> dict:
    """Largest quantity whose loss at the stop, charges included, stays within max_loss_inr,
    then capped by cash (when known) and by the concentration limit."""
    entry, stop, target = float(entry), float(stop), float(target)
    lo, hi = 0, int(max_loss_inr // (entry - stop))
    while lo < hi:  # loss grows with quantity, so the largest fit is a binary search
        mid = (lo + hi + 1) // 2
        if _loss_at_stop(mid, entry, stop) <= max_loss_inr:
            lo = mid
        else:
            hi = mid - 1
    qty, binding = lo, "max loss"
    room = cap_pct / 100 * net_worth_inr - held_value_inr
    by_cap = max(0, int(room // entry)) if net_worth_inr > 0 else qty
    if by_cap < qty:
        qty, binding = by_cap, "concentration limit"
    if cash_inr is not None:
        by_cash = int(cash_inr // (entry * 1.0015))  # leave room for buy-side charges
        if by_cash < qty:
            qty, binding = max(0, by_cash), "available cash"
    return {
        "quantity": qty,
        "value_inr": round(qty * entry, 2),
        "loss_at_stop_inr": round(_loss_at_stop(qty, entry, stop), 2) if qty else 0.0,
        "gain_at_target_inr": round(
            qty * (target - entry) - _charges("BUY", qty * entry) - _charges("SELL", qty * target),
            2,
        )
        if qty
        else 0.0,
        "binding": binding,
        "cash_known": cash_inr is not None,
    }


def verdict(target_first: float, stop_first: float, break_even: float, neither: float) -> dict:
    decided = target_first + stop_first
    if decided == 0:
        return {"text": "Neither level was reached in any replay.", "edge": None}
    share = target_first / decided
    edge = share - break_even
    side = "the target" if edge > 0 else "the stop"
    if abs(edge) < 0.05:
        text = "Close to break-even after charges."
    elif abs(edge) < 0.15:
        text = f"Leans toward {side}."
    else:
        text = f"Strongly toward {side}."
    notes = []
    if neither > 0.40:
        notes.append(
            f"{neither:.0%} of replays reached neither level, so the price at the end decides those."
        )
    return {"text": text, "edge": round(edge, 4), "notes": notes}


def compute_odds(
    bars: pd.DataFrame,
    stop: float,
    target: float,
    entry: float | None = None,
    horizon_days: int = 20,
    lookback_years: int = 2,
    replays: int = 10_000,
    seed: int = 19,
    position_qty: int | None = None,
) -> dict:
    """Odds for buying at `entry` (default: last traded close) with a stop and a target."""
    bars = bars.dropna(subset=["Open", "High", "Low", "Close"])
    stop, target = float(stop), float(target)
    if entry is None:
        entry = (bars["Raw Close"] if "Raw Close" in bars else bars["Close"]).iloc[-1]
    entry = float(entry)
    if not stop < entry < target:
        raise ValueError(f"Needs stop < entry < target (got {stop}, {entry:.2f}, {target})")

    all_r = _ratios(bars)
    pool = all_r[-lookback_years * 252 :]
    if len(pool) < MIN_POOL_DAYS:
        raise ValueError(f"Only {len(pool)} days of price history (needs {MIN_POOL_DAYS})")

    # Replays: blocks of consecutive days from the pool
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(horizon_days / BLOCK_DAYS)
    starts = rng.integers(0, len(pool) - BLOCK_DAYS + 1, size=(replays, n_blocks))
    idx = (starts[:, :, None] + np.arange(BLOCK_DAYS)).reshape(replays, -1)[:, :horizon_days]
    full = np.full(replays, 1.0)
    sim = _outcomes(pool[idx], entry * full, stop * full, target * full)

    p_target = float(sim["target_first"].mean())
    p_stop = float(sim["stop_first"].mean())
    p_neither = 1.0 - p_target - p_stop
    final = sim["closes"][:, -1]
    green = sim["target_first"] | (~sim["stop_first"] & (final > entry))

    # Break-even after charges, at the sized quantity or a nominal position
    qty = position_qty or max(1, int(NOMINAL_POSITION_INR // entry))
    buy = _charges("BUY", qty * entry)
    win = qty * (target - entry) - buy - _charges("SELL", qty * target)
    loss = qty * (entry - stop) + buy + _charges("SELL", qty * stop)
    break_even = loss / (win + loss) if win > 0 else 1.0
    risk = qty * (entry - stop)
    net = (
        qty * (sim["exit_price"] - entry) - buy - _charges("SELL", qty * entry)
    )  # ponytail: flat sell-charge approximation

    # History: the same percentages started on every past day
    hist = None
    h = horizon_days
    if len(all_r) >= h:
        # window j holds days j+1..j+h and starts from day j's close
        windows = np.lib.stride_tricks.sliding_window_view(all_r, h, axis=0).transpose(0, 2, 1)
        e = bars["Close"].astype(float).to_numpy()[: len(windows)]
        res = _outcomes(windows, e, e * stop / entry, e * target / entry)
        n = len(windows)
        hist = {
            "windows": n,
            "independent_windows": n // h,
            "target_first": int(res["target_first"].sum()),
            "stop_first": int(res["stop_first"].sum()),
            "neither": int(n - res["target_first"].sum() - res["stop_first"].sum()),
            "from": str(bars.index[0].date()),
            "to": str(bars.index[n - 1].date()),
        }

    pct = np.percentile(sim["closes"], [5, 25, 50, 75, 95], axis=0)
    counts, edges = np.histogram(final, bins=24)
    stopped = sim["stop_first"]
    return {
        "entry": round(entry, 2),
        "stop": stop,
        "target": target,
        "reward_to_risk": round((target - entry) / (entry - stop), 2),
        "horizon_days": horizon_days,
        "replays": replays,
        "seed": seed,
        "block_days": BLOCK_DAYS,
        "pool": {"days": len(pool), "lookback_years": lookback_years},
        "odds": {
            "target_first": round(p_target, 4),
            "stop_first": round(p_stop, 4),
            "neither": round(p_neither, 4),
            "green": round(float(green.mean()), 4),
        },
        "break_even": {
            "target_share_needed": round(break_even, 4),
            "position_qty": qty,
            "assumed": position_qty is None,
            "charges_round_trip_inr": round(buy + _charges("SELL", qty * entry), 2),
        },
        "net_pnl": {
            "mean_inr": round(float(net.mean()), 2),
            "mean_r": round(float(net.mean() / risk), 3),
            "worst_stop_fill": round(float(sim["exit_price"][stopped].min()), 2)
            if stopped.any()
            else None,
        },
        "median_days": {
            "to_target": int(np.median(sim["target_day"][sim["target_first"]]) + 1)
            if sim["target_first"].any()
            else None,
            "to_stop": int(np.median(sim["stop_day"][stopped]) + 1) if stopped.any() else None,
        },
        "price_at_end": {
            "p5": round(float(np.percentile(final, 5)), 2),
            "p50": round(float(np.percentile(final, 50)), 2),
            "p95": round(float(np.percentile(final, 95)), 2),
        },
        "fan": {
            k: [round(float(v), 2) for v in row]
            for k, row in zip(("p5", "p25", "p50", "p75", "p95"), pct, strict=True)
        },
        "histogram": {"counts": counts.tolist(), "edges": [round(float(x), 2) for x in edges]},
        "history": hist,
        "verdict": verdict(p_target, p_stop, break_even, p_neither),
    }
