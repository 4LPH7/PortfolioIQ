-- PortfolioIQ — Complete Database Schema for Supabase
-- Paste this entire file into: Supabase Dashboard → SQL Editor → Run
-- Generated: 2026-07-10 10:46 IST
-- ============================================================

-- ─────────────────────────────────────────────
-- Migration: 001_instrument_master.sql
-- ─────────────────────────────────────────────
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


-- ─────────────────────────────────────────────
-- Migration: 002_user_holdings.sql
-- ─────────────────────────────────────────────
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


-- ─────────────────────────────────────────────
-- Migration: 003_holding_tax_lots.sql
-- ─────────────────────────────────────────────
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

    -- Tax classification â€” auto-computed, stored for query performance
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

-- Index: FIFO order â€” oldest lots first for sell routing
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


-- ─────────────────────────────────────────────
-- Migration: 004_user_margins.sql
-- ─────────────────────────────────────────────
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


-- ─────────────────────────────────────────────
-- Migration: 005_live_prices.sql
-- ─────────────────────────────────────────────
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
-- UPSERT conflict target. No additional indexes needed â€” this
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


-- ─────────────────────────────────────────────
-- Migration: 006_price_history.sql
-- ─────────────────────────────────────────────
-- ============================================================
-- Migration 006: Price History
-- Append-only time-series archive of intraday price snapshots.
-- Partitioned by date (RANGE) for efficient data retention.
-- Used for EOD Tableau exports and historical analytics.
-- ============================================================

CREATE TABLE IF NOT EXISTS price_history (
    id                  BIGSERIAL,
    instrument_token    INTEGER NOT NULL,           -- Intentionally no FK (partition tables)

    -- Price snapshot
    last_price          NUMERIC(15, 2) NOT NULL,
    open_price          NUMERIC(15, 2),
    high_price          NUMERIC(15, 2),
    low_price           NUMERIC(15, 2),
    close_price         NUMERIC(15, 2),
    volume              BIGINT,
    change_percent      NUMERIC(8, 4),

    -- Metadata
    source              TEXT NOT NULL DEFAULT 'yahoo',
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Partition key must be part of the primary key
    PRIMARY KEY (id, recorded_at)
) PARTITION BY RANGE (recorded_at);

-- BRIN index: Compact time-range index for append-only data
-- (BRIN = Block Range INdex, ideal when data is physically ordered by time)
CREATE INDEX IF NOT EXISTS idx_price_history_ts_brin
    ON price_history USING BRIN (recorded_at)
    WITH (pages_per_range = 32);

-- B-tree composite index: Lookup price history for a specific instrument
CREATE INDEX IF NOT EXISTS idx_price_history_token_ts
    ON price_history (instrument_token, recorded_at DESC);

-- ============================================================
-- Create initial partitions (today + next 2 days as safety margin)
-- The partition management function (migration 013) handles
-- ongoing partition creation via APScheduler.
-- ============================================================

DO $$
DECLARE
    target_date DATE;
    partition_name TEXT;
BEGIN
    FOR i IN 0..2 LOOP
        target_date := CURRENT_DATE + (i || ' days')::INTERVAL;
        partition_name := 'price_history_' || to_char(target_date, 'YYYY_MM_DD');

        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF price_history
             FOR VALUES FROM (%L) TO (%L)',
            partition_name,
            target_date,
            target_date + INTERVAL '1 day'
        );

        RAISE NOTICE 'Created partition: %', partition_name;
    END LOOP;
END;
$$;


-- ─────────────────────────────────────────────
-- Migration: 007_target_allocations.sql
-- ─────────────────────────────────────────────
-- ============================================================
-- Migration 007: Target Allocations
-- Defines named allocation profiles and per-sector/stock targets.
-- The drift detector compares live weights against these targets.
-- ============================================================

-- Named allocation strategies (can have multiple profiles, one active)
CREATE TABLE IF NOT EXISTS allocation_profiles (
    id                  SERIAL PRIMARY KEY,
    user_id             TEXT NOT NULL DEFAULT 'default',
    profile_name        TEXT NOT NULL,
    description         TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_profile_user_name
        UNIQUE (user_id, profile_name)
);

