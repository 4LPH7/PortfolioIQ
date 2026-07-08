-- ============================================================
-- Migration 004: User Margins
-- Real-time cash and margin snapshot from Kite margins API.
-- Upserted at SOD and re-read by Gatekeeper before each order.
-- ============================================================

CREATE TABLE IF NOT EXISTS user_margins (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             TEXT NOT NULL DEFAULT 'default',
    segment             TEXT NOT NULL DEFAULT 'equity'
                        CHECK (segment IN ('equity', 'commodity')),

    -- Available funds (kite.margins()['equity']['available'])
    available_cash          NUMERIC(18, 2) NOT NULL DEFAULT 0,
    available_collateral    NUMERIC(18, 2) NOT NULL DEFAULT 0,
    opening_balance         NUMERIC(18, 2) NOT NULL DEFAULT 0,
    live_balance            NUMERIC(18, 2) NOT NULL DEFAULT 0,
    intraday_payin          NUMERIC(18, 2) NOT NULL DEFAULT 0,
    adhoc_margin            NUMERIC(18, 2) NOT NULL DEFAULT 0,

    -- Utilised funds (kite.margins()['equity']['utilised'])
    utilised_debits         NUMERIC(18, 2) NOT NULL DEFAULT 0,
    utilised_exposure       NUMERIC(18, 2) NOT NULL DEFAULT 0,
    utilised_span           NUMERIC(18, 2) NOT NULL DEFAULT 0,
    option_premium          NUMERIC(18, 2) NOT NULL DEFAULT 0,
    holding_sales           NUMERIC(18, 2) NOT NULL DEFAULT 0,
    turnover                NUMERIC(18, 2) NOT NULL DEFAULT 0,
    m2m_realised            NUMERIC(18, 2) NOT NULL DEFAULT 0,
    m2m_unrealised          NUMERIC(18, 2) NOT NULL DEFAULT 0,
    payout                  NUMERIC(18, 2) NOT NULL DEFAULT 0,

    -- Net available (kite.margins()['equity']['net'])
    net                     NUMERIC(18, 2) NOT NULL DEFAULT 0,

    -- When this snapshot was pulled from broker
    synced_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One row per user per segment (upserted)
    CONSTRAINT uq_margin_user_segment
        UNIQUE (user_id, segment)
);

-- Index: Gatekeeper reads margin before every order
CREATE INDEX IF NOT EXISTS idx_margins_user_segment
    ON user_margins (user_id, segment);
