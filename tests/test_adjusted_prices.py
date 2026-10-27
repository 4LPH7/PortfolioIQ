"""
Corporate-action-adjusted prices (migration 024) and the on-demand backtest call.

The cache keeps the traded close and Yahoo's adjusted close side by side; return maths reads the
adjusted series, price displays read the traded one, and a new dividend restates history.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from src.analytics.historical_cache import (
    PRICE_BASIS,
    _bars_to_dataframe,
    _df_to_dtos,
    _fetch_from_yahoo_chart_api,
    backfill_adjusted_prices,
    get_or_sync_historical_bars,
    raw_close,
)
from src.models.dtos import HistoricalBarDTO


def _bar(d, close, adj=None, basis=None):
    c = Decimal(str(close))
    return HistoricalBarDTO(
        tradingsymbol="ITC",
        bar_date=d,
        open=c,
        high=c,
        low=c,
        close=c,
        volume=100,
        adj_close=Decimal(str(adj)) if adj is not None else None,
        price_basis=basis,
    )


def test_dataframe_is_adjusted_and_keeps_traded_close():
    bars = [
        _bar(date(2026, 9, 1), 400.0, 380.0, PRICE_BASIS),  # before a ₹20 dividend
        _bar(date(2026, 9, 2), 380.0, 380.0, PRICE_BASIS),  # ex-date: traded price drops
    ]
    df = _bars_to_dataframe(bars)
    # Adjusted: no fake 5% loss across the ex-date
    assert df["Close"].tolist() == [380.0, 380.0]
    assert df["Open"].iloc[0] == 380.0  # OHLC scaled by the same factor
    # Traded prices survive for displays and cost comparisons
    assert raw_close(df).tolist() == [400.0, 380.0]


def test_legacy_rows_inherit_the_earliest_known_factor():
    bars = [
        _bar(date(2026, 8, 31), 410.0),  # cached before migration 024: no factor
        _bar(date(2026, 9, 1), 400.0, 380.0, PRICE_BASIS),
    ]
    df = _bars_to_dataframe(bars)
    assert np.isclose(df["Close"].iloc[0], 410.0 * 0.95)


def test_rows_without_any_factor_behave_as_before():
    df = _bars_to_dataframe([_bar(date(2026, 9, 1), 400.0), _bar(date(2026, 9, 2), 404.0)])
    assert df["Close"].tolist() == [400.0, 404.0]


def test_raw_close_falls_back_for_frames_without_it():
    assert raw_close(pd.DataFrame({"Close": [1.0, 2.0]})).tolist() == [1.0, 2.0]


def test_chart_api_reads_adjusted_close_and_never_invents_one():
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1790000000, 1790086400],
                    "indicators": {
                        "quote": [
                            {
                                "open": [400.0, 380.0],
                                "high": [401.0, 381.0],
                                "low": [399.0, 379.0],
                                "close": [400.0, 380.0],
                                "volume": [10, 20],
                            }
                        ],
                        "adjclose": [{"adjclose": [380.0, None]}],
                    },
                }
            ]
        }
    }
    resp = MagicMock(status_code=200)
    resp.json.return_value = payload
    with patch("requests.get", return_value=resp):
        df = _fetch_from_yahoo_chart_api("ITC.NS")
    assert df["Adj Close"].iloc[0] == 380.0
    assert np.isnan(df["Adj Close"].iloc[1])
    dtos = _df_to_dtos("ITC", df)
    assert dtos[0].adj_close == Decimal("380.0") and dtos[0].price_basis == PRICE_BASIS
    assert dtos[1].adj_close is None and dtos[1].price_basis is None  # queued for backfill


def test_new_dividend_restates_history_with_a_full_refetch():
    cached = [_bar(date(2026, 9, 1), 400.0, 400.0, PRICE_BASIS)]
    # Same day, but Yahoo now reports a lower adjusted close: a dividend went ex since
    delta = pd.DataFrame(
        {
            "Open": [400.0, 390.0],
            "High": [400.0, 390.0],
            "Low": [400.0, 390.0],
            "Close": [400.0, 390.0],
            "Adj Close": [390.0, 390.0],
            "Volume": [1, 1],
        },
        index=pd.to_datetime(["2026-09-01", "2026-09-02"]),
    )
    with (
        patch(
            "src.analytics.historical_cache.get_last_completed_trading_date",
            return_value=date(2026, 9, 2),
        ),
        patch("src.analytics.historical_cache.get_historical_bars", return_value=cached),
        patch("src.analytics.historical_cache._fetch_from_yfinance", return_value=delta) as fetch,
        patch("src.analytics.historical_cache.upsert_historical_bars"),
        patch("src.analytics.historical_cache.mark_bars_legacy_before") as mark_legacy,
    ):
        get_or_sync_historical_bars("ITC")
    # Delta fetch, then a full 10y refetch; older rows are relabelled once
    assert fetch.call_count == 2
    assert fetch.call_args_list[1].kwargs == {"period": "10y"}
    mark_legacy.assert_called_once_with("ITC", date(2026, 9, 1))


def test_unchanged_factor_appends_only_new_days():
    cached = [_bar(date(2026, 9, 1), 400.0, 380.0, PRICE_BASIS)]
    delta = pd.DataFrame(
        {
            "Open": [400.0, 402.0],
            "High": [400.0, 402.0],
            "Low": [400.0, 402.0],
            "Close": [400.0, 402.0],
            "Adj Close": [380.0, 381.9],
            "Volume": [1, 1],
        },
        index=pd.to_datetime(["2026-09-01", "2026-09-02"]),
    )
    with (
        patch(
            "src.analytics.historical_cache.get_last_completed_trading_date",
            return_value=date(2026, 9, 2),
        ),
        patch("src.analytics.historical_cache.get_historical_bars", return_value=cached),
        patch("src.analytics.historical_cache._fetch_from_yfinance", return_value=delta) as fetch,
        patch("src.analytics.historical_cache.upsert_historical_bars") as upsert,
    ):
        get_or_sync_historical_bars("ITC")
    assert fetch.call_count == 1
    assert [d.bar_date for d in upsert.call_args[0][0]] == [date(2026, 9, 2)]


def test_backfill_refetches_queued_symbols_and_counts_failures():
    frames = {"ITC": pd.DataFrame({"Close": [1.0]}), "DEAD": pd.DataFrame()}
    with (
        patch(
            "src.analytics.historical_cache.get_symbols_with_unadjusted_bars",
            return_value=["ITC", "DEAD"],
        ),
        patch(
            "src.analytics.historical_cache.get_or_sync_historical_bars",
            side_effect=lambda s, force_sync: frames[s],
        ) as sync,
    ):
        result = backfill_adjusted_prices()
    assert result == {"refetched": 1, "failed": 1}
    assert all(c.kwargs["force_sync"] for c in sync.call_args_list)


def test_on_demand_backtest_reaches_the_real_engine():
    """Regression: run_and_record_backtest passed stock_df=/bench_df= and always raised."""
    from src.analytics.signal_recorder import run_and_record_backtest

    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2023-01-02", periods=600)
    prices = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, len(idx))))
    df = pd.DataFrame(
        {"Open": prices, "High": prices, "Low": prices, "Close": prices, "Volume": 1000},
        index=idx,
    )
    with patch("src.analytics.signal_recorder.record_backtest_run") as record:
        run = run_and_record_backtest("SYNTH", stock_df=df, bench_df=df.copy())
    assert run.tradingsymbol == "SYNTH"
    record.assert_called_once()


def test_refetch_removes_holiday_bars_and_keeps_trading_day_gaps():
    """Older builds wrote synthetic bars on exchange holidays; a refetch clears them."""
    from src.analytics.historical_cache import _reconcile_missing_days

    cached = [
        _bar(date(2026, 4, 30), 400.0, 400.0, PRICE_BASIS),
        _bar(date(2026, 5, 1), 400.0),  # Maharashtra Day: no NSE session
        _bar(date(2026, 5, 4), 401.0),  # a Monday the source skipped: keep it
        _bar(date(2026, 5, 5), 402.0, 402.0, PRICE_BASIS),
    ]
    fetched = {date(2026, 4, 30), date(2026, 5, 5)}
    with (
        patch("src.analytics.historical_cache.get_historical_bars", return_value=cached),
        patch(
            "src.analytics.historical_cache.is_holiday",
            side_effect=lambda d: d == date(2026, 5, 1),
        ),
        patch("src.analytics.historical_cache.delete_cached_bars", return_value=1) as delete,
        patch("src.analytics.historical_cache.mark_bars_legacy_on") as legacy,
    ):
        _reconcile_missing_days("ITC", date(2026, 4, 30), fetched)
    delete.assert_called_once_with("ITC", [date(2026, 5, 1)])
    legacy.assert_called_once_with("ITC", [date(2026, 5, 4)])
