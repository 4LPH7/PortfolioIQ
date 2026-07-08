-- ============================================================
-- Migration 008: Order Audit Trail + Validation Log
-- IMMUTABLE append-only ledger of every order attempt.
-- Includes per-check validation detail rows.
-- ============================================================

-- Primary audit table: one row per order attempt
CREATE TABLE IF NOT EXISTS order_audit_trail (
    id                      BIGSERIAL PRIMARY KEY,

    -- Order identity
    internal_order_id       UUID NOT NULL DEFAULT gen_random_uuid(),
    kite_order_id           TEXT,                 -- Populated after broker execution
    user_id                 TEXT NOT NULL DEFAULT 'default',

    -- Instrument
    instrument_token        INTEGER NOT NULL
                            REFERENCES instrument_master(instrument_token) ON DELETE RESTRICT,
    tradingsymbol           TEXT NOT NULL,
    exchange                TEXT NOT NULL DEFAULT 'NSE',

    -- Order parameters (exact Kite place_order() fields)
    transaction_type        TEXT NOT NULL
                            CHECK (transaction_type IN ('BUY', 'SELL')),
    order_type              TEXT NOT NULL DEFAULT 'MARKET'
                            CHECK (order_type IN ('MARKET', 'LIMIT', 'SL', 'SL-M')),
    variety                 TEXT NOT NULL DEFAULT 'regular'
                            CHECK (variety IN ('regular', 'amo', 'iceberg', 'auction')),
    product                 TEXT NOT NULL DEFAULT 'CNC'
                            CHECK (product IN ('CNC', 'MIS', 'NRML', 'MTF')),
    validity                TEXT NOT NULL DEFAULT 'DAY'
                            CHECK (validity IN ('DAY', 'IOC')),

    -- Quantities & prices
    requested_quantity      INTEGER NOT NULL,
    executed_quantity       INTEGER DEFAULT 0,
    requested_price         NUMERIC(15, 2),       -- For LIMIT orders
    trigger_price           NUMERIC(15, 2),       -- For SL orders
    execution_price         NUMERIC(15, 2),       -- Actual fill price from broker

    -- Reference price at signal time (for slippage calculation)
    price_at_signal         NUMERIC(15, 2) NOT NULL,

    -- Gatekeeper result
    validation_status       TEXT NOT NULL DEFAULT 'PENDING'
                            CHECK (validation_status IN (
                                'PENDING',        -- Awaiting checks
                                'APPROVED',       -- All checks passed
                                'BLOCKED',        -- Failed one or more checks
                                'DRY_RUN'         -- Simulated only
                            )),

    -- Broker execution status (synced from Kite order updates)
    broker_status           TEXT
                            CHECK (broker_status IN (
                                'NOT_SENT', 'OPEN', 'COMPLETE',
                                'REJECTED', 'CANCELLED',
                                'TRIGGER PENDING', 'MODIFY PENDING',
                                NULL
                            )),
    broker_status_message   TEXT,

    -- Who triggered this order
    trigger_source          TEXT NOT NULL DEFAULT 'MANUAL'
                            CHECK (trigger_source IN ('MANUAL', 'REBALANCER', 'SCHEDULER')),

    -- Rebalancing context
    rebalance_session_id    UUID,
    target_weight_pct       NUMERIC(6, 3),
    current_weight_pct      NUMERIC(6, 3),
    drift_pct               NUMERIC(6, 3),

    -- Tax context
    tax_type                TEXT
                            CHECK (tax_type IN ('STCG', 'LTCG', 'NA', NULL)),
    estimated_tax_impact    NUMERIC(18, 2),

    -- Dry-run mode flag at time of order
    is_dry_run              BOOLEAN NOT NULL DEFAULT TRUE,

    -- Timestamps
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    validated_at            TIMESTAMPTZ,
    executed_at             TIMESTAMPTZ,

    -- Additional context
    notes                   TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_audit_user_date
    ON order_audit_trail (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_dedup
    ON order_audit_trail (user_id, instrument_token, transaction_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_rebalance_session
    ON order_audit_trail (rebalance_session_id)
    WHERE rebalance_session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_audit_pending
    ON order_audit_trail (user_id, created_at DESC)
    WHERE validation_status = 'PENDING' OR broker_status = 'OPEN';

-- ============================================================
-- Per-check validation detail rows (one per rule checked)
-- ============================================================
CREATE TABLE IF NOT EXISTS order_validation_log (
    id              BIGSERIAL PRIMARY KEY,
    audit_id        BIGINT NOT NULL
                    REFERENCES order_audit_trail(id) ON DELETE RESTRICT,

    check_name      TEXT NOT NULL
                    CHECK (check_name IN (
                        'MARGIN_CHECK',
                        'SLIPPAGE_CHECK',
                        'CONCENTRATION_CHECK',
                        'DUPLICATE_CHECK',
                        'MARKET_HOURS_CHECK',
                        'QUANTITY_CHECK',
                        'TAX_WARNING'
                    )),
    passed          BOOLEAN NOT NULL,

    -- Human-readable context for the Audit Log UI
    expected_value  TEXT,                         -- e.g., 'Required: ₹75,000'
    actual_value    TEXT,                         -- e.g., 'Available: ₹50,000'
    message         TEXT NOT NULL,               -- e.g., 'BLOCKED: Insufficient margin'

    checked_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_validation_audit
    ON order_validation_log (audit_id);