-- Enforce only one active profile per user at the database level
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_profile
    ON allocation_profiles (user_id)
    WHERE is_active = TRUE;

-- Target weights per sector or individual stock
CREATE TABLE IF NOT EXISTS target_allocations (
    id                  SERIAL PRIMARY KEY,
    profile_id          INTEGER NOT NULL
                        REFERENCES allocation_profiles(id) ON DELETE CASCADE,

    -- What this target applies to
    allocation_type     TEXT NOT NULL
                        CHECK (allocation_type IN ('SECTOR', 'STOCK', 'CASH')),
    sector              TEXT,                     -- For SECTOR type
    instrument_token    INTEGER                   -- For STOCK type
                        REFERENCES instrument_master(instrument_token) ON DELETE RESTRICT,

    -- Target and tolerance
    target_weight_pct   NUMERIC(6, 3) NOT NULL,   -- e.g., 40.000 = 40%
    drift_threshold_pct NUMERIC(5, 2) NOT NULL DEFAULT 5.00,  -- Alert threshold

    -- Guardrails
    rebalance_priority  INTEGER DEFAULT 0,        -- Higher = rebalance this first
    max_concentration   NUMERIC(5, 2) DEFAULT 15.00,  -- Hard ceiling per stock

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_weight_range
        CHECK (target_weight_pct >= 0 AND target_weight_pct <= 100),
    CONSTRAINT chk_drift_threshold
        CHECK (drift_threshold_pct > 0 AND drift_threshold_pct <= 50),
    -- Ensure correct fields are filled based on type
    CONSTRAINT chk_allocation_target
        CHECK (
            (allocation_type = 'SECTOR' AND sector IS NOT NULL AND instrument_token IS NULL) OR
            (allocation_type = 'STOCK'  AND instrument_token IS NOT NULL AND sector IS NULL) OR
            (allocation_type = 'CASH'   AND sector IS NULL AND instrument_token IS NULL)
        )
);

CREATE INDEX IF NOT EXISTS idx_allocations_profile
    ON target_allocations (profile_id);

-- Seed a default profile for WJU490
INSERT INTO allocation_profiles (user_id, profile_name, description, is_active)
VALUES ('default', 'Balanced Growth', 'Default allocation strategy â€” customize in Streamlit UI', TRUE)
ON CONFLICT (user_id, profile_name) DO NOTHING;

-- Seed a CASH bucket (remaining allocation goes to cash)
INSERT INTO target_allocations (profile_id, allocation_type, target_weight_pct, drift_threshold_pct)
SELECT id, 'CASH', 10.000, 5.00
FROM allocation_profiles
WHERE user_id = 'default' AND profile_name = 'Balanced Growth'
ON CONFLICT DO NOTHING;


-- ─────────────────────────────────────────────
-- Migration: 008_order_audit_trail.sql
-- ─────────────────────────────────────────────
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
    expected_value  TEXT,                         -- e.g., 'Required: â‚¹75,000'
    actual_value    TEXT,                         -- e.g., 'Available: â‚¹50,000'
    message         TEXT NOT NULL,               -- e.g., 'BLOCKED: Insufficient margin'

    checked_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_validation_audit
    ON order_validation_log (audit_id);


-- ─────────────────────────────────────────────
-- Migration: 009_audit_immutability.sql
-- ─────────────────────────────────────────────
-- ============================================================
-- Migration 009: Audit Immutability Enforcement
-- Triggers that prevent UPDATE or DELETE on audit tables.
-- Financial audit logs must be append-only and tamper-proof.
-- ============================================================

-- Shared function: raises an exception on any mutation attempt
CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'SECURITY VIOLATION: Table "%" is append-only. '
        'UPDATE and DELETE operations are permanently prohibited. '
        'Attempted operation: % by role: %',
        TG_TABLE_NAME,
        TG_OP,
        current_user;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
   SECURITY DEFINER;  -- Runs with definer's rights, not caller's rights

-- Lock down order_audit_trail
DROP TRIGGER IF EXISTS enforce_audit_trail_immutability ON order_audit_trail;
CREATE TRIGGER enforce_audit_trail_immutability
    BEFORE UPDATE OR DELETE ON order_audit_trail
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();

