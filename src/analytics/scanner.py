"""
Market scanner: NIFTY 500 ranked on price signals that held up out of sample.

The cross-sectional version of the evidence rule PortfolioIQ applies to single stocks
(indicator_evaluator): a signal counts only while its month-by-month rank correlation with
the next month's return has been positive and significant (t > 2). At every past month the
weights come from earlier months only, so the backtest shows what the ranking would actually
have delivered, after delivery charges and slippage.

It cannot see the future. It measures whether a tilt has paid on average, and the universe is
today's index members, which flatters the past: stocks that dropped out are missing.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import requests
from loguru import logger
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.analytics.data_quality import assess, freshest
from src.db.connection import execute_sql, get_db_session

NIFTY500_CSV = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
SIGNALS = {
    "mom_12_1": "12-month momentum, skipping the last month",
    "mom_6_1": "6-month momentum, skipping the last month",
    "trend_200": "Price against its 200-day average",
    "sma_50_200": "50-day average against the 200-day",
    "near_52w_high": "Closeness to the 52-week high",
    "low_vol": "Low volatility over the past year",
    "reversal_1m": "Last month's losers bounce (short-term reversal)",
}
MIN_IC_MONTHS = 12
MIN_STOCKS = 30
MIN_PRICED_DAYS = 260
TOP_N = 20
POSITION_INR = 25_000
SLIPPAGE_BPS = 10
SHORT_HISTORY_MONTHS = 60
_SYMBOL = re.compile(r"[A-Z0-9&_-]{1,30}")


# ── Signals and statistics (pure) ─────────────────────────────


def compute_signals(prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Each signal as a dates × symbols frame, oriented so that higher means expected better."""
    log_r = np.log(prices).diff()
    sma50 = prices.rolling(50, min_periods=45).mean()
    sma200 = prices.rolling(200, min_periods=180).mean()
    return {
        "mom_12_1": prices.shift(21) / prices.shift(252) - 1,
        "mom_6_1": prices.shift(21) / prices.shift(126) - 1,
        "trend_200": prices / sma200 - 1,
        "sma_50_200": sma50 / sma200 - 1,
        "near_52w_high": prices / prices.rolling(252, min_periods=240).max() - 1,
        "low_vol": -log_r.rolling(252, min_periods=200).std(),
        "reversal_1m": -(prices / prices.shift(21) - 1),
    }


