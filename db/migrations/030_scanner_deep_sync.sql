-- ============================================================
-- Migration 030: remember which symbols have had their full 10-year history fetched
-- Prices cached before this held about 5 years. The scanner refetches each symbol once; a
-- recent IPO has no more history to give, so without this record it would refetch forever.
-- ============================================================

CREATE TABLE IF NOT EXISTS scanner_deep_sync (
    tradingsymbol   TEXT PRIMARY KEY,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
