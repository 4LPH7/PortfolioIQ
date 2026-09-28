-- ============================================================
-- Migration 021: Historical Daily Bars Cache
-- Stores historical daily OHLCV bars for equities and benchmarks
-- to support fast, offline walk-forward backtesting without rate limits.
-- ============================================================

CREATE TABLE IF NOT EXISTS historical_daily_bars (
    tradingsymbol               TEXT NOT NULL,
    bar_date                    DATE NOT NULL,
    open                        NUMERIC(15, 2) NOT NULL,
    high                        NUMERIC(15, 2) NOT NULL,
    low                         NUMERIC(15, 2) NOT NULL,
    close                       NUMERIC(15, 2) NOT NULL,
    volume                      BIGINT NOT NULL DEFAULT 0,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (tradingsymbol, bar_date)
);

CREATE INDEX IF NOT EXISTS idx_historical_daily_bars_lookup
    ON historical_daily_bars (tradingsymbol, bar_date DESC);
