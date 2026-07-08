-- ============================================================
-- Migration 011: Corporate Actions
-- Tracks stock splits, bonus issues, and dividends from Yahoo Finance.
-- Used to normalize historical average_price in holding_tax_lots
-- and prevent artificial P&L crashes after corporate events.
-- ============================================================

CREATE TABLE IF NOT EXISTS corporate_actions (
    id                  BIGSERIAL PRIMARY KEY,
    instrument_token    INTEGER NOT NULL
                        REFERENCES instrument_master(instrument_token) ON DELETE RESTRICT,
    tradingsymbol       TEXT NOT NULL,
    isin                TEXT,

    -- Action classification
    action_type         TEXT NOT NULL
                        CHECK (action_type IN ('SPLIT', 'BONUS', 'DIVIDEND', 'RIGHTS')),

    -- Key dates
    ex_date             DATE NOT NULL,            -- Position on this date qualifies
    record_date         DATE,                     -- Record date (if available)
    payment_date        DATE,                     -- For dividends

    -- Split / Bonus ratio (e.g., 2-for-1 split: ratio_from=1, ratio_to=2)
    -- Stored as integer ratio components for exact arithmetic
    ratio_from          INTEGER,                  -- Old shares
    ratio_to            INTEGER,                  -- New shares

    -- Adjustment factor: ratio_to / ratio_from
    -- Prices pre-split must be DIVIDED by this factor
    -- e.g., 2-for-1 split → factor=2.0, old ₹1000 → adjusted ₹500
    adjustment_factor   NUMERIC(12, 6),

    -- Dividend details (when action_type = 'DIVIDEND')
    dividend_per_share  NUMERIC(15, 2),

    -- Processing state
    is_applied          BOOLEAN NOT NULL DEFAULT FALSE,
    applied_at          TIMESTAMPTZ,
    applied_to_lots     INTEGER DEFAULT 0,        -- Count of tax lots adjusted

    -- Source
    source              TEXT NOT NULL DEFAULT 'yahoo'
                        CHECK (source IN ('yahoo', 'nse', 'manual')),
    raw_data            JSONB,                    -- Original API response for audit

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Prevent duplicate corporate action entries
    CONSTRAINT uq_corp_action_date_type
        UNIQUE (instrument_token, action_type, ex_date)
);

-- Index: Find unapplied corporate actions to process
CREATE INDEX IF NOT EXISTS idx_corp_actions_pending
    ON corporate_actions (is_applied, ex_date)
    WHERE is_applied = FALSE;

-- Index: Corporate action history for an instrument
CREATE INDEX IF NOT EXISTS idx_corp_actions_instrument
    ON corporate_actions (instrument_token, ex_date DESC);
