-- ============================================================
-- Migration 018: Portfolio Cash Flows Ledger
-- Explicit audit ledger for external deposits, withdrawals,
-- dividend cash inflows, and brokerage account adjustments.
-- ============================================================

CREATE TABLE IF NOT EXISTS portfolio_cash_flows (
    id                      BIGSERIAL PRIMARY KEY,
    user_id                 TEXT NOT NULL DEFAULT 'default',
    flow_date               DATE NOT NULL,
    flow_type               TEXT NOT NULL
                            CHECK (flow_type IN (
                                'DEPOSIT',
                                'WITHDRAWAL',
                                'DIVIDEND',
                                'CHARGE',
                                'INTEREST'
                            )),
    amount                  NUMERIC(15, 2) NOT NULL CHECK (amount > 0),

    -- Unit issuance/redemption tracking
    units_affected          NUMERIC(18, 6),
    nav_per_unit            NUMERIC(15, 4),

    -- Audit context
    source                  TEXT NOT NULL DEFAULT 'MANUAL'
                            CHECK (source IN (
                                'MANUAL',
                                'AUTO_MARGIN_SYNC',
                                'CORPORATE_ACTION',
                                'BROKER_LEDGER'
                            )),
    external_reference      TEXT,
    notes                   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cash_flows_user_date
    ON portfolio_cash_flows (user_id, flow_date ASC);

CREATE INDEX IF NOT EXISTS idx_cash_flows_type_date
    ON portfolio_cash_flows (flow_type, flow_date DESC);
