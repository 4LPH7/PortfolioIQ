-- ============================================================
-- Migration 001: Instrument Master
-- The canonical registry mapping Zerodha tokens to Yahoo tickers.
-- Refreshed daily ~8:30 AM IST from Kite instruments API.
-- ============================================================

CREATE TABLE IF NOT EXISTS instrument_master (
    -- Primary identifier (Kite Connect 32-bit int, stable for equity)
    instrument_token    INTEGER PRIMARY KEY,

    -- Zerodha identifiers
    exchange_token      INTEGER NOT NULL,
    tradingsymbol       TEXT NOT NULL,            -- e.g., 'RELIANCE', 'M&M'
    name                TEXT,                     -- Full company name
    isin                TEXT,                     -- e.g., 'INE002A01018'

    -- Yahoo Finance mapping (built from instrument_master.csv seed)
    yf_ticker           TEXT,                     -- e.g., 'RELIANCE.NS', 'M&M.NS'

    -- Instrument metadata
    exchange            TEXT NOT NULL DEFAULT 'NSE'
                        CHECK (exchange IN ('NSE', 'BSE', 'NFO', 'MCX', 'CDS', 'BFO')),
    instrument_type     TEXT NOT NULL DEFAULT 'EQ'
                        CHECK (instrument_type IN ('EQ', 'FUT', 'CE', 'PE', 'BE')),
    segment             TEXT,                     -- e.g., 'NSE', 'NFO-FUT'
    tick_size           NUMERIC(8, 4),            -- Minimum price movement
    lot_size            INTEGER DEFAULT 1,

    -- Classification (used for drift detection & sector allocation)
    sector              TEXT,                     -- e.g., 'IT', 'Financials', 'Healthcare'
    industry            TEXT,                     -- e.g., 'Software Services'
    market_cap_category TEXT
                        CHECK (market_cap_category IN ('LARGE', 'MID', 'SMALL', NULL)),

    -- Lifecycle
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    last_synced_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Uniqueness: stable lookup even when token refreshes
    CONSTRAINT uq_instrument_exchange_symbol
        UNIQUE (exchange, tradingsymbol)
);

-- Index: Yahoo Finance ticker lookups (polling daemon)
CREATE INDEX IF NOT EXISTS idx_instrument_yf_ticker
    ON instrument_master (yf_ticker)
    WHERE yf_ticker IS NOT NULL;

-- Index: ISIN lookups (corporate actions cross-referencing)
CREATE INDEX IF NOT EXISTS idx_instrument_isin
    ON instrument_master (isin)
    WHERE isin IS NOT NULL;

-- Index: Sector-based queries (drift detection)
CREATE INDEX IF NOT EXISTS idx_instrument_sector
    ON instrument_master (sector)
    WHERE sector IS NOT NULL;

-- Index: Active instruments only (polling filter)
CREATE INDEX IF NOT EXISTS idx_instrument_active
    ON instrument_master (is_active)
    WHERE is_active = TRUE;
