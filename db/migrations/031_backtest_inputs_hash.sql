-- ============================================================
-- Migration 031: fingerprint each backtest run's inputs
-- sha256 over the parameters, cost/impact constants, fill rule and a digest of the price
-- bars, so two runs can be shown to share (or not share) data and settings.
-- Older rows stay NULL.
-- ============================================================

ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS inputs_hash TEXT;
