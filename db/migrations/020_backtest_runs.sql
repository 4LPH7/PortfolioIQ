-- ============================================================
-- Migration 020: Walk-Forward Backtest Runs & Indicator Evaluations
-- Stores rolling walk-forward simulation runs, out-of-sample performance,
-- dual-baseline comparisons, and independent indicator validation metrics.
-- ============================================================

CREATE TABLE IF NOT EXISTS backtest_runs (
    id                          BIGSERIAL PRIMARY KEY,
    run_id                      UUID NOT NULL DEFAULT gen_random_uuid(),
    tradingsymbol               TEXT NOT NULL,
    model_version               TEXT NOT NULL DEFAULT 'v1.0.0',

    -- Partition windows
    train_start_date            DATE NOT NULL,
    train_end_date              DATE NOT NULL,
    test_start_date             DATE NOT NULL,
    test_end_date               DATE NOT NULL,
    train_window_days           INT NOT NULL DEFAULT 252,
    test_window_days            INT NOT NULL DEFAULT 63,
    total_folds                 INT NOT NULL DEFAULT 1,

    -- Out-of-Sample Strategy Performance (Net of Indian Transaction Costs)
    strategy_cagr               NUMERIC(8, 4),
    strategy_sharpe             NUMERIC(8, 4),
    strategy_sortino            NUMERIC(8, 4),
    strategy_max_drawdown       NUMERIC(8, 4),
    strategy_win_rate           NUMERIC(8, 4),
    strategy_profit_factor      NUMERIC(8, 4),
    total_trades                INT NOT NULL DEFAULT 0,

    -- Baseline 1: Stock Buy-and-Hold (Timing Skill)
    stock_cagr                  NUMERIC(8, 4),
    stock_sharpe                NUMERIC(8, 4),
    stock_max_drawdown          NUMERIC(8, 4),

    -- Baseline 2: Market NIFTY 50 TRI Buy-and-Hold (Market Alpha)
    benchmark_cagr              NUMERIC(8, 4),
    benchmark_sharpe            NUMERIC(8, 4),
    benchmark_max_drawdown      NUMERIC(8, 4),

    -- Excess Attribution
    excess_cagr_vs_stock        NUMERIC(8, 4),
    excess_cagr_vs_benchmark    NUMERIC(8, 4),
    total_cost_drag_bps         NUMERIC(8, 2) DEFAULT 0.00,

    -- Hurdle Classification
    status                      TEXT NOT NULL DEFAULT 'UNPROVEN_NOISE',
    passed_hurdle               BOOLEAN NOT NULL DEFAULT FALSE,
    hurdle_details              JSONB DEFAULT '{}'::jsonb,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT uq_backtest_runs UNIQUE (tradingsymbol, model_version, train_start_date, test_end_date),
    CONSTRAINT chk_backtest_status CHECK (status IN ('PROVEN_EDGE', 'UNPROVEN_NOISE', 'PENDING'))
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_symbol_date
    ON backtest_runs (tradingsymbol, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_status
    ON backtest_runs (status);


CREATE TABLE IF NOT EXISTS indicator_evaluations (
    id                          BIGSERIAL PRIMARY KEY,
    backtest_run_id             BIGINT NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    indicator_name              TEXT NOT NULL,

    -- Statistical Information Coefficient
    in_sample_ic                NUMERIC(8, 4),
    in_sample_p_value           NUMERIC(8, 4),
    out_sample_ic               NUMERIC(8, 4),
    out_sample_p_value          NUMERIC(8, 4),
    mean_ic                     NUMERIC(8, 4),
    std_ic                      NUMERIC(8, 4),
    information_ratio           NUMERIC(8, 4),

    -- Dynamic Weight & Pruning
    weight                      NUMERIC(8, 4) NOT NULL DEFAULT 0.0000,
    is_pruned                   BOOLEAN NOT NULL DEFAULT TRUE,
    prune_reason                TEXT,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_indicator_eval UNIQUE (backtest_run_id, indicator_name)
);

CREATE INDEX IF NOT EXISTS idx_indicator_evaluations_run
    ON indicator_evaluations (backtest_run_id);