def month_ends(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The last trading day of each month in the data (the latest day for the current month)."""
    s = pd.Series(index, index=index)
    return pd.DatetimeIndex(s.groupby(index.to_period("M")).max().to_numpy())


def rank_ic(x: pd.Series, y: pd.Series) -> float | None:
    ok = x.notna() & y.notna()
    if ok.sum() < MIN_STOCKS:
        return None
    ic = x[ok].rank().corr(y[ok].rank())
    return None if pd.isna(ic) else float(ic)


def select_signals(ics: pd.DataFrame) -> dict[str, dict]:
    """Which signals pass on this IC history, and their weights (information ratio)."""
    out = {}
    for name in SIGNALS:
        v = ics[name].dropna() if name in ics else pd.Series(dtype=float)
        n = len(v)
        stats = {"months": n, "mean_ic": None, "t_stat": None, "hit_rate": None, "weight": 0.0}
        if n >= MIN_IC_MONTHS:
            mean, sd = float(v.mean()), float(v.std(ddof=1))
            t = mean / sd * math.sqrt(n) if sd > 0 else 0.0
            stats.update(mean_ic=mean, t_stat=t, hit_rate=float((v > 0).mean()))
            if mean > 0 and t > 2:
                stats["weight"] = mean / sd
        out[name] = stats
    total = sum(s["weight"] for s in out.values())
    for s in out.values():
        s["weight"] = s["weight"] / total if total else 0.0
        s["used"] = s["weight"] > 0
    return out


def composite(sigs: dict[str, pd.DataFrame], day, selection: dict[str, dict]) -> pd.Series | None:
    """Weighted average of each used signal's percentile rank; a stock needs every used signal."""
    used = {k: v["weight"] for k, v in selection.items() if v["used"]}
    if not used:
        return None
    ranks = pd.DataFrame({k: sigs[k].loc[day].rank(pct=True) for k in used}).dropna()
    return (ranks * pd.Series(used)).sum(axis=1)


def _cost_rate() -> float:
    """One-way cost of trading one position: delivery charges plus slippage."""
    from src.analytics.cost_calculator import compute_indian_delivery_charges

    v = Decimal(POSITION_INR)
    charges = (
        compute_indian_delivery_charges("BUY", v).total_charges
        + compute_indian_delivery_charges("SELL", v).total_charges
    )
    return float(charges) / 2 / POSITION_INR + SLIPPAGE_BPS / 1e4


def backtest(sigs: dict, monthly: pd.DataFrame, ics: pd.DataFrame, cost_rate: float) -> dict:
    """Monthly: rank with weights learnt from earlier months only, hold the top TOP_N equally,
    pay for turnover, and compare with an equal-weight hold of every eligible stock."""
    fwd = monthly.shift(-1) / monthly - 1
    rows, prev, idle = [], set(), 0
    for i, day in enumerate(monthly.index[:-1]):
        if i < MIN_IC_MONTHS:
            continue  # not enough earlier months to judge any signal yet
        score = composite(sigs, day, select_signals(ics.iloc[:i]))
        nxt = fwd.loc[day]
        if score is None:
            # nothing had passed yet: hold the whole index that month, so the record is unbroken
            univ = nxt.dropna()
            if len(univ) >= MIN_STOCKS:
                idle += 1
                rows.append(
                    {
                        "date": day,
                        "portfolio": float(univ.mean()),
                        "universe": float(univ.mean()),
                        "turnover": 0.0,
                        "cost": 0.0,
                        "ic": None,
                        "active": False,
                    }
                )
                prev = set()  # coming back in buys a full portfolio
            continue
        eligible = score.index[nxt.reindex(score.index).notna()]
        if len(eligible) < MIN_STOCKS:
            continue
        picks = set(score[eligible].nlargest(TOP_N).index)
        turnover = len(picks - prev) / len(picks)
        cost = cost_rate * (2 * turnover if prev else 1.0)
        rows.append(
            {
                "date": day,
                "portfolio": float(nxt[list(picks)].mean()) - cost,
                "universe": float(nxt[eligible].mean()),
                "turnover": turnover,
                "cost": cost,
                "ic": rank_ic(score[eligible], nxt[eligible]),
                "active": True,
            }
        )
        prev = picks
    if not rows:
        return {"months": 0, "idle_months": 0, "active_months": 0}
    df = pd.DataFrame(rows).set_index("date")
    n = len(df)
    port, univ = (1 + df["portfolio"]).cumprod(), (1 + df["universe"]).cumprod()
    excess_log = np.log1p(df["portfolio"]) - np.log1p(df["universe"])
    ic = df["ic"].dropna()
    ic_t = float(ic.mean() / ic.std(ddof=1) * math.sqrt(len(ic))) if len(ic) > 2 else None
    return {
        "months": n,
        "idle_months": idle,
        "active_months": n - idle,
        "from": str(df.index[0].date()),
        "to": str(monthly.index[monthly.index.get_loc(df.index[-1]) + 1].date()),
        "cagr_pct": round((port.iloc[-1] ** (12 / n) - 1) * 100, 2),
        "universe_cagr_pct": round((univ.iloc[-1] ** (12 / n) - 1) * 100, 2),
        # judged on the months it picked stocks; index months match the index by definition
        "months_beat_universe": round(
            float((df["portfolio"] > df["universe"])[df["active"]].mean())
            if df["active"].any()
            else 0.0,
            3,
        ),
        "avg_monthly_excess_pct": round(float((df["portfolio"] - df["universe"]).mean()) * 100, 3),
        "worst_12m_excess_pct": round(float(np.expm1(excess_log.rolling(12).sum().min())) * 100, 2)
        if n >= 12
        else None,
        "max_drawdown_pct": round(float((port / port.cummax() - 1).min()) * 100, 2),
        "avg_turnover": round(float(df["turnover"].mean()), 3),
        "cost_drag_pct_per_year": round(float(df["cost"].mean()) * 12 * 100, 2),
        "composite_ic": round(float(ic.mean()), 4) if len(ic) else None,
        "composite_ic_t": round(ic_t, 2) if ic_t is not None else None,
        "curve": {
            "dates": [str(d.date()) for d in df.index],
            "portfolio": [round(float(x), 4) for x in port],
            "universe": [round(float(x), 4) for x in univ],
        },
    }


def compute_scan(prices: pd.DataFrame) -> dict:
    """Evidence and today's ranking from an adjusted price matrix (dates × symbols)."""
    if prices.shape[1] < MIN_STOCKS:
        raise ValueError(f"Only {prices.shape[1]} stocks have a year of prices")
    sigs = compute_signals(prices)
    ends = month_ends(prices.index)
    monthly = prices.loc[ends]
    fwd = monthly.shift(-1) / monthly - 1
    ics = pd.DataFrame(
        {k: [rank_ic(sigs[k].loc[d], fwd.loc[d]) for d in ends] for k in SIGNALS},
        index=ends,
        dtype=float,
    )
    bt = backtest(sigs, monthly, ics, _cost_rate())
    now = select_signals(ics)  # every month with a known next-month return
    if bt["months"] < MIN_IC_MONTHS:
        status = "insufficient_data"
    elif bt["active_months"] == 0:
        status = "no_edge"  # no signal ever passed
    elif (
        bt["cagr_pct"] > bt["universe_cagr_pct"]
        and bt["months_beat_universe"] > 0.5
        and (bt["composite_ic_t"] or 0) > 2
    ):
        status = "edge"
    else:
        status = "no_edge"

    latest = prices.index[-1]
    score = composite(sigs, latest, now)
    pct = {k: sigs[k].loc[latest].rank(pct=True) for k in SIGNALS}
    order = (
        score.sort_values(ascending=False).index
        if score is not None
        else sigs["mom_12_1"].loc[latest].dropna().sort_values(ascending=False).index
    )

    def val(name, sym, scale=100.0):
        x = sigs[name].loc[latest, sym]
        return None if pd.isna(x) else round(float(x) * scale, 2)

    score_pct = score.rank(pct=True) if score is not None else None
    ranking = []
    for i, sym in enumerate(order, 1):
        ranking.append(
            {
                "symbol": sym,
                "rank": i if score is not None else None,
                "score": round(float(score_pct[sym]) * 100, 1) if score is not None else None,
                "signals": {
                    k: None if pd.isna(pct[k].get(sym)) else round(float(pct[k][sym]) * 100)
                    for k in SIGNALS
                },
                "mom_12_1_pct": val("mom_12_1", sym),
                "trend_200_pct": val("trend_200", sym),
                "off_52w_high_pct": val("near_52w_high", sym),
                "vol_1y_pct": None
                if pd.isna(sigs["low_vol"].loc[latest, sym])
                else round(-float(sigs["low_vol"].loc[latest, sym]) * math.sqrt(252) * 100, 1),
                "last_price": round(float(prices[sym].iloc[-1]), 2),
            }
        )
    return {
        "as_of": str(latest.date()),
        "status": status,
        "short_history": bt["months"] < SHORT_HISTORY_MONTHS,
        "backtest": bt,
        "signals": {
            k: {
                "label": SIGNALS[k],
                **{f: (round(v, 4) if isinstance(v, float) else v) for f, v in now[k].items()},
            }
            for k in SIGNALS
        },
        "top_n": TOP_N,
        "position_inr": POSITION_INR,
        "slippage_bps": SLIPPAGE_BPS,
        "stocks_ranked": len(ranking),
        "ranking": ranking,
    }


# ── Data ──────────────────────────────────────────────────────


def fetch_universe() -> list[dict]:
    resp = requests.get(NIFTY500_CSV, headers={"User-Agent": "Mozilla/5.0 PortfolioIQ"}, timeout=20)
    resp.raise_for_status()
    rows = []
    for r in csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))):
        sym = (r.get("Symbol") or "").strip().upper()
        if _SYMBOL.fullmatch(sym):
            rows.append(
                {
                    "symbol": sym,
                    "company": (r.get("Company Name") or "").strip()[:200],
                    "industry": (r.get("Industry") or "").strip()[:100],
                    "isin": (r.get("ISIN Code") or "").strip()[:20],
                }
            )
    if len(rows) < 400:
        raise ValueError(f"The NSE list had only {len(rows)} usable rows")
    return rows


