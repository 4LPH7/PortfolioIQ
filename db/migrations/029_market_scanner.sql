-- ============================================================
-- Migration 029: market scanner (NIFTY 500 ranked on signals tested out of sample)
-- Read-only research: nothing here can place, size or cancel an order.
-- ============================================================

-- Index members from NSE's published constituent list, refreshed weekly
CREATE TABLE IF NOT EXISTS scanner_universe (
    tradingsymbol   TEXT PRIMARY KEY,
    company_name    TEXT,
    industry        TEXT,
    isin            TEXT,
    index_name      TEXT NOT NULL DEFAULT 'NIFTY 500',
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One row per scan: the ranking and its evidence are stored whole, so the page reads one row
CREATE TABLE IF NOT EXISTS scanner_runs (
    id              BIGSERIAL PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'completed', 'failed')),
    trigger         TEXT NOT NULL DEFAULT 'scheduled',
    universe_size   INTEGER,
    priced          INTEGER,
    result          JSONB,
    error           TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ
);

-- At most one scan runs at a time: a second start fails on this index instead of racing
CREATE UNIQUE INDEX IF NOT EXISTS uq_scanner_one_running
    ON scanner_runs (status) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_scanner_runs_finished
    ON scanner_runs (finished_at DESC) WHERE status = 'completed';
