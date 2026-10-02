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

from src.db.repository import get_historical_bars, upsert_historical_bars
from src.ingestion.market_hours import is_holiday, now_ist
from src.ingestion.ticker_map import (
    DEFAULT_BENCHMARK_SYMBOL,
    get_yf_ticker,
)
from src.models.dtos import HistoricalBarDTO


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
    """Convert HistoricalBarDTO list into a standard OHLCV DataFrame."""
    if not bars:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    records = []
    for b in bars:
        records.append(
            {
                "Date": pd.to_datetime(b.bar_date),
                "Open": float(b.open),
                "High": float(b.high),
                "Low": float(b.low),
                "Close": float(b.close),
                "Volume": int(b.volume),
            }
        )
    df = pd.DataFrame(records)
    df.set_index("Date", inplace=True)
    df.sort_index(ascending=True, inplace=True)
    return df


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

    range_map = {"1mo": "1mo", "3mo": "3mo", "6mo": "6mo", "1y": "1y", "2y": "2y", "5y": "5y"}
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
                quote = item.get("indicators", {}).get("quote", [{}])[0]
                opens = quote.get("open", [])
                highs = quote.get("high", [])
                lows = quote.get("low", [])
                closes = quote.get("close", [])
                volumes = quote.get("volume", [])

                records = []
                for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes):
                    if c is not None and o is not None:
                        dt = pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Kolkata").tz_localize(None)
                        records.append({
                            "Date": dt,
                            "Open": float(o),
                            "High": float(h if h is not None else o),
                            "Low": float(l if l is not None else o),
                            "Close": float(c),
                            "Volume": int(v or 0),
                        })
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
        if start_date is not None:
            df = ticker.history(start=start_date.strftime("%Y-%m-%d"), auto_adjust=True)
        else:
            df = ticker.history(period=period, auto_adjust=True)

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
        pd.DataFrame with DatetimeIndex and columns ['Open', 'High', 'Low', 'Close', 'Volume']
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

        # 2. Incremental fetch: Fetch only missing bars past max_cached_date
        delta_start = max_cached_date + timedelta(days=1)
        yf_ticker = get_yf_ticker(tradingsymbol)
        logger.info(
            "Cache STALE for {}: fetching delta bars from {} to {}",
            tradingsymbol,
            delta_start,
            last_trading_day,
        )
        try:
            delta_df = _fetch_from_yfinance(yf_ticker, start_date=delta_start)
        except Exception as exc:
            logger.warning("Error fetching delta bars for {}: {}", tradingsymbol, exc)
            delta_df = None

        if delta_df is not None and not delta_df.empty:
            delta_dtos = []
            for dt, row in delta_df.iterrows():
                bar_d = dt.date() if isinstance(dt, datetime | pd.Timestamp) else dt
                if bar_d > max_cached_date:
                    delta_dtos.append(
                        HistoricalBarDTO(
                            tradingsymbol=tradingsymbol,
                            bar_date=bar_d,
                            open=Decimal(str(round(float(row["Open"]), 2))),
                            high=Decimal(str(round(float(row["High"]), 2))),
                            low=Decimal(str(round(float(row["Low"]), 2))),
                            close=Decimal(str(round(float(row["Close"]), 2))),
                            volume=int(row.get("Volume", 0)),
                        )
                    )
            if delta_dtos:
                upsert_historical_bars(delta_dtos)
                logger.debug(
                    "Upserted {} delta bars for {}",
                    len(delta_dtos),
                    tradingsymbol,
                )
                db_bars = get_historical_bars(tradingsymbol, start_date=start_cutoff)

        return _bars_to_dataframe(db_bars)

    # 3. Cold start or forced sync: Query full history from yfinance
    yf_ticker = get_yf_ticker(tradingsymbol)
    logger.info("Cold start sync for {} ({})", tradingsymbol, yf_ticker)
    try:
        full_df = _fetch_from_yfinance(yf_ticker, period="2y")
    except Exception as exc:
        logger.warning("Error fetching full history for {}: {}", tradingsymbol, exc)
        full_df = None

    if full_df is not None and not full_df.empty:
        all_dtos = []
        for dt, row in full_df.iterrows():
            bar_d = dt.date() if isinstance(dt, datetime | pd.Timestamp) else dt
            all_dtos.append(
                HistoricalBarDTO(
                    tradingsymbol=tradingsymbol,
                    bar_date=bar_d,
                    open=Decimal(str(round(float(row["Open"]), 2))),
                    high=Decimal(str(round(float(row["High"]), 2))),
                    low=Decimal(str(round(float(row["Low"]), 2))),
                    close=Decimal(str(round(float(row["Close"]), 2))),
                    volume=int(row.get("Volume", 0)),
                )
            )
        upsert_historical_bars(all_dtos)
        logger.info("Persisted {} bars for {}", len(all_dtos), tradingsymbol)
        db_bars = get_historical_bars(tradingsymbol, start_date=start_cutoff)
        return _bars_to_dataframe(db_bars)

    # Fallback if yfinance failed but we have stale DB bars
    if db_bars:
        logger.warning("yfinance unavailable; returning stale cached bars for {}", tradingsymbol)
        return _bars_to_dataframe(db_bars)

    logger.error("No historical bars available for {}", tradingsymbol)
    return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


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