def refresh_universe(max_age_days: int = 7) -> list[dict]:
    """Today's members, refetched weekly. If NSE is unreachable, the last list is used."""
    rows = execute_sql(
        """SELECT tradingsymbol AS symbol, company_name AS company, industry, isin, fetched_at
           FROM scanner_universe"""
    )
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    if rows and min(r["fetched_at"] for r in rows) > cutoff:
        return rows
    try:
        fresh = fetch_universe()
    except Exception as exc:
        if rows:
            logger.warning("NSE index list unavailable, using the stored one: {}", exc)
            return rows
        raise
    with get_db_session() as s:
        s.execute(text("DELETE FROM scanner_universe"))
        s.execute(
            text(
                """INSERT INTO scanner_universe (tradingsymbol, company_name, industry, isin)
                   VALUES (:symbol, :company, :industry, :isin)"""
            ),
            fresh,
        )
    return fresh


def sync_prices(symbols: list[str], pause: float = 0.3) -> list[str]:
    """Bring each symbol's cached bars up to date; returns the symbols that failed."""
    from src.analytics.historical_cache import get_or_sync_historical_bars

    failed = []
    for sym in symbols:
        started = time.monotonic()
        try:
            get_or_sync_historical_bars(sym, lookback_days=30)
        except Exception as exc:
            failed.append(sym)
            logger.warning("Scanner price sync failed for {}: {}", sym, exc)
        if time.monotonic() - started > 0.3:  # it went to the network: be polite
            time.sleep(pause)
    return failed


