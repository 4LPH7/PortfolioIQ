-- ============================================================
-- Migration 003: Holding Tax Lots
-- FIFO-ordered purchase lots for STCG/LTCG classification.
-- Each buy creates a new lot. Sells reduce remaining_quantity FIFO.
-- ============================================================

CREATE TABLE IF NOT EXISTS holding_tax_lots (
    id                  BIGSERIAL PRIMARY KEY,
    holding_id          BIGINT NOT NULL
                        REFERENCES user_holdings(id) ON DELETE RESTRICT,

    -- Lot details
    buy_date            DATE NOT NULL,
    buy_price           NUMERIC(15, 2) NOT NULL,      -- Original purchase price
    adjusted_price      NUMERIC(15, 2),               -- After corporate action adjustments
    quantity            INTEGER NOT NULL,              -- Total shares in this lot
    remaining_quantity  INTEGER NOT NULL,              -- After partial sells (FIFO)

    -- Tax classification — auto-computed, stored for query performance
    -- LTCG threshold: held > 365 days for equity (Indian tax law)
    tax_type            TEXT GENERATED ALWAYS AS (
                            CASE
                                WHEN buy_date < CURRENT_DATE - INTERVAL '365 days'
                                THEN 'LTCG'
                                ELSE 'STCG'
                            END
                        ) STORED
                        CHECK (tax_type IN ('STCG', 'LTCG')),

    -- Corporate action tracking
    split_adjusted      BOOLEAN NOT NULL DEFAULT FALSE,
    bonus_adjusted      BOOLEAN NOT NULL DEFAULT FALSE,
    adjustment_factor   NUMERIC(12, 6) DEFAULT 1.000000,  -- Cumulative split/bonus multiplier

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_remaining_lte_quantity
        CHECK (remaining_quantity >= 0 AND remaining_quantity <= quantity),
    CONSTRAINT chk_positive_qty
        CHECK (quantity > 0),
    CONSTRAINT chk_positive_price
        CHECK (buy_price > 0)
);

-- Index: Lookup all lots for a holding
CREATE INDEX IF NOT EXISTS idx_tax_lots_holding
    ON holding_tax_lots (holding_id);

-- Index: FIFO order — oldest lots first for sell routing
CREATE INDEX IF NOT EXISTS idx_tax_lots_fifo
    ON holding_tax_lots (holding_id, buy_date ASC);

-- Index: Find unsold lots quickly
CREATE INDEX IF NOT EXISTS idx_tax_lots_unsold
    ON holding_tax_lots (holding_id)
    WHERE remaining_quantity > 0;

-- Index: Unsold lots with buy_date (for tax filtering at query time)
-- NOTE: STCG filtering uses CURRENT_DATE which is not IMMUTABLE,
-- so we index on remaining_quantity > 0 and filter STCG in queries.
CREATE INDEX IF NOT EXISTS idx_tax_lots_recent_unsold
    ON holding_tax_lots (holding_id, buy_date)
    WHERE remaining_quantity > 0;
