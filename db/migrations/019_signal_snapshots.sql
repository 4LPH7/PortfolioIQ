-- ============================================================
-- Migration 019: Signal Snapshots & Multi-Horizon Forward Outcomes
-- Append-only time-series tracking daily quantitative signal states,
-- underlying indicator metrics, and realized forward returns (5d, 20d, 60d).
-- ============================================================

CREATE TABLE IF NOT EXISTS signal_snapshots (
    id                          BIGSERIAL PRIMARY KEY,
    snapshot_date               DATE NOT NULL,
    user_id                     TEXT NOT NULL DEFAULT 'default',
    tradingsymbol               TEXT NOT NULL,
    model_version               TEXT NOT NULL DEFAULT 'v1.0.0',

    -- Signal valuation & state
    current_price               NUMERIC(15, 2) NOT NULL,
    benchmark_price             NUMERIC(15, 2), -- NIFTY 50 TRI closing price
    composite_score             NUMERIC(5, 2) NOT NULL, -- 0.00 to 100.00
    signal_label                TEXT NOT NULL, -- 'STRONG_BUY', 'BUY', 'HOLD', 'SELL', 'STRONG_SELL'
    status                      TEXT NOT NULL DEFAULT 'PENDING', -- 'PROVEN_EDGE', 'UNPROVEN_NOISE', 'PENDING'

    -- Indicators payload (JSONB)
    -- Stores breakdown: {"rsi": {"value": 28.4, "score": 80.0, "ic": 0.075, "p_value": 0.018, "weight": 0.35, "pruned": false}, ...}
    indicators                  JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Calibrated Monte Carlo dispersion percentiles (JSONB)
    -- Stores: {"p10": 102.5, "p25": 108.0, "p50": 114.2, "p75": 121.0, "p90": 128.5, "df": 4.2, "coverage_80": 81.5}
    monte_carlo                 JSONB DEFAULT '{}'::jsonb,

    -- Multi-Horizon Forward Return Tracking (Realized Ex-Post)
    -- 5 Trading Days (~1 week)
    return_5d_stock             NUMERIC(8, 4),
    return_5d_benchmark         NUMERIC(8, 4),
    excess_return_5d            NUMERIC(8, 4),
    realized_5d_at              DATE,

    -- 20 Trading Days (~1 month)
    return_20d_stock            NUMERIC(8, 4),
    return_20d_benchmark        NUMERIC(8, 4),
    excess_return_20d           NUMERIC(8, 4),
    realized_20d_at             DATE,

    -- 60 Trading Days (~1 quarter)
    return_60d_stock            NUMERIC(8, 4),
    return_60d_benchmark        NUMERIC(8, 4),
    excess_return_60d           NUMERIC(8, 4),
    realized_60d_at             DATE,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT uq_signal_snapshots UNIQUE (user_id, tradingsymbol, snapshot_date, model_version),
    CONSTRAINT chk_signal_status CHECK (status IN ('PROVEN_EDGE', 'UNPROVEN_NOISE', 'PENDING')),
    CONSTRAINT chk_composite_score CHECK (composite_score >= 0.00 AND composite_score <= 100.00),
    CONSTRAINT chk_positive_price CHECK (current_price > 0)
);

-- Indexes for deterministic queries and time-series replay
CREATE INDEX IF NOT EXISTS idx_signal_snapshots_lookup
    ON signal_snapshots (user_id, tradingsymbol, snapshot_date DESC);

CREATE INDEX IF NOT EXISTS idx_signal_snapshots_date
    ON signal_snapshots (snapshot_date DESC);

CREATE INDEX IF NOT EXISTS idx_signal_snapshots_status
    ON signal_snapshots (status);

CREATE INDEX IF NOT EXISTS idx_signal_snapshots_indicators_gin
    ON signal_snapshots USING GIN (indicators);