-- Lock down order_validation_log
DROP TRIGGER IF EXISTS enforce_validation_log_immutability ON order_validation_log;
CREATE TRIGGER enforce_validation_log_immutability
    BEFORE UPDATE OR DELETE ON order_validation_log
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();

-- ============================================================
-- Revoke direct table permissions
-- Application uses a service role that only has INSERT + SELECT.
-- This ensures even app bugs can't corrupt the audit trail.
-- NOTE: app_role must be created by a superuser BEFORE running this.
--       See db/temp_create_role.sql
-- ============================================================

-- Role creation is handled separately (requires superuser).
-- If app_role doesn't exist, the GRANTs below will fail gracefully.

DO $$
BEGIN
    -- Revoke dangerous permissions from PUBLIC
    REVOKE UPDATE, DELETE, TRUNCATE ON order_audit_trail FROM PUBLIC;
    REVOKE UPDATE, DELETE, TRUNCATE ON order_validation_log FROM PUBLIC;

    -- Grant minimal permissions to app_role
    GRANT INSERT, SELECT ON order_audit_trail TO app_role;
    GRANT INSERT, SELECT ON order_validation_log TO app_role;
    GRANT USAGE, SELECT ON SEQUENCE order_audit_trail_id_seq TO app_role;
    GRANT USAGE, SELECT ON SEQUENCE order_validation_log_id_seq TO app_role;
EXCEPTION WHEN undefined_object THEN
    RAISE WARNING 'app_role does not exist. Skipping GRANT statements. '
                  'Run db/temp_create_role.sql as postgres superuser first.';
END
$$;


-- ─────────────────────────────────────────────
-- Migration: 010_market_calendar.sql
-- ─────────────────────────────────────────────
-- ============================================================
-- Migration 010: Market Calendar
-- NSE trading holidays and special sessions for 2026.
-- Used by the polling daemon and Gatekeeper to detect
-- whether the market is currently open.
-- ============================================================