def deep_sync_prices(symbols: list[str], pause: float = 0.3) -> list[str]:
    """Refetch each symbol's full history once (older caches hold about 5 years).
    Returns the symbols that failed; they are retried on the next scan."""
    from src.analytics.historical_cache import get_or_sync_historical_bars

    done = {r["tradingsymbol"] for r in execute_sql("SELECT tradingsymbol FROM scanner_deep_sync")}
    failed = []
    for sym in (s for s in symbols if s not in done):
        try:
            if get_or_sync_historical_bars(sym, force_sync=True).empty:
                raise ValueError("no bars returned")
            execute_sql(
                "INSERT INTO scanner_deep_sync (tradingsymbol) VALUES (:s) ON CONFLICT DO NOTHING",
                {"s": sym},
            )
        except Exception as exc:
            failed.append(sym)
            logger.warning("Scanner deep sync failed for {}: {}", sym, exc)
        time.sleep(pause)
    return failed


def load_prices(symbols: list[str], years: int = 10) -> tuple[pd.DataFrame, list[dict]]:
    """Adjusted closes, one column per symbol, from the cache in a single query, plus every
    symbol left out with its reason (thin history, stale, gap, suspended, or no prices)."""
    rows = execute_sql(
        """SELECT tradingsymbol AS s, bar_date AS d, close::float AS c, adj_close::float AS a
           FROM historical_daily_bars WHERE tradingsymbol = ANY(:syms) AND bar_date >= :since""",
        {"syms": symbols, "since": datetime.now(UTC).date() - timedelta(days=365 * years)},
    )
    if not rows:
        return pd.DataFrame(), [{"symbol": s, "reason": "no price history"} for s in symbols]
    df = pd.DataFrame(rows, columns=["s", "d", "c", "a"])
    close = df.pivot(index="d", columns="s", values="c").sort_index()
    adj = df.pivot(index="d", columns="s", values="a").reindex_like(close)
    prices = close * (adj / close).bfill().fillna(1.0)  # same convention as historical_cache
    prices.index = pd.to_datetime(prices.index)
    own = {c: prices[c].dropna() for c in prices.columns}  # each symbol's real trading days
    as_of = freshest(own)
    excluded = [{"symbol": s, "reason": "no price history"} for s in symbols if s not in own]
    keep = []
    for sym, series in own.items():
        reason = assess(series, MIN_PRICED_DAYS, as_of)
        if reason:
            excluded.append({"symbol": sym, "reason": reason})
        else:
            keep.append(sym)
    excluded.sort(key=lambda e: e["symbol"])
    return prices[keep].ffill(limit=5), excluded


