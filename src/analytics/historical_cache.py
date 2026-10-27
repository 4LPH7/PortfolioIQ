"""
PortfolioIQ — Historical OHLCV Bar Cache & Synchronization
Provides local PostgreSQL caching for equity and benchmark price histories
to guarantee sub-millisecond backtest data retrieval with zero external rate-limit risk.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import yfinance as yf
from loguru import logger

from src.db.repository import (
    delete_cached_bars,
    get_historical_bars,
    get_symbols_with_unadjusted_bars,
    mark_bars_legacy_before,
    mark_bars_legacy_on,
    upsert_historical_bars,
)
from src.ingestion.market_hours import is_holiday, now_ist
from src.ingestion.ticker_map import (
    DEFAULT_BENCHMARK_SYMBOL,
    get_yf_ticker,
)
from src.models.dtos import HistoricalBarDTO

# Every cached row keeps the traded close in `close` and the split- and dividend-adjusted close in
# `adj_close`, from the same source. Return maths reads the adjusted series; price displays read raw.
PRICE_BASIS = "close+adj_close"
EMPTY_COLUMNS = ["Open", "High", "Low", "Close", "Raw Close", "Volume"]


def get_last_completed_trading_date(as_of: datetime | None = None) -> date:
    """
    Find the most recent completed market trading day.
    If market is currently open or pre-open, returns previous trading day.
    """
    now = as_of or now_ist()
    current_date = now.date()

    # Before 16:00 IST, today's close is not finalized; start check from yesterday
    if now.hour < 16:
        current_date -= timedelta(days=1)

    # Step backwards skipping weekends and holidays
    while current_date.weekday() >= 5 or is_holiday(current_date):
        current_date -= timedelta(days=1)

    return current_date


def _bars_to_dataframe(bars: list[HistoricalBarDTO]) -> pd.DataFrame:
    """
    Convert cached bars into an adjusted OHLCV DataFrame.

    Open/High/Low/Close are scaled by adj_close / close, so returns include splits and dividends.
    "Raw Close" keeps the traded price for comparisons with a purchase price or a live quote.
    """
    if not bars:
        return pd.DataFrame(columns=EMPTY_COLUMNS)

    records = []
    for b in bars:
        records.append(
            {
                "Date": pd.to_datetime(b.bar_date),
                "Open": float(b.open),
                "High": float(b.high),
                "Low": float(b.low),
                "Raw Close": float(b.close),
                "Adj": float(b.adj_close) if b.adj_close is not None else float("nan"),
                "Volume": int(b.volume),
            }
        )
    df = pd.DataFrame(records)
    df.set_index("Date", inplace=True)
    df.sort_index(ascending=True, inplace=True)

    # ponytail: rows cached before adj_close existed reuse the earliest later factor; a corporate
    # action older than the refetched history isn't reflected. Rows with no factor at all keep 1.0.
    factor = (df["Adj"] / df["Raw Close"]).bfill().fillna(1.0)
    for col in ("Open", "High", "Low"):
        df[col] = df[col] * factor
    df["Close"] = df["Raw Close"] * factor
    return df[EMPTY_COLUMNS]


def raw_close(df: pd.DataFrame) -> pd.Series:
    """The traded close: for current-price displays and comparisons with a purchase price."""
    return df["Raw Close"] if "Raw Close" in df.columns else df["Close"]


def _df_to_dtos(
    tradingsymbol: str, df: pd.DataFrame, after: date | None = None
) -> list[HistoricalBarDTO]:
    """Rows from a fetched frame (traded "Close" plus optional "Adj Close") as cache DTOs."""
    dtos = []
    has_adj = "Adj Close" in df.columns
    for dt, row in df.iterrows():
        bar_d = dt.date() if isinstance(dt, datetime | pd.Timestamp) else dt
        if after is not None and bar_d <= after:
            continue
        adj = row["Adj Close"] if has_adj else None
        adj_ok = adj is not None and pd.notna(adj) and float(adj) > 0
        dtos.append(
            HistoricalBarDTO(
                tradingsymbol=tradingsymbol,
                bar_date=bar_d,
                open=Decimal(str(round(float(row["Open"]), 2))),
                high=Decimal(str(round(float(row["High"]), 2))),
                low=Decimal(str(round(float(row["Low"]), 2))),
                close=Decimal(str(round(float(row["Close"]), 2))),
                volume=int(row.get("Volume", 0) or 0),
                adj_close=Decimal(str(round(float(adj), 4))) if adj_ok else None,
                price_basis=PRICE_BASIS if adj_ok else None,
            )
        )
    return dtos


def _fetch_from_yahoo_chart_api(
    yf_ticker: str,
    start_date: date | None = None,
    period: str = "2y",
) -> pd.DataFrame | None:
    """Safely fetch historical daily bars using direct Yahoo Finance chart API with browser headers."""
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Referer": "https://finance.yahoo.com/",
    }

    range_map = {
        "1mo": "1mo",
        "3mo": "3mo",
        "6mo": "6mo",
        "1y": "1y",
        "2y": "2y",
        "5y": "5y",
        "10y": "10y",
    }
    api_range = range_map.get(period, "2y")

    if start_date is not None:
        start_ts = int(datetime.combine(start_date, datetime.min.time()).timestamp())
        end_ts = int(datetime.now().timestamp())
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}?period1={start_ts}&period2={end_ts}&interval=1d"
    else:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}?range={api_range}&interval=1d"

    try:
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("chart", {}).get("result", [])
            if result:
                item = result[0]
                timestamps = item.get("timestamp", [])
                indicators = item.get("indicators", {})
                quote = indicators.get("quote", [{}])[0]
                opens = quote.get("open", [])
                highs = quote.get("high", [])
                lows = quote.get("low", [])
                closes = quote.get("close", [])
                volumes = quote.get("volume", [])
                # Split- and dividend-adjusted closes; missing for some instruments (indices)
                adj_closes = (indicators.get("adjclose") or [{}])[0].get("adjclose") or [
                    None
                ] * len(timestamps)

                records = []
                for ts, bar_open, bar_high, bar_low, bar_close, bar_vol, bar_adj in zip(
                    timestamps, opens, highs, lows, closes, volumes, adj_closes, strict=False
                ):
                    if bar_close is not None and bar_open is not None:
                        dt = (
                            pd.to_datetime(ts, unit="s", utc=True)
                            .tz_convert("Asia/Kolkata")
                            .tz_localize(None)
                        )
                        records.append(
                            {
                                "Date": dt,
                                "Open": float(bar_open),
                                "High": float(bar_high if bar_high is not None else bar_open),
                                "Low": float(bar_low if bar_low is not None else bar_open),
                                "Close": float(bar_close),
                                # Missing stays missing: the row is then queued for backfill, never
                                # labelled adjusted on a guess
                                "Adj Close": float(bar_adj)
                                if bar_adj is not None
                                else float("nan"),
                                "Volume": int(bar_vol or 0),
                            }
                        )
                if records:
                    df = pd.DataFrame(records).set_index("Date").sort_index()
                    logger.debug("Direct Yahoo chart fetched {} bars for {}", len(df), yf_ticker)
                    return df
    except Exception as exc:
        logger.debug("Direct Yahoo chart fetch for {} failed: {}", yf_ticker, exc)

    return None


def _fetch_from_yfinance(
    yf_ticker: str,
    start_date: date | None = None,
    period: str = "2y",
) -> pd.DataFrame | None:
    """Safely fetch historical data from Yahoo Finance with rate-limit evasion and fallbacks."""
    # 1. Primary: Direct Yahoo v8 chart API with browser headers (immune to 429 scraping blocks)
    chart_df = _fetch_from_yahoo_chart_api(yf_ticker, start_date=start_date, period=period)
    if chart_df is not None and not chart_df.empty:
        return chart_df

    # 2. Secondary: Alternative exchange suffix (.BO vs .NS)
    alt_ticker = (
        yf_ticker.replace(".NS", ".BO")
        if ".NS" in yf_ticker
        else yf_ticker.replace(".BO", ".NS")
        if ".BO" in yf_ticker
        else None
    )
    if alt_ticker:
        chart_df_alt = _fetch_from_yahoo_chart_api(alt_ticker, start_date=start_date, period=period)
        if chart_df_alt is not None and not chart_df_alt.empty:
            return chart_df_alt

    # 3. Tertiary: Fallback to yfinance package
    try:
        logger.debug(
            "Querying yfinance package for {} (start={}, period={})", yf_ticker, start_date, period
        )
        ticker = yf.Ticker(yf_ticker)
        # auto_adjust=False keeps the traded "Close" and adds "Adj Close", the same convention as
        # the chart API path, so the cache never mixes adjusted and unadjusted closes.
        if start_date is not None:
            df = ticker.history(start=start_date.strftime("%Y-%m-%d"), auto_adjust=False)
        else:
            df = ticker.history(period=period, auto_adjust=False)

        if df is None or df.empty:
            return None

        # Flatten MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df.index = pd.to_datetime(df.index)
        return df
    except Exception as exc:
        logger.warning("yfinance download error for {}: {}", yf_ticker, exc)
        return None


def get_or_sync_historical_bars(
    tradingsymbol: str,
    lookback_days: int = 750,
    force_sync: bool = False,
) -> pd.DataFrame:
    """
    Retrieve daily OHLCV bars from local database cache, synchronizing
    missing delta bars from Yahoo Finance if stale.

    Args:
        tradingsymbol: Zerodha tradingsymbol (e.g. 'INFY', 'ITBEES', 'NIFTY 50 TRI')
        lookback_days: Maximum calendar days of history to return (default 750 ~2.5 yrs)
        force_sync: If True, bypass cache freshness check and fetch full history

    Returns:
        pd.DataFrame with DatetimeIndex and adjusted ['Open', 'High', 'Low', 'Close'],
        plus 'Raw Close' (traded) and 'Volume'
    """
    start_cutoff = date.today() - timedelta(days=lookback_days)
    db_bars = get_historical_bars(tradingsymbol, start_date=start_cutoff)
    last_trading_day = get_last_completed_trading_date()

    # 1. Cache hit: We already have bars covering up to the latest market trading day
    if db_bars and not force_sync:
        max_cached_date = max(b.bar_date for b in db_bars)
        if max_cached_date >= last_trading_day:
            logger.debug(
                "Cache HIT for {} ({} bars, latest={})",
                tradingsymbol,
                len(db_bars),
                max_cached_date,
            )
            return _bars_to_dataframe(db_bars)

        # 2. Incremental fetch from the last cached day, so that day can be compared
        yf_ticker = get_yf_ticker(tradingsymbol)
        logger.info(
            "Cache STALE for {}: fetching delta bars from {} to {}",
            tradingsymbol,
            max_cached_date,
            last_trading_day,
        )
        try:
            delta_df = _fetch_from_yfinance(yf_ticker, start_date=max_cached_date)
        except Exception as exc:
            logger.warning("Error fetching delta bars for {}: {}", tradingsymbol, exc)
            delta_df = None

        if delta_df is not None and not delta_df.empty:
            if _adjustment_restated(db_bars, delta_df):
                # A dividend or split since the last fetch restates every past adjusted close
                logger.info("Corporate action detected for {}: refetching history", tradingsymbol)
                return get_or_sync_historical_bars(tradingsymbol, lookback_days, force_sync=True)
            delta_dtos = _df_to_dtos(tradingsymbol, delta_df, after=max_cached_date)
            if delta_dtos:
                upsert_historical_bars(delta_dtos)
                logger.debug("Upserted {} delta bars for {}", len(delta_dtos), tradingsymbol)
                db_bars = get_historical_bars(tradingsymbol, start_date=start_cutoff)

        return _bars_to_dataframe(db_bars)

    # 3. Cold start or forced sync: full history (10y gives the scanner's evidence test enough months)
    yf_ticker = get_yf_ticker(tradingsymbol)
    logger.info("Cold start sync for {} ({})", tradingsymbol, yf_ticker)
    try:
        full_df = _fetch_from_yfinance(yf_ticker, period="10y")
    except Exception as exc:
        logger.warning("Error fetching full history for {}: {}", tradingsymbol, exc)
        full_df = None

    if full_df is not None and not full_df.empty:
        all_dtos = _df_to_dtos(tradingsymbol, full_df)
        upsert_historical_bars(all_dtos)
        if all_dtos:
            first = min(d.bar_date for d in all_dtos)
            # Rows older than this fetch carry the previous adjustment basis
            mark_bars_legacy_before(tradingsymbol, first)
            _reconcile_missing_days(tradingsymbol, first, {d.bar_date for d in all_dtos})
        logger.info("Persisted {} bars for {}", len(all_dtos), tradingsymbol)
        db_bars = get_historical_bars(tradingsymbol, start_date=start_cutoff)
        return _bars_to_dataframe(db_bars)

    # Fallback if yfinance failed but we have stale DB bars
    if db_bars:
        logger.warning("yfinance unavailable; returning stale cached bars for {}", tradingsymbol)
        return _bars_to_dataframe(db_bars)

    logger.error("No historical bars available for {}", tradingsymbol)
    return pd.DataFrame(columns=EMPTY_COLUMNS)


def _reconcile_missing_days(tradingsymbol: str, first: date, fetched: set[date]) -> None:
    """
    Cached rows inside the refetched window that the source no longer returns.
    On a weekend or exchange holiday no bar can exist (older builds wrote synthetic ones): delete.
    On a trading day the source may just have a gap: keep the row, labelled legacy.
    """
    missing = [b.bar_date for b in get_historical_bars(tradingsymbol, start_date=first)]
    missing = [d for d in missing if d not in fetched and d <= max(fetched)]
    phantom = [d for d in missing if d.weekday() >= 5 or is_holiday(d)]
    if phantom:
        removed = delete_cached_bars(tradingsymbol, phantom)
        logger.info("Removed {} non-trading-day bars for {}", removed, tradingsymbol)
    gaps = [d for d in missing if d not in phantom]
    if gaps:
        mark_bars_legacy_on(tradingsymbol, gaps)


def _adjustment_restated(db_bars: list[HistoricalBarDTO], fetched: pd.DataFrame) -> bool:
    """True when the overlapping day's adj_close/close ratio moved: a new dividend or split."""
    last = max(db_bars, key=lambda b: b.bar_date)
    if last.adj_close is None or "Adj Close" not in fetched.columns:
        return False
    same_day = fetched[fetched.index.normalize() == pd.Timestamp(last.bar_date)]
    if same_day.empty:
        return False
    row = same_day.iloc[0]
    if pd.isna(row["Adj Close"]) or not row["Close"]:
        return False
    cached_ratio = float(last.adj_close) / float(last.close)
    fetched_ratio = float(row["Adj Close"]) / float(row["Close"])
    return abs(fetched_ratio - cached_ratio) > 1e-4


def backfill_adjusted_prices(limit: int = 50) -> dict[str, int]:
    """
    Refetch symbols still holding rows cached before adjusted closes existed (migration 024).
    A symbol that fails to fetch stays queued and is retried on the next run.
    """
    done, failed = 0, 0
    for symbol in get_symbols_with_unadjusted_bars(limit=limit):
        df = get_or_sync_historical_bars(symbol, force_sync=True)
        if df.empty:
            failed += 1
        else:
            done += 1
    if done or failed:
        logger.info("Adjusted-price backfill: {} refetched, {} failed", done, failed)
    return {"refetched": done, "failed": failed}


def get_benchmark_bars(
    lookback_days: int = 750,
    force_sync: bool = False,
) -> pd.DataFrame:
    """Retrieve or synchronize benchmark daily bars (NIFTY 50 TRI)."""
    return get_or_sync_historical_bars(
        DEFAULT_BENCHMARK_SYMBOL,
        lookback_days=lookback_days,
        force_sync=force_sync,
    )
