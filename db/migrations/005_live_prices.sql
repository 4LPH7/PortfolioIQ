-- ============================================================
-- Migration 005: Live Prices
-- Latest price snapshot per instrument from Yahoo Finance.
-- Updated every ~50 seconds during market hours via UPSERT.
-- fillfactor=70 reserves 30% page space for HOT (Heap-Only Tuple)
-- in-place updates, avoiding index churn on every write.
-- ============================================================

CREATE TABLE IF NOT EXISTS live_prices (
    instrument_token    INTEGER PRIMARY KEY
                        REFERENCES instrument_master(instrument_token) ON DELETE RESTRICT,

    -- Price data (from Yahoo Finance yf.Ticker().info)
    last_price          NUMERIC(15, 2) NOT NULL,
    open_price          NUMERIC(15, 2),
    high_price          NUMERIC(15, 2),
    low_price           NUMERIC(15, 2),
    close_price         NUMERIC(15, 2),           -- Previous day's close
    volume              BIGINT,

    -- Computed day change (updated on each poll)
    change_absolute     NUMERIC(15, 2),            -- last_price - close_price
    change_percent      NUMERIC(8, 4),             -- ((last - close) / close) * 100

    -- Additional market data (available from yf.info)
    week_52_high        NUMERIC(15, 2),
    week_52_low         NUMERIC(15, 2),
    market_cap          BIGINT,
    beta                NUMERIC(8, 4),

    -- Data quality tracking
    source              TEXT NOT NULL DEFAULT 'yahoo'
                        CHECK (source IN ('yahoo', 'kite', 'manual')),
    is_stale            BOOLEAN NOT NULL DEFAULT FALSE,  -- True if poll failed

    last_updated        TIMESTAMPTZ NOT NULL DEFAULT NOW()

) WITH (fillfactor = 70);  -- HOT update optimization for high-frequency UPSERTs

-- NOTE: The PRIMARY KEY index on instrument_token serves as the
-- UPSERT conflict target. No additional indexes needed — this
-- table is always accessed by primary key.

-- Function: Mark prices stale if not updated within 2x polling interval
CREATE OR REPLACE FUNCTION mark_stale_prices()
RETURNS void AS $$
BEGIN
    UPDATE live_prices
    SET is_stale = TRUE
    WHERE last_updated < NOW() - INTERVAL '2 minutes'
      AND is_stale = FALSE;
END;
$$ LANGUAGE plpgsql;
