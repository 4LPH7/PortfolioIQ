-- Keep open Kite positions and statement ledger rows separate from settled holdings.
ALTER TABLE user_holdings
    ADD COLUMN IF NOT EXISTS data_source TEXT NOT NULL DEFAULT 'legacy';

CREATE TABLE IF NOT EXISTS user_positions (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             TEXT NOT NULL DEFAULT 'default',
    instrument_token    INTEGER NOT NULL REFERENCES instrument_master(instrument_token) ON DELETE RESTRICT,
    tradingsymbol       TEXT NOT NULL,
    exchange            TEXT NOT NULL DEFAULT 'NSE',
    product             TEXT NOT NULL CHECK (product IN ('CNC', 'MIS', 'NRML', 'MTF')),
    quantity            INTEGER NOT NULL,
    average_price       NUMERIC(15, 2) NOT NULL DEFAULT 0,
    last_price          NUMERIC(15, 2),
    pnl                 NUMERIC(18, 2),
    day_change           NUMERIC(15, 2),
    day_change_pct       NUMERIC(8, 4),
    data_source          TEXT NOT NULL DEFAULT 'KITE',
    last_synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_position_user_instrument_product UNIQUE (user_id, instrument_token, product)
);

CREATE INDEX IF NOT EXISTS idx_positions_user_active
    ON user_positions (user_id, tradingsymbol)
    WHERE quantity <> 0;

CREATE TABLE IF NOT EXISTS broker_ledger_entries (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             TEXT NOT NULL DEFAULT 'default',
    import_hash         CHAR(64) NOT NULL,
    posting_date        DATE,
    particulars         TEXT NOT NULL DEFAULT '',
    cost_center         TEXT NOT NULL DEFAULT '',
    voucher_type        TEXT NOT NULL DEFAULT '',
    debit               NUMERIC(20, 6) NOT NULL DEFAULT 0,
    credit              NUMERIC(20, 6) NOT NULL DEFAULT 0,
    net_balance         NUMERIC(20, 6),
    entry_kind          TEXT NOT NULL DEFAULT 'OTHER',
    source_file         TEXT NOT NULL DEFAULT '',
    imported_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_broker_ledger_import UNIQUE (user_id, import_hash)
);

CREATE INDEX IF NOT EXISTS idx_broker_ledger_user_date
    ON broker_ledger_entries (user_id, posting_date DESC, id DESC);