CREATE TABLE IF NOT EXISTS market_calendar (
    id              SERIAL PRIMARY KEY,
    holiday_date    DATE NOT NULL,
    exchange        TEXT NOT NULL DEFAULT 'NSE',
    holiday_name    TEXT NOT NULL,
    session_type    TEXT NOT NULL DEFAULT 'CLOSED'
                    CHECK (session_type IN ('CLOSED', 'MUHURAT', 'HALF_DAY')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_calendar_date_exchange
        UNIQUE (holiday_date, exchange)
);

CREATE INDEX IF NOT EXISTS idx_calendar_date
    ON market_calendar (holiday_date);

-- ============================================================
-- NSE 2026 Holiday Calendar
-- Source: NSE India official circular (https://www.nseindia.com)
-- Weekends (Sat/Sun) are implicit market closures â€” not stored here.
-- ============================================================
INSERT INTO market_calendar (holiday_date, exchange, holiday_name, session_type)
VALUES
    -- Q1 2026
    ('2026-01-26', 'NSE', 'Republic Day',                           'CLOSED'),
    ('2026-03-03', 'NSE', 'Holi',                                   'CLOSED'),
    ('2026-03-26', 'NSE', 'Shri Ram Navami',                        'CLOSED'),
    ('2026-03-31', 'NSE', 'Shri Mahavir Jayanti',                   'CLOSED'),

    -- Q2 2026
    ('2026-04-03', 'NSE', 'Good Friday',                            'CLOSED'),
    ('2026-04-14', 'NSE', 'Dr. B.R. Ambedkar Jayanti',              'CLOSED'),
    ('2026-05-01', 'NSE', 'Maharashtra Day',                        'CLOSED'),
    ('2026-05-28', 'NSE', 'Bakri Id',                               'CLOSED'),

    -- Q3 2026
    ('2026-06-26', 'NSE', 'Muharram',                               'CLOSED'),
    ('2026-09-14', 'NSE', 'Ganesh Chaturthi',                       'CLOSED'),

    -- Q4 2026
    ('2026-10-02', 'NSE', 'Mahatma Gandhi Jayanti',                 'CLOSED'),
    ('2026-10-20', 'NSE', 'Dussehra',                               'CLOSED'),
    ('2026-11-08', 'NSE', 'Diwali - Muhurat Trading',               'MUHURAT'),
    ('2026-11-10', 'NSE', 'Diwali - Balipratipada',                 'CLOSED'),
    ('2026-11-24', 'NSE', 'Prakash Gurpurb Sri Guru Nanak Dev',     'CLOSED'),
    ('2026-12-25', 'NSE', 'Christmas',                              'CLOSED')
ON CONFLICT (holiday_date, exchange) DO NOTHING;

-- ============================================================
-- Market Hours Reference
-- As of June 30, 2026 (new F&O extension from Aug 3, 2026)
-- ============================================================
COMMENT ON TABLE market_calendar IS
'NSE trading holidays. Market hours (IST): Pre-open 09:00-09:15, 
Regular session 09:15-15:30, Closing auction 15:15-15:35.
F&O session extends to 15:40 effective Aug 3 2026.
Check system_config for configurable open/close times.';


-- ─────────────────────────────────────────────
-- Migration: 011_corporate_actions.sql
-- ─────────────────────────────────────────────
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
    -- e.g., 2-for-1 split â†’ factor=2.0, old â‚¹1000 â†’ adjusted â‚¹500
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


-- ─────────────────────────────────────────────
-- Migration: 012_app_config.sql
-- ─────────────────────────────────────────────
-- ============================================================
-- Migration 012: App Configuration & Broker Sessions
-- Runtime config flags and Kite Connect session token storage.
-- ============================================================

-- ============================================================
-- Broker session tokens (one active row per user)
-- access_token is valid for one trading day (until ~6 AM next day)
-- ============================================================
CREATE TABLE IF NOT EXISTS broker_sessions (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'default',
    api_key         TEXT NOT NULL,
    access_token    TEXT NOT NULL,           -- From kite.generate_session()
    issued_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,             -- Approx 6 AM next calendar day
    is_valid        BOOLEAN NOT NULL DEFAULT TRUE,

    CONSTRAINT uq_session_user
        UNIQUE (user_id)
);

-- ============================================================
-- System configuration â€” runtime flags as key-value pairs.
-- Editable from the Streamlit Settings page.
-- ============================================================
CREATE TABLE IF NOT EXISTS system_config (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    value_type      TEXT NOT NULL DEFAULT 'string'
                    CHECK (value_type IN ('string', 'boolean', 'integer', 'float')),
    description     TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed all default runtime configuration values
INSERT INTO system_config (key, value, value_type, description)
VALUES
    ('dry_run_mode',
     'true',
     'boolean',
     'CRITICAL: When true, orders are simulated and logged but NOT sent to broker. '
     'Keep enabled for at least 5 full market sessions before going live.'),

    ('polling_interval_sec',
     '50',
     'integer',
     'Seconds between Yahoo Finance price polls. Recommended: 45-60. '
     'Lower values risk rate limiting (HTTP 403/999).'),

    ('slippage_bound_pct',
     '2.0',
     'float',
     'Maximum allowed price drift (%) since recommendation. Orders blocked if '
     'current price has moved more than this % since the rebalancer signal.'),

    ('concentration_limit_pct',
     '15.0',
     'float',
     'Maximum single-stock allocation as % of total portfolio value. '
     'Buying blocked if it would push any stock above this threshold.'),

    ('duplicate_window_sec',
     '300',
     'integer',
     'Seconds to look back for duplicate orders. If an identical order '
     '(same instrument + transaction type) was placed in this window, block it.'),

    ('market_open_time',
     '09:15',
     'string',
     'NSE normal market open time in IST (HH:MM). '
     'Pre-open session starts at 09:00 but orders execute from 09:15.'),

    ('market_close_time',
     '15:30',
     'string',
     'NSE normal market close time in IST (HH:MM). '
     'F&O extends to 15:40 from Aug 3 2026.'),

    ('eod_export_time',
     '15:45',
     'string',
     'Time (IST) for end-of-day Tableau export job. '
     'Runs 15 minutes after market close to capture final settlement prices.'),

    ('max_rebalance_orders',
     '10',
     'integer',
     'Maximum simultaneous orders per rebalance session. '
     'Safety limit to prevent runaway order floods.'),

    ('price_staleness_threshold_sec',
     '120',
     'integer',
     'Seconds after which a price in live_prices is considered stale '
     'and marked with is_stale=true.')

ON CONFLICT (key) DO UPDATE
    SET value       = EXCLUDED.value,
        description = EXCLUDED.description,
        updated_at  = NOW();

-- Auto-update timestamp on config change
CREATE OR REPLACE FUNCTION update_config_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_config_updated_at ON system_config;
CREATE TRIGGER trg_config_updated_at
    BEFORE UPDATE ON system_config
    FOR EACH ROW EXECUTE FUNCTION update_config_timestamp();


-- ─────────────────────────────────────────────
-- Migration: 013_partition_management.sql
-- ─────────────────────────────────────────────
-- ============================================================
-- Migration 013: Partition Management Functions
-- Auto-creates and drops daily partitions for price_history.
-- Called by APScheduler daily at ~8 AM IST.
-- ============================================================

-- Create partition for a specific date
CREATE OR REPLACE FUNCTION create_price_partition(target_date DATE DEFAULT CURRENT_DATE)
RETURNS TEXT AS $$
DECLARE
    partition_name TEXT;
    next_date DATE;
BEGIN
    next_date := target_date + INTERVAL '1 day';
    partition_name := 'price_history_' || to_char(target_date, 'YYYY_MM_DD');

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF price_history
         FOR VALUES FROM (%L) TO (%L)',
        partition_name,
        target_date,
        next_date
    );

    RETURN partition_name;
END;
$$ LANGUAGE plpgsql;

-- Create today's + next N days partitions (safety margin)
CREATE OR REPLACE FUNCTION create_upcoming_partitions(days_ahead INTEGER DEFAULT 3)
RETURNS TABLE(partition_name TEXT, created_at_ts TIMESTAMPTZ) AS $$
DECLARE
    target_date DATE;
    pname TEXT;
BEGIN
    FOR i IN 0..days_ahead LOOP
        target_date := CURRENT_DATE + (i || ' days')::INTERVAL;
        pname := create_price_partition(target_date);
        partition_name := pname;
        created_at_ts := NOW();
        RETURN NEXT;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Drop partitions older than retention_days
CREATE OR REPLACE FUNCTION drop_old_price_partitions(retention_days INTEGER DEFAULT 90)
RETURNS TABLE(dropped_partition TEXT, partition_date DATE) AS $$
DECLARE
    cutoff_date DATE;
    rec RECORD;
    pdate DATE;
    pname TEXT;
BEGIN
    cutoff_date := CURRENT_DATE - (retention_days || ' days')::INTERVAL;

    FOR rec IN
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename LIKE 'price_history_%'
          AND tablename ~ 'price_history_\d{4}_\d{2}_\d{2}'
        ORDER BY tablename ASC
    LOOP
        BEGIN
            -- Parse date from partition name: price_history_2026_06_30 â†’ 2026-06-30
            pdate := to_date(
                substring(rec.tablename FROM 'price_history_(\d{4}_\d{2}_\d{2})'),
                'YYYY_MM_DD'
            );

            IF pdate < cutoff_date THEN
                pname := rec.tablename;
                EXECUTE format('DROP TABLE IF EXISTS %I', pname);
                dropped_partition := pname;
                partition_date := pdate;
                RETURN NEXT;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'Could not process partition %: %', rec.tablename, SQLERRM;
        END;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Convenience view: List all existing price_history partitions
CREATE OR REPLACE VIEW v_price_partitions AS
SELECT
    tablename AS partition_name,
    to_date(
        substring(tablename FROM 'price_history_(\d{4}_\d{2}_\d{2})'),
        'YYYY_MM_DD'
    ) AS partition_date,
    pg_size_pretty(pg_total_relation_size(quote_ident(tablename))) AS size
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename LIKE 'price_history_%'
  AND tablename ~ 'price_history_\d{4}_\d{2}_\d{2}'
ORDER BY partition_date DESC;


-- ─────────────────────────────────────────────
-- Migration: 014_export_views.sql
-- ─────────────────────────────────────────────
-- ============================================================
-- Migration 014: Export Views
-- Analytics views joining holdings + live prices.
-- Used by: Streamlit dashboard, drift detector, Tableau export.
-- ============================================================

-- ============================================================
-- VIEW: v_portfolio_snapshot
-- Primary dashboard view â€” joins holdings with live prices.
-- Returns one row per holding with full valuation metrics.
-- ============================================================
CREATE OR REPLACE VIEW v_portfolio_snapshot AS
SELECT
    -- Identity
    h.user_id,
    h.instrument_token,
    h.tradingsymbol,
    h.exchange,
    im.name                                         AS company_name,
    im.sector,
    im.industry,
    im.market_cap_category,
    im.yf_ticker,

    -- Quantities
    h.quantity + h.t1_quantity                      AS total_quantity,
    h.quantity                                      AS settled_quantity,
    h.t1_quantity,
    h.used_quantity,
    h.authorised_quantity,

    -- Prices
    h.average_price,
    COALESCE(lp.last_price, h.last_price)           AS current_price,
    lp.open_price,
    lp.high_price,
    lp.low_price,
    COALESCE(lp.close_price, h.close_price)         AS prev_close,

    -- Valuation
    h.average_price * (h.quantity + h.t1_quantity)  AS invested_value,
    COALESCE(lp.last_price, h.last_price)
        * (h.quantity + h.t1_quantity)              AS current_value,

    -- Total P&L
    (COALESCE(lp.last_price, h.last_price) - h.average_price)
        * (h.quantity + h.t1_quantity)              AS total_pnl,

    -- Total return %
    CASE
        WHEN h.average_price > 0
        THEN ROUND(
            ((COALESCE(lp.last_price, h.last_price) - h.average_price)
             / h.average_price) * 100,
            2)
        ELSE 0
    END                                             AS total_return_pct,

    -- Day P&L
    (COALESCE(lp.last_price, h.last_price)
        - COALESCE(lp.close_price, h.close_price))
        * (h.quantity + h.t1_quantity)              AS day_pnl,

    -- Day change
    COALESCE(lp.change_absolute, h.day_change)      AS day_change,
    COALESCE(lp.change_percent,  h.day_change_pct)  AS day_change_pct,

    -- Data freshness
    lp.last_updated                                 AS price_updated_at,
    lp.is_stale                                     AS price_is_stale,
    h.last_synced_at                                AS holdings_synced_at,

    -- Metadata
    h.product,
    h.first_buy_date,
    h.has_discrepancy

FROM user_holdings h
JOIN  instrument_master im ON h.instrument_token = im.instrument_token
LEFT JOIN live_prices   lp ON h.instrument_token = lp.instrument_token
WHERE (h.quantity + h.t1_quantity) > 0;

-- ============================================================
-- VIEW: v_portfolio_summary
-- Single-row aggregate: total AUM, day P&L, total return, etc.
-- Used for the top KPI cards in Streamlit.
-- ============================================================
CREATE OR REPLACE VIEW v_portfolio_summary AS
SELECT
    user_id,
    COUNT(*)                            AS num_holdings,
    ROUND(SUM(invested_value),    2)    AS total_invested,
    ROUND(SUM(current_value),     2)    AS total_aum,
    ROUND(SUM(total_pnl),         2)    AS total_pnl,
    ROUND(SUM(day_pnl),           2)    AS day_pnl,
    CASE
        WHEN SUM(invested_value) > 0
        THEN ROUND((SUM(total_pnl) / SUM(invested_value)) * 100, 2)
        ELSE 0
    END                                 AS total_return_pct,
    CASE
        WHEN SUM(current_value) > 0
        THEN ROUND((SUM(day_pnl) / SUM(current_value)) * 100, 2)
        ELSE 0
    END                                 AS day_return_pct,
    MAX(price_updated_at)               AS last_price_update,
    MAX(holdings_synced_at)             AS last_holdings_sync
FROM v_portfolio_snapshot
GROUP BY user_id;

-- ============================================================
-- VIEW: v_sector_allocation
-- Current vs target sector weights for drift detection.
-- ============================================================
CREATE OR REPLACE VIEW v_sector_allocation AS
WITH portfolio_total AS (
    SELECT user_id, SUM(current_value) AS total_aum
    FROM v_portfolio_snapshot
    GROUP BY user_id
),
sector_values AS (
    SELECT
        ps.user_id,
        COALESCE(ps.sector, 'Unclassified')         AS sector,
        COUNT(*)                                    AS num_holdings,
        SUM(ps.current_value)                       AS sector_value
    FROM v_portfolio_snapshot ps
    GROUP BY ps.user_id, ps.sector
)
SELECT
    sv.user_id,
    sv.sector,
    sv.num_holdings,
    ROUND(sv.sector_value, 2)                       AS sector_value,
    pt.total_aum,
    CASE
        WHEN pt.total_aum > 0
        THEN ROUND((sv.sector_value / pt.total_aum) * 100, 3)
        ELSE 0
    END                                             AS current_weight_pct,
    ta.target_weight_pct,
    ta.drift_threshold_pct,
    CASE
        WHEN pt.total_aum > 0 AND ta.target_weight_pct IS NOT NULL
        THEN ROUND(
            ((sv.sector_value / pt.total_aum) * 100) - ta.target_weight_pct,
            3)
        ELSE NULL
    END                                             AS drift_pct,
    CASE
        WHEN pt.total_aum > 0 AND ta.target_weight_pct IS NOT NULL
        AND ABS(((sv.sector_value / pt.total_aum) * 100) - ta.target_weight_pct)
            > ta.drift_threshold_pct
        THEN TRUE
        ELSE FALSE
    END                                             AS is_drifted
FROM sector_values sv
JOIN portfolio_total pt ON sv.user_id = pt.user_id
LEFT JOIN allocation_profiles ap
    ON sv.user_id = ap.user_id AND ap.is_active = TRUE
LEFT JOIN target_allocations ta
    ON ap.id = ta.profile_id
    AND ta.allocation_type = 'SECTOR'
    AND ta.sector = sv.sector
ORDER BY ABS(COALESCE(
    CASE
        WHEN pt.total_aum > 0 AND ta.target_weight_pct IS NOT NULL
        THEN ((sv.sector_value / pt.total_aum) * 100) - ta.target_weight_pct
        ELSE NULL
    END, 0)) DESC;

-- ============================================================
-- VIEW: v_tax_summary
-- STCG vs LTCG lot summary per holding for the tax guard.
-- ============================================================
CREATE OR REPLACE VIEW v_tax_summary AS
SELECT
    h.user_id,
    h.tradingsymbol,
    h.exchange,
    h.average_price,
    COALESCE(lp.last_price, h.last_price)           AS current_price,
    -- LTCG lots (held > 365 days)
    SUM(CASE WHEN tl.buy_date < CURRENT_DATE - INTERVAL '365 days'
             THEN tl.remaining_quantity ELSE 0 END) AS ltcg_quantity,
    SUM(CASE WHEN tl.buy_date < CURRENT_DATE - INTERVAL '365 days'
             THEN tl.remaining_quantity * tl.buy_price ELSE 0 END) AS ltcg_cost_basis,
    -- STCG lots (held <= 365 days)
    SUM(CASE WHEN tl.buy_date >= CURRENT_DATE - INTERVAL '365 days'
             THEN tl.remaining_quantity ELSE 0 END) AS stcg_quantity,
    SUM(CASE WHEN tl.buy_date >= CURRENT_DATE - INTERVAL '365 days'
             THEN tl.remaining_quantity * tl.buy_price ELSE 0 END) AS stcg_cost_basis,
    -- Nearest STCG to LTCG conversion date
    MIN(CASE WHEN tl.buy_date >= CURRENT_DATE - INTERVAL '365 days'
                  AND tl.remaining_quantity > 0
             THEN tl.buy_date + INTERVAL '365 days' ELSE NULL END) AS next_ltcg_date
FROM user_holdings h
JOIN holding_tax_lots tl ON h.id = tl.holding_id
LEFT JOIN live_prices lp ON h.instrument_token = lp.instrument_token
WHERE tl.remaining_quantity > 0
GROUP BY h.user_id, h.tradingsymbol, h.exchange, h.average_price,
         lp.last_price, h.last_price;

