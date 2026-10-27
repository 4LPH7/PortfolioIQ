-- ============================================================
-- Migration 027: AI research committee (advisory only)
-- After TradingAgents (analysts, bull/bear debate, portfolio manager) and ai-hedge-fund
-- (investor personas). The committee has no tools and no path to orders: its output is
-- text stored here for the user to read.
-- ============================================================

-- Fundamentals fetched off the request path by the nightly job
CREATE TABLE IF NOT EXISTS fundamentals_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    tradingsymbol   TEXT NOT NULL,
    fields          JSONB NOT NULL,            -- {key: {"value": n, "unit": "..."}}
    source          TEXT NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol_time
    ON fundamentals_snapshots (tradingsymbol, fetched_at DESC);

-- One row per committee run: inputs, every stage's output, cost and audit fields
CREATE TABLE IF NOT EXISTS research_runs (
    id                      BIGSERIAL PRIMARY KEY,
    user_id                 TEXT NOT NULL DEFAULT 'default',
    idempotency_key         TEXT NOT NULL,
    tradingsymbol           TEXT NOT NULL,
    run_date                DATE NOT NULL,     -- IST trading day, for the daily quota
    status                  TEXT NOT NULL DEFAULT 'queued'
                            CHECK (status IN ('queued', 'running', 'completed', 'budget_exceeded',
                                              'declined', 'failed', 'abandoned')),
    models                  JSONB,             -- stage -> model id
    prompt_hashes           JSONB,             -- stage -> sha256 of the prompt template
    snapshot_schema_version INTEGER,
    data_as_of              DATE,              -- last bar in the snapshot
    snapshot                JSONB,
    stages                  JSONB,             -- stage -> parsed output, refusal or error
    decision                JSONB,
    grounding               JSONB,             -- {checked, passed, rate, failures}
    calls                   JSONB,             -- per call: stage, model, usage, request id, ms
    cost_usd                NUMERIC(10, 4) NOT NULL DEFAULT 0,
    budget_usd              NUMERIC(10, 4),
    error                   TEXT,
    price_at_run            NUMERIC(15, 4),
    fwd_return_5d           NUMERIC(10, 6),
    fwd_return_20d          NUMERIC(10, 6),
    fwd_return_60d          NUMERIC(10, 6),
    alpha_5d                NUMERIC(10, 6),
    alpha_20d               NUMERIC(10, 6),
    alpha_60d               NUMERIC(10, 6),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at              TIMESTAMPTZ,
    finished_at             TIMESTAMPTZ,

    -- A repeated POST with the same key returns the same run instead of starting another
    CONSTRAINT uq_research_idempotency UNIQUE (user_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_research_runs_user_day
    ON research_runs (user_id, run_date);
CREATE INDEX IF NOT EXISTS idx_research_runs_symbol
    ON research_runs (tradingsymbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_runs_active
    ON research_runs (status) WHERE status IN ('queued', 'running');

INSERT INTO system_config (key, value, value_type, description)
VALUES
    ('research_model_quick',
     'claude-opus-5-5',
     'string',
     'Model for the analyst, persona and debate stages: claude-opus-5-5 or claude-sonnet-5-5.'),
    ('research_model_deep',
     'claude-opus-5-5',
     'string',
     'Model for the portfolio-manager decision: claude-opus-5-5 or claude-sonnet-5-5.'),
    ('research_daily_run_limit',
     '5',
     'integer',
     'Maximum research committee runs per IST day. A run that failed before spending anything does not count.'),
    ('research_max_cost_usd_per_run',
     '1.00',
     'float',
     'Hard spending cap per run in USD. Remaining stages are skipped when the next call could pass it.'),
    ('research_run_timeout_min',
     '15',
     'integer',
     'A run still queued or running after this many minutes is marked abandoned.')
ON CONFLICT (key) DO NOTHING;
