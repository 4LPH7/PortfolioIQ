-- ============================================================
-- Migration 033: Decision journal
-- One row per decision (took, skipped or watching) with the scanner rank, odds and committee
-- rating as they were at the time, so the outcome can be judged against what was known then.
-- Context is captured by the server, never trusted from the client. Information only.
-- ============================================================

CREATE TABLE IF NOT EXISTS decision_journal (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             TEXT NOT NULL DEFAULT 'default',
    tradingsymbol       TEXT NOT NULL,
    action              TEXT NOT NULL CHECK (action IN ('took', 'skipped', 'watching')),
    side                TEXT CHECK (side IN ('BUY', 'SELL')),
    reason              TEXT NOT NULL DEFAULT '' CHECK (char_length(reason) <= 500),
    context             JSONB NOT NULL DEFAULT '{}'::jsonb,
    price_at_decision   NUMERIC(15, 4),
    decided_on          DATE NOT NULL DEFAULT CURRENT_DATE,
    fwd_return_20d      NUMERIC(10, 6),
    alpha_20d           NUMERIC(10, 6),
    settled_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_journal_user_day
    ON decision_journal (user_id, decided_on DESC);
CREATE INDEX IF NOT EXISTS idx_journal_unsettled
    ON decision_journal (decided_on) WHERE settled_at IS NULL;
