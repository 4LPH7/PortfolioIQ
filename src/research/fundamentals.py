"""
Fundamentals for the research committee and the market scanner, fetched by scheduled jobs and
never on a request.

yfinance has no reliable timeout of its own, so symbols are fetched in a child process that is
terminated at a deadline: a hung request cannot outlive the job or leak into the server. One
child handles a batch, and each result is sent back as soon as it is fetched, so a hang late in
a batch keeps the results before it.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import queue
import time
from datetime import UTC, datetime, timedelta

from loguru import logger

from src.db.connection import execute_sql

SOURCE = "yfinance"
MAX_AGE = timedelta(days=7)
FETCH_TIMEOUT_SEC = 25  # per symbol
STARTUP_GRACE_SEC = 5
BATCH = 25

# yfinance `info` key -> (snapshot key, unit). Units are as yfinance reports them for NSE
# tickers; dividend yield is left out because its unit has changed between yfinance releases.
FIELDS = {
    "trailingPE": ("pe_ttm", "x"),
    "priceToBook": ("price_to_book", "x"),
    "returnOnEquity": ("roe", "ratio"),
    "profitMargins": ("net_margin", "ratio"),
    "revenueGrowth": ("revenue_growth_yoy", "ratio"),
    "earningsGrowth": ("earnings_growth_yoy", "ratio"),
    "debtToEquity": ("debt_to_equity", "pct"),
    "marketCap": ("market_cap", "INR"),
}


def _fields(info: dict) -> dict | None:
    fields = {}
    for src_key, (key, unit) in FIELDS.items():
        v = info.get(src_key)
        if isinstance(v, int | float) and v == v and abs(v) != float("inf"):
            fields[key] = {"value": float(v), "unit": unit}
    return fields or None


def _fetch_many(yf_tickers: list[str], out) -> None:  # runs in the child process
    import yfinance as yf

    for t in yf_tickers:
        try:
            out.put((t, _fields(yf.Ticker(t).info or {})))
        except Exception:
            out.put((t, None))
        time.sleep(0.3)  # one ticker at a time, politely


def fetch_fundamentals_batch(
    symbols: list[str], per_symbol_timeout: float = FETCH_TIMEOUT_SEC
) -> dict[str, dict | None]:
    """{symbol: fields or None}. Symbols missing from the result passed the deadline."""
    from src.ingestion.ticker_map import get_yf_ticker

    tickers = {get_yf_ticker(s): s for s in symbols}
    ctx = mp.get_context("spawn")  # no fork of a threaded server process
    out = ctx.Queue()
    proc = ctx.Process(target=_fetch_many, args=(list(tickers), out), daemon=True)
    proc.start()
    deadline = time.monotonic() + STARTUP_GRACE_SEC + per_symbol_timeout * len(tickers)
    results: dict[str, dict | None] = {}
    while len(results) < len(tickers) and time.monotonic() < deadline:
        try:
            ticker, fields = out.get(timeout=1.0)
            results[tickers[ticker]] = fields
        except queue.Empty:
            if not proc.is_alive():
                break  # the child exited without sending the rest
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        logger.warning(
            "Fundamentals fetch stopped at the deadline: {} of {} symbols done",
            len(results),
            len(tickers),
        )
    return results


def fetch_fundamentals(tradingsymbol: str, timeout: float = FETCH_TIMEOUT_SEC) -> dict | None:
    """Fields for one symbol, or None if the fetch failed or passed the deadline."""
    return fetch_fundamentals_batch([tradingsymbol], timeout).get(tradingsymbol)


def refresh_fundamentals(symbols: list[str]) -> dict[str, int]:
    saved = failed = 0
    for i in range(0, len(symbols), BATCH):
        batch = symbols[i : i + BATCH]
        results = fetch_fundamentals_batch(batch)
        for sym in batch:
            fields = results.get(sym)
            if not fields:
                failed += 1
                continue
            execute_sql(
                """INSERT INTO fundamentals_snapshots (tradingsymbol, fields, source)
                   VALUES (:sym, CAST(:fields AS JSONB), :source)""",
                {"sym": sym, "fields": json.dumps(fields, sort_keys=True), "source": SOURCE},
            )
            saved += 1
    logger.info("Fundamentals refresh: {} saved, {} failed", saved, failed)
    return {"saved": saved, "failed": failed}


def latest_fundamentals(tradingsymbol: str, now: datetime | None = None) -> dict | None:
    """{"fields", "source", "fetched_at"} from the newest row, or None if older than 7 days."""
    now = now or datetime.now(UTC)
    rows = execute_sql(
        """SELECT fields, source, fetched_at FROM fundamentals_snapshots
           WHERE tradingsymbol = :sym AND fetched_at >= :cutoff
           ORDER BY fetched_at DESC LIMIT 1""",
        {"sym": tradingsymbol, "cutoff": now - MAX_AGE},
    )
    return rows[0] if rows else None


def latest_fundamentals_many(symbols: list[str]) -> dict[str, dict]:
    """Newest row per symbol, any age; the caller shows `fetched_at` alongside."""
    rows = execute_sql(
        """SELECT DISTINCT ON (tradingsymbol) tradingsymbol, fields, fetched_at
           FROM fundamentals_snapshots WHERE tradingsymbol = ANY(:syms)
           ORDER BY tradingsymbol, fetched_at DESC""",
        {"syms": symbols},
    )
    return {r["tradingsymbol"]: r for r in rows}