def _enrich(result: dict, universe: list[dict], held: set[str]) -> None:
    from src.research.fundamentals import latest_fundamentals_many

    meta = {u["symbol"]: u for u in universe}
    symbols = [r["symbol"] for r in result["ranking"]]
    funds = latest_fundamentals_many(symbols)
    news = {
        r["tradingsymbol"]: r["n"]
        for r in execute_sql(
            """SELECT tradingsymbol, COUNT(*) AS n FROM news_items
               WHERE tradingsymbol = ANY(:syms)
                 AND COALESCE(published_at, fetched_at) > NOW() - INTERVAL '7 days'
               GROUP BY tradingsymbol""",
            {"syms": symbols},
        )
    }
    for r in result["ranking"]:
        m = meta.get(r["symbol"], {})
        f = funds.get(r["symbol"])
        fields = f["fields"] if f else {}

        def num(key, scale=1.0, fields=fields):
            item = fields.get(key)
            return round(float(item["value"]) * scale, 2) if item else None

        r.update(
            company=m.get("company"),
            industry=m.get("industry"),
            in_index=r["symbol"] in meta,
            held=r["symbol"] in held,
            headlines_7d=int(news.get(r["symbol"], 0)),
            fundamentals={
                "pe_ttm": num("pe_ttm"),
                "price_to_book": num("price_to_book"),
                "roe_pct": num("roe", 100),
                "revenue_growth_pct": num("revenue_growth_yoy", 100),
                "debt_to_equity_pct": num("debt_to_equity"),
                "market_cap_cr": num("market_cap", 1e-7),
                "as_of": str(f["fetched_at"].date()) if f else None,
            },
        )


# ── Runs ──────────────────────────────────────────────────────


def start_scan(trigger: str) -> int | None:
    """A new run row, or None if one is already running (a unique index enforces one)."""
    execute_sql(
        """UPDATE scanner_runs SET status = 'failed', finished_at = NOW(),
               error = 'Did not finish within 3 hours'
           WHERE status = 'running' AND started_at < NOW() - INTERVAL '3 hours'"""
    )
    try:
        return execute_sql(
            "INSERT INTO scanner_runs (trigger) VALUES (:t) RETURNING id", {"t": trigger}
        )[0]["id"]
    except IntegrityError:
        return None


def _previous_completed() -> dict | None:
    rows = execute_sql(
        """SELECT result FROM scanner_runs WHERE status = 'completed'
           ORDER BY finished_at DESC OFFSET 1 LIMIT 1"""
    )
    return rows[0]["result"] if rows else None


