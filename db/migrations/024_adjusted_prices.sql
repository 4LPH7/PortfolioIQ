-- ============================================================
-- Migration 024: Corporate-action-adjusted closes
-- The Yahoo chart API path stored the traded close while the yfinance fallback stored
-- dividend-adjusted closes, so one cache mixed two conventions. From now on every row keeps
-- the traded close in `close` and the split- and dividend-adjusted close in `adj_close`.
--
-- price_basis:
--   'close+adj_close'     both columns filled from one source (current convention)
--   'legacy_unadjusted'   older than the refetchable history; adjusted by the earliest known factor
--   NULL                  cached before this migration; queued for backfill_adjusted_prices()
-- ============================================================

ALTER TABLE historical_daily_bars
    ADD COLUMN IF NOT EXISTS adj_close   NUMERIC(15, 4),
    ADD COLUMN IF NOT EXISTS price_basis TEXT;

CREATE INDEX IF NOT EXISTS idx_historical_daily_bars_unadjusted
    ON historical_daily_bars (tradingsymbol)
    WHERE price_basis IS NULL;
