-- ============================================================
-- Migration 015: Holdings Reconciliation Log
-- ============================================================

CREATE TABLE IF NOT EXISTS holdings_reconciliation_log (
    id                      BIGSERIAL PRIMARY KEY,
    user_id                 TEXT NOT NULL DEFAULT 'default',
    instrument_token        INTEGER NOT NULL
                            REFERENCES instrument_master(instrument_token) ON DELETE RESTRICT,
    tradingsymbol           TEXT NOT NULL,
    old_quantity            INTEGER NOT NULL,
    new_quantity            INTEGER NOT NULL,
    old_avg_price           NUMERIC(15, 2),
    new_avg_price           NUMERIC(15, 2),
    delta_quantity          INTEGER NOT NULL,
    reconciliation_reason   TEXT NOT NULL
                            CHECK (reconciliation_reason IN (
                                'T1_SETTLEMENT',
                                'TRADE_FILL',
                                'CORPORATE_ACTION_SPLIT',
                                'CORPORATE_ACTION_BONUS',
                                'EXTERNAL_TRANSFER',
                                'INITIAL_SYNC',
                                'DISCREPANCY'
                            )),
    detected_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recon_log_user_token
    ON holdings_reconciliation_log (user_id, instrument_token, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_recon_log_reason
    ON holdings_reconciliation_log (reconciliation_reason, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_recon_log_detected
    ON holdings_reconciliation_log (detected_at DESC);
