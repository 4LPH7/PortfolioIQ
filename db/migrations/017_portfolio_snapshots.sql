-- ============================================================
-- Migration 017: Portfolio Daily Snapshots
-- Append-only time series tracking daily NAV, unit NAV (base 100),
-- daily returns, benchmark comparisons, and external cash flows.
-- ============================================================

CREATE TABLE IF NOT EXISTS portfolio_daily_snapshots (
    id                          BIGSERIAL PRIMARY KEY,
    snapshot_date               DATE NOT NULL,
    user_id                     TEXT NOT NULL DEFAULT 'default',

    -- Valuation components
    total_equity_value          NUMERIC(15, 2) NOT NULL,
    cash_balance                NUMERIC(15, 2) NOT NULL,
    total_nav                   NUMERIC(15, 2) NOT NULL,

    -- Unitization (GIPS Time-Weighted Return)
    units                       NUMERIC(18, 6) NOT NULL DEFAULT 1.000000,
    unit_nav                    NUMERIC(15, 4) NOT NULL DEFAULT 100.0000,
    daily_return_pct            NUMERIC(8, 4),

    -- Benchmark comparison (Total Return Index)
    benchmark_name              TEXT NOT NULL DEFAULT 'NIFTY 50 TRI',
    benchmark_value             NUMERIC(15, 2),
    benchmark_daily_return_pct  NUMERIC(8, 4),

    -- Flow accounting & attribution
    net_external_flow           NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
    gross_daily_return_pct      NUMERIC(8, 4),
    stt_drag_bps                NUMERIC(6, 2) DEFAULT 0.00,
    fee_drag_bps                NUMERIC(6, 2) DEFAULT 0.00,
    tax_drag_bps                NUMERIC(6, 2) DEFAULT 0.00,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT uq_snapshots_user_date UNIQUE (user_id, snapshot_date),
    CONSTRAINT chk_positive_nav CHECK (total_nav >= 0),
    CONSTRAINT chk_positive_units CHECK (units > 0),
    CONSTRAINT chk_positive_unit_nav CHECK (unit_nav > 0)
);

-- Time-series lookup indexes
CREATE INDEX IF NOT EXISTS idx_snapshots_user_date_asc
    ON portfolio_daily_snapshots (user_id, snapshot_date ASC);

CREATE INDEX IF NOT EXISTS idx_snapshots_user_date_desc
    ON portfolio_daily_snapshots (user_id, snapshot_date DESC);
