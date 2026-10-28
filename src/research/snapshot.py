"""
The research committee's only input: a deterministic snapshot of one stock.

Every value carries {value, unit, source, as_of}. The flattened key paths form the grounding
allowlist: a number the committee cites must name one of these keys and match its value.
Serialised with sorted keys so the same data gives the same bytes (and the same prompt cache).
"""

from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd

SNAPSHOT_SCHEMA_VERSION = 1
MIN_BARS = 60
BARS_SOURCE = "historical_daily_bars (dividend-adjusted)"


def _v(value: float, unit: str, source: str, as_of: str) -> dict:
    return {"value": round(float(value), 2), "unit": unit, "source": source, "as_of": as_of}


def _rsi(closes: pd.Series, n: int = 14) -> float:
    delta = closes.diff().dropna()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean().iloc[-1]
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean().iloc[-1]
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def build_snapshot(
    symbol: str,
    bars: pd.DataFrame,
    holding: dict | None,
    fundamentals: dict | None,
    now: datetime,
    track_record: dict | None = None,
) -> dict:
    """
    `bars`: adjusted daily bars ("Close", optional "Raw Close"). `holding`: {"weight_pct",
    "unrealised_pnl_pct"} or None. `fundamentals`: a latest_fundamentals() row or None.
    `track_record`: runs.track_record() for this stock, or None below its minimum sample.
    """
    closes = bars["Close"].dropna().astype(float)
    if len(closes) < MIN_BARS:
        raise ValueError(f"{symbol} has {len(closes)} days of price history (needs {MIN_BARS})")
    traded = bars["Raw Close"].dropna() if "Raw Close" in bars else closes
    as_of = str(closes.index[-1].date())
    year = closes.tail(252)
    log_r = np.log(year / year.shift(1)).dropna()

    price = {"last_close": _v(traded.iloc[-1], "INR", "historical_daily_bars", as_of)}
    for name, n in (("return_1m", 21), ("return_3m", 63), ("return_6m", 126), ("return_12m", 252)):
        if len(closes) > n:
            price[name] = _v(
                (closes.iloc[-1] / closes.iloc[-1 - n] - 1) * 100, "pct", BARS_SOURCE, as_of
            )
    price["volatility_1y"] = _v(log_r.std() * np.sqrt(252) * 100, "pct", BARS_SOURCE, as_of)
    price["max_drawdown_1y"] = _v((year / year.cummax() - 1).min() * 100, "pct", BARS_SOURCE, as_of)
    price["off_52w_high"] = _v((year.iloc[-1] / year.max() - 1) * 100, "pct", BARS_SOURCE, as_of)
    if len(closes) >= 200:
        sma = closes.rolling(50).mean().iloc[-1] / closes.rolling(200).mean().iloc[-1]
        price["sma50_vs_sma200"] = _v((sma - 1) * 100, "pct", BARS_SOURCE, as_of)
    price["rsi_14"] = _v(_rsi(closes), "index", BARS_SOURCE, as_of)

    data: dict = {"price": price}
    unavailable: list[str] = []
    stamp = now.isoformat(timespec="minutes")
    if holding:
        data["position"] = {
            "weight_pct": _v(holding["weight_pct"], "pct", "portfolio_valuation", stamp),
            "unrealised_pnl_pct": _v(
                holding["unrealised_pnl_pct"], "pct", "portfolio_valuation", stamp
            ),
        }
    else:
        unavailable.append("position: not currently held")

    if fundamentals:
        fetched = fundamentals["fetched_at"]
        fetched = (
            fetched.isoformat(timespec="minutes") if hasattr(fetched, "isoformat") else str(fetched)
        )
        f = {}
        for key, item in sorted(fundamentals["fields"].items()):
            value, unit = float(item["value"]), item["unit"]
            if unit == "ratio":  # one percent convention across the snapshot
                value, unit = value * 100, "pct"
            elif unit == "INR" and key == "market_cap":
                value, unit = value / 1e7, "INR_crore"
            f[key] = _v(value, unit, fundamentals["source"], fetched)
        data["fundamentals"] = f
    else:
        unavailable.append("fundamentals: no fetch in the last 7 days")

    if track_record:
        src = "research_runs (settled 20-day alpha vs NIFTY 50 TRI)"
        record = {"settled_runs": _v(track_record["settled_runs"], "count", src, as_of)}
        for key in ("hit_rate_pct", "avg_alpha_20d_pct"):
            if track_record.get(key) is not None:
                record[key] = _v(track_record[key], "pct", src, as_of)
        data["track_record"] = record
    # Below the minimum sample the section is simply absent, so the prompt is unchanged

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "symbol": symbol,
        "data_as_of": as_of,
        "data": data,
        "unavailable": unavailable,
    }


def allowlist(snapshot: dict) -> dict[str, dict]:
    """{"price.return_1m": {value, unit, ...}, ...}: the only keys a citation may name."""
    return {
        f"{section}.{key}": item
        for section, items in snapshot["data"].items()
        for key, item in items.items()
    }


def to_prompt_json(snapshot: dict) -> str:
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))


ABS_TOL = 0.01
REL_TOL = 0.005


def check_citation(citation: dict, allowed: dict[str, dict]) -> dict:
    """Grounding for one {snapshot_key, value, unit} citation."""
    key, unit = citation.get("snapshot_key"), citation.get("unit")
    item = allowed.get(key)
    if item is None:
        return {**citation, "ok": False, "reason": "key is not in the snapshot"}
    if unit != item["unit"]:
        return {**citation, "ok": False, "reason": f"unit {unit!r}, snapshot has {item['unit']!r}"}
    try:
        cited = float(citation.get("value"))
    except (TypeError, ValueError):
        return {**citation, "ok": False, "reason": "value is not a number"}
    actual = item["value"]
    if abs(cited - actual) <= ABS_TOL or abs(cited - actual) <= REL_TOL * abs(actual):
        return {**citation, "ok": True, "actual": actual}
    return {**citation, "ok": False, "actual": actual, "reason": f"snapshot value is {actual}"}
