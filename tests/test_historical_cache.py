"""
Tests for src/analytics/historical_cache.py and src/ingestion/ticker_map.py.
Verifies caching, incremental sync, benchmark mapping, and network fault tolerance.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

import pandas as pd

from src.analytics.historical_cache import (
    _bars_to_dataframe,
    get_benchmark_bars,
    get_last_completed_trading_date,
    get_or_sync_historical_bars,
)
from src.ingestion.ticker_map import (
    DEFAULT_BENCHMARK_SYMBOL,
    DEFAULT_BENCHMARK_TICKER,
    get_benchmark_ticker,
    get_yf_ticker,
)
from src.models.dtos import HistoricalBarDTO

# ──────────────────────────────────────────────────────────
# Ticker Map Tests
# ──────────────────────────────────────────────────────────


def test_ticker_map_standard_and_overrides():
    """Verify ticker mapping for standard NSE scrips and special overrides."""
    assert get_yf_ticker("INFY") == "INFY.NS"
    assert get_yf_ticker("RELIANCE") == "RELIANCE.NS"
    assert get_yf_ticker("TCS", exchange="NSE") == "TCS.NS"
    assert get_yf_ticker("TCS", exchange="BSE") == "TCS.BO"

    # Overrides
    assert get_yf_ticker("GOLDCASE") == "GOLDCASE.BO"
    assert get_yf_ticker("ITBEES") == "ITBEES.NS"
    assert get_yf_ticker("GOLDENTOBC-BZ") == "GOLDENTOBC.NS"
    assert get_yf_ticker("NIFTY 50") == "^NSEI"
    assert get_yf_ticker("NIFTY 50 TRI") == "^NSEI"

    # Benchmark helper
    assert get_benchmark_ticker() == DEFAULT_BENCHMARK_TICKER
    assert get_benchmark_ticker(DEFAULT_BENCHMARK_SYMBOL) == "^NSEI"


# ──────────────────────────────────────────────────────────
# Last Completed Trading Day Tests
# ──────────────────────────────────────────────────────────


def test_get_last_completed_trading_date():
    """Verify last completed trading day correctly avoids weekends."""
    # Sunday at 18:00 -> should step back to Friday
    sunday_dt = datetime(2026, 9, 27, 18, 0, 0)
    last_trading = get_last_completed_trading_date(as_of=sunday_dt)
    assert last_trading == date(2026, 9, 25)  # Friday
    assert last_trading.weekday() == 4

    # Monday at 10:00 (market open but not complete) -> should be Friday
    monday_morning = datetime(2026, 9, 28, 10, 0, 0)
    last_trading_mon = get_last_completed_trading_date(as_of=monday_morning)
    assert last_trading_mon == date(2026, 9, 25)

    # Monday at 17:00 (market closed) -> should be Monday
    monday_evening = datetime(2026, 9, 28, 17, 0, 0)
    with patch("src.analytics.historical_cache.is_holiday", return_value=False):
        last_trading_eve = get_last_completed_trading_date(as_of=monday_evening)
        assert last_trading_eve == date(2026, 9, 28)


# ──────────────────────────────────────────────────────────
# Bars to DataFrame Conversion
# ──────────────────────────────────────────────────────────


def test_bars_to_dataframe_empty():
    """Verify empty bars list returns an empty DataFrame with standard columns."""
    df = _bars_to_dataframe([])
    assert isinstance(df, pd.DataFrame)
    assert df.empty
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_bars_to_dataframe_populated():
    """Verify bars conversion produces sorted DatetimeIndex and float columns."""
    bars = [
        HistoricalBarDTO(
            tradingsymbol="INFY",
            bar_date=date(2026, 9, 25),
            open=Decimal("1500.00"),
            high=Decimal("1520.00"),
            low=Decimal("1490.00"),
            close=Decimal("1515.00"),
            volume=1000000,
        ),
        HistoricalBarDTO(
            tradingsymbol="INFY",
            bar_date=date(2026, 9, 24),
            open=Decimal("1480.00"),
            high=Decimal("1505.00"),
            low=Decimal("1475.00"),
            close=Decimal("1495.00"),
            volume=950000,
        ),
    ]
    df = _bars_to_dataframe(bars)
    assert len(df) == 2
    assert df.index[0] == pd.to_datetime("2026-09-24")
    assert df.index[1] == pd.to_datetime("2026-09-25")
    assert df.loc[pd.to_datetime("2026-09-25"), "Close"] == 1515.00


# ──────────────────────────────────────────────────────────
# Historical Cache Sync & Hit Tests
# ──────────────────────────────────────────────────────────


def test_cache_hit_zero_network_calls():
    """When DB has fresh bars up to last completed market day, yfinance must NOT be queried."""
    last_trading_day = date(2026, 9, 25)
    cached_bars = [
        HistoricalBarDTO(
            tradingsymbol="INFY",
            bar_date=date(2026, 9, 24),
            open=Decimal("1480.00"),
            high=Decimal("1505.00"),
            low=Decimal("1475.00"),
            close=Decimal("1495.00"),
            volume=950000,
        ),
        HistoricalBarDTO(
            tradingsymbol="INFY",
            bar_date=date(2026, 9, 25),
            open=Decimal("1500.00"),
            high=Decimal("1520.00"),
            low=Decimal("1490.00"),
            close=Decimal("1515.00"),
            volume=1000000,
        ),
    ]

    with (
        patch(
            "src.analytics.historical_cache.get_last_completed_trading_date",
            return_value=last_trading_day,
        ),
        patch("src.analytics.historical_cache.get_historical_bars", return_value=cached_bars),
        patch("src.analytics.historical_cache._fetch_from_yfinance") as mock_yf,
    ):
        df = get_or_sync_historical_bars("INFY")
        assert len(df) == 2
        assert mock_yf.call_count == 0  # Zero network calls on cache hit


def test_incremental_fetch_when_cache_stale():
    """When DB is missing recent bars, only delta bars are fetched from yfinance."""
    max_cached_date = date(2026, 9, 22)
    last_trading_day = date(2026, 9, 25)

    existing_bars = [
        HistoricalBarDTO(
            tradingsymbol="INFY",
            bar_date=max_cached_date,
            open=Decimal("1470.00"),
            high=Decimal("1485.00"),
            low=Decimal("1465.00"),
            close=Decimal("1480.00"),
            volume=800000,
        )
    ]

    # Mock delta dataframe from yfinance
    delta_data = {
        "Open": [1485.0, 1490.0, 1500.0],
        "High": [1495.0, 1510.0, 1520.0],
        "Low": [1475.0, 1485.0, 1490.0],
        "Close": [1490.0, 1505.0, 1515.0],
        "Volume": [900000, 950000, 1000000],
    }
    delta_dates = pd.to_datetime(["2026-09-23", "2026-09-24", "2026-09-25"])
    mock_delta_df = pd.DataFrame(delta_data, index=delta_dates)

    updated_bars = existing_bars + [
        HistoricalBarDTO(
            tradingsymbol="INFY",
            bar_date=d.date(),
            open=Decimal(str(o)),
            high=Decimal(str(h)),
            low=Decimal(str(low_val)),
            close=Decimal(str(c)),
            volume=v,
        )
        for d, o, h, low_val, c, v in zip(
            delta_dates,
            delta_data["Open"],
            delta_data["High"],
            delta_data["Low"],
            delta_data["Close"],
            delta_data["Volume"],
            strict=True,
        )
    ]

    with (
        patch(
            "src.analytics.historical_cache.get_last_completed_trading_date",
            return_value=last_trading_day,
        ),
        patch(
            "src.analytics.historical_cache.get_historical_bars",
            side_effect=[existing_bars, updated_bars],
        ),
        patch(
            "src.analytics.historical_cache._fetch_from_yfinance", return_value=mock_delta_df
        ) as mock_yf,
        patch("src.analytics.historical_cache.upsert_historical_bars") as mock_upsert,
    ):
        df = get_or_sync_historical_bars("INFY")
        assert len(df) == 4
        assert mock_yf.call_count == 1
        assert mock_upsert.call_count == 1
        # Upsert should have passed 3 delta bars
        upserted_arg = mock_upsert.call_args[0][0]
        assert len(upserted_arg) == 3


def test_cold_start_sync():
    """When DB has zero bars, full history is fetched and persisted."""
    mock_full_data = {
        "Open": [100.0, 102.0],
        "High": [103.0, 105.0],
        "Low": [99.0, 101.0],
        "Close": [102.0, 104.0],
        "Volume": [50000, 60000],
    }
    dates = pd.to_datetime(["2026-09-24", "2026-09-25"])
    mock_df = pd.DataFrame(mock_full_data, index=dates)

    persisted_bars = [
        HistoricalBarDTO(
            tradingsymbol="NEWSTOCK",
            bar_date=d.date(),
            open=Decimal(str(o)),
            high=Decimal(str(h)),
            low=Decimal(str(low_val)),
            close=Decimal(str(c)),
            volume=v,
        )
        for d, o, h, low_val, c, v in zip(
            dates,
            mock_full_data["Open"],
            mock_full_data["High"],
            mock_full_data["Low"],
            mock_full_data["Close"],
            mock_full_data["Volume"],
            strict=True,
        )
    ]

    with (
        patch(
            "src.analytics.historical_cache.get_historical_bars", side_effect=[[], persisted_bars]
        ),
        patch(
            "src.analytics.historical_cache._fetch_from_yfinance", return_value=mock_df
        ) as mock_yf,
        patch("src.analytics.historical_cache.upsert_historical_bars") as mock_upsert,
    ):
        df = get_or_sync_historical_bars("NEWSTOCK")
        assert len(df) == 2
        assert mock_yf.call_count == 1
        assert mock_upsert.call_count == 1


def test_get_benchmark_bars():
    """Verify get_benchmark_bars delegates with DEFAULT_BENCHMARK_SYMBOL."""
    with patch("src.analytics.historical_cache.get_or_sync_historical_bars") as mock_sync:
        mock_sync.return_value = pd.DataFrame({"Close": [24500.0]})
        df = get_benchmark_bars()
        assert not df.empty
        mock_sync.assert_called_once_with(
            DEFAULT_BENCHMARK_SYMBOL,
            lookback_days=750,
            force_sync=False,
        )


def test_yfinance_error_graceful_fallback():
    """When yfinance fails, return stale cached bars rather than crashing."""
    last_trading_day = date(2026, 9, 25)
    stale_bars = [
        HistoricalBarDTO(
            tradingsymbol="INFY",
            bar_date=date(2026, 9, 20),
            open=Decimal("1450.00"),
            high=Decimal("1460.00"),
            low=Decimal("1440.00"),
            close=Decimal("1455.00"),
            volume=500000,
        )
    ]

    with (
        patch(
            "src.analytics.historical_cache.get_last_completed_trading_date",
            return_value=last_trading_day,
        ),
        patch("src.analytics.historical_cache.get_historical_bars", return_value=stale_bars),
        patch(
            "src.analytics.historical_cache._fetch_from_yfinance",
            side_effect=Exception("HTTP 429 Rate Limit"),
        ),
    ):
        df = get_or_sync_historical_bars("INFY")
        assert len(df) == 1
        assert df.iloc[0]["Close"] == 1455.00
