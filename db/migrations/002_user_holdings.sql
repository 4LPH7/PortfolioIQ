-- ============================================================
-- Migration 002: User Holdings
-- Broker-reported holdings. Upserted daily from Kite at 9:15 AM.
-- Stores the complete Kite holdings() API response fields.
-- ============================================================

CREATE TABLE IF NOT EXISTS user_holdings (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             TEXT NOT NULL DEFAULT 'default',

    -- Instrument reference
    instrument_token    INTEGER NOT NULL
                        REFERENCES instrument_master(instrument_token) ON DELETE RESTRICT,
    tradingsymbol       TEXT NOT NULL,
    exchange            TEXT NOT NULL DEFAULT 'NSE',
    isin                TEXT,

    -- Quantity breakdown (exact Kite holdings API fields)
    quantity            INTEGER NOT NULL DEFAULT 0,   -- T+2 settled
    t1_quantity         INTEGER NOT NULL DEFAULT 0,   -- T+1 unsettled
    opening_quantity    INTEGER NOT NULL DEFAULT 0,   -- Qty at day start
    used_quantity       INTEGER NOT NULL DEFAULT 0,   -- Blocked / pledged
    authorised_quantity INTEGER NOT NULL DEFAULT 0,   -- Authorised for e-DIS sale
    collateral_quantity INTEGER NOT NULL DEFAULT 0,   -- Pledged as margin

    -- Pricing from Kite sync
    average_price       NUMERIC(15, 2) NOT NULL,      -- Avg buy price
    last_price          NUMERIC(15, 2),               -- LTP at sync time
    close_price         NUMERIC(15, 2),               -- Previous day close

    -- Computed at sync time (from Kite)
    pnl                 NUMERIC(18, 2),               -- Broker-reported P&L
    day_change          NUMERIC(15, 2),               -- Absolute day change
    day_change_pct      NUMERIC(8, 4),               -- Percentage day change

    -- Product type
    product             TEXT NOT NULL DEFAULT 'CNC'
                        CHECK (product IN ('CNC', 'MIS', 'NRML', 'MTF')),

    -- Tax classification anchor date
    first_buy_date      DATE,                         -- For STCG/LTCG boundary

    -- Discrepancy flag from broker
    has_discrepancy     BOOLEAN NOT NULL DEFAULT FALSE,

    -- Lifecycle
    last_synced_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One holding row per user / instrument / product type
    CONSTRAINT uq_holding_user_instrument
        UNIQUE (user_id, instrument_token, product)
);

-- Index: Main dashboard query
CREATE INDEX IF NOT EXISTS idx_holdings_user
    ON user_holdings (user_id);

-- Index: Join with live_prices
CREATE INDEX IF NOT EXISTS idx_holdings_instrument
    ON user_holdings (instrument_token);

-- Index: Non-zero holdings only (Streamlit display filter)
CREATE INDEX IF NOT EXISTS idx_holdings_active
    ON user_holdings (user_id)
    WHERE (quantity + t1_quantity) > 0;

-- Trigger: Auto-update updated_at on row change
CREATE OR REPLACE FUNCTION update_holdings_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_holdings_updated_at ON user_holdings;
CREATE TRIGGER trg_holdings_updated_at
    BEFORE UPDATE ON user_holdings
    FOR EACH ROW EXECUTE FUNCTION update_holdings_timestamp();
