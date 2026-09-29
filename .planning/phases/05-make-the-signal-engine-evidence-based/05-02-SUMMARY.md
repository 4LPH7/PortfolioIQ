---
phase: 05-make-the-signal-engine-evidence-based
plan: 02
status: complete
commits: 1
completed_at: 2026-09-29T10:27:00Z
---

# Plan 05-02: Historical Price Caching & Incremental Ingestion Summary

## Overview

Plan 05-02 established a local historical OHLCV bar cache backed by PostgreSQL (`historical_daily_bars`), standardized Zerodha-to-Yahoo Finance ticker mapping, and built an incremental bar synchronization mechanism that eliminates external rate-limit errors and network latency during walk-forward backtesting.

## Key Accomplishments

1. **Standardized Ticker Mapping (`src/ingestion/ticker_map.py`):**
   - Centralized mapping rules for Zerodha tradingsymbols to Yahoo Finance tickers.
   - Handled special scrips and BSE-only overrides (e.g. `GOLDCASE -> GOLDCASE.BO`, `ITBEES -> ITBEES.NS`, `GOLDENTOBC-BZ -> GOLDENTOBC.NS`).
   - Standardized benchmark index symbol mapping (`NIFTY 50 TRI -> ^NSEI`).
   - Exposed `get_yf_ticker()` and `get_benchmark_ticker()`.

2. **Historical Price Caching & Incremental Sync (`src/analytics/historical_cache.py`):**
   - Built `get_or_sync_historical_bars()`:
     - **Cache Hit:** If cached bars exist covering up to the last completed market trading day, returns standard OHLCV `pd.DataFrame` with zero external network requests (0 ms network cost).
     - **Incremental Fetch:** If cache is stale, calculates `delta_start = max_cached_date + 1 day` and only requests missing bars from `yfinance`, upserting delta records into `historical_daily_bars`.
     - **Cold Start:** Automatically pulls full history on first request and persists to PostgreSQL.
     - **Fault Tolerance:** If `yfinance` encounters HTTP 429 rate limits or network errors, catches exceptions gracefully and returns available cached bars.
   - Built `get_last_completed_trading_date()`: Automatically accounts for IST market trading hours, weekends, and database-backed NSE trading holidays.
   - Built `get_benchmark_bars()`: Helper dedicated to caching and fetching NIFTY 50 TRI benchmark daily bars.

3. **Verification & Testing (`tests/test_historical_cache.py`):**
   - Verified standard ticker mappings and overrides.
   - Verified `get_last_completed_trading_date()` holiday and weekend avoidance.
   - Verified cache hit with zero network calls.
   - Verified incremental delta fetch and cold start ingestion.
   - Verified graceful fallback under external network and rate-limit errors.
   - All 252 tests in project test suite passing with 100% safety-critical coverage.