def _notify_changes(result: dict) -> None:
    """Push when the verdict flips or a stock enters the top 5. Failures never fail the scan."""
    from src.ingestion import notify

    try:
        prev = _previous_completed()
        if prev and prev.get("status") != result["status"]:
            title, body = notify.verdict_change_message(prev["status"], result["status"])
            notify.send("scanner_verdict", title, body, dedupe_key=f"verdict:{result['as_of']}")
        was_top5 = {r["symbol"] for r in (prev or {}).get("ranking", [])[:5]}
        for row in result["ranking"][:5]:
            if row["symbol"] not in was_top5 and prev:
                title, body = notify.top5_message(row["symbol"], row["rank"])
                notify.send(
                    "scanner_top5",
                    title,
                    body,
                    dedupe_key=f"top5:{row['symbol']}:{result['as_of']}",
                )
    except Exception as exc:  # a notification problem must not look like a failed scan
        logger.warning("Scanner notifications skipped: {}", exc)


def run_scan(run_id: int) -> None:
    from src.analytics.valuator import compute_portfolio_valuation

    try:
        universe = refresh_universe()
        try:
            held = {h.tradingsymbol for h in compute_portfolio_valuation().holdings}
        except Exception:
            held = set()
        symbols = sorted({u["symbol"] for u in universe} | held)
        failed = deep_sync_prices(symbols)
        failed += [s for s in sync_prices(symbols) if s not in failed]
        prices, excluded = load_prices(symbols)
        result = compute_scan(prices)
        _enrich(result, universe, held)
        result["sync_failed"] = failed
        result["excluded"] = excluded
        execute_sql(
            """UPDATE scanner_runs SET status = 'completed', finished_at = NOW(),
                   universe_size = :u, priced = :p, result = CAST(:r AS JSONB) WHERE id = :id""",
            {
                "id": run_id,
                "u": len(universe),
                "p": prices.shape[1],
                "r": json.dumps(
                    result, default=lambda o: o.item() if hasattr(o, "item") else str(o)
                ),
            },
        )
        logger.info(
            "Scan {} done: {} stocks ranked, {}", run_id, result["stocks_ranked"], result["status"]
        )
        _notify_changes(result)
    except Exception as exc:
        logger.exception("Scan {} failed: {}", run_id, exc)
        execute_sql(
            """UPDATE scanner_runs SET status = 'failed', finished_at = NOW(), error = :e
               WHERE id = :id""",
            {"id": run_id, "e": str(exc)[:500]},
        )


def start_in_background(trigger: str = "manual") -> int | None:
    run_id = start_scan(trigger)
    if run_id is not None:
        threading.Thread(
            target=run_scan, args=(run_id,), daemon=True, name=f"scan-{run_id}"
        ).start()
    return run_id


def latest_scan() -> dict:
    done = execute_sql(
        """SELECT id, finished_at, universe_size, priced, result FROM scanner_runs
           WHERE status = 'completed' ORDER BY finished_at DESC LIMIT 1"""
    )
    running = execute_sql(
        "SELECT id, started_at FROM scanner_runs WHERE status = 'running' LIMIT 1"
    )
    failed = execute_sql(
        """SELECT id, finished_at, error FROM scanner_runs WHERE status = 'failed'
           ORDER BY finished_at DESC LIMIT 1"""
    )
    return {
        "latest": done[0] if done else None,
        "running": running[0] if running else None,
        "last_failure": failed[0] if failed else None,
    }


def rank_lookup() -> dict[str, dict]:
    """{symbol: {rank, of, status}} from the latest completed scan; empty when there is none."""
    scan = latest_scan()["latest"]
    if not scan:
        return {}
    result = scan["result"]
    return {
        r["symbol"]: {"rank": r["rank"], "of": result["stocks_ranked"], "status": result["status"]}
        for r in result["ranking"]
    }


def top_symbols(n: int = 20) -> list[str]:
    scan = latest_scan()["latest"]
    if not scan or scan["result"].get("status") == "insufficient_data":
        return []
    return [r["symbol"] for r in scan["result"]["ranking"][:n] if r["rank"] is not None]
