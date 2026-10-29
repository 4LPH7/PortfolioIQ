-- ============================================================
-- Migration 032: Portfolio limits
-- A gatekeeper check that rejects a live BUY pushing one sector or the portfolio drawdown past
-- the user's limits. In dry run it only records a "WOULD BLOCK LIVE" note. Sells are never blocked.
-- Separate from the risk_* alert thresholds (026), which only notify.
-- ============================================================

ALTER TABLE order_validation_log
    DROP CONSTRAINT IF EXISTS order_validation_log_check_name_check;

ALTER TABLE order_validation_log
    ADD CONSTRAINT order_validation_log_check_name_check
    CHECK (check_name IN (
        'MARGIN_CHECK',
        'SLIPPAGE_CHECK',
        'CONCENTRATION_CHECK',
        'DUPLICATE_CHECK',
        'MARKET_HOURS_CHECK',
        'QUANTITY_CHECK',
        'TAX_WARNING',
        'SELLABLE_CHECK',
        'PRICE_BAND_CHECK',
        'PORTFOLIO_LIMITS_CHECK'
    ));

INSERT INTO system_config (key, value, value_type, description)
VALUES
    ('risk_block_max_sector_pct',
     '35',
     'float',
     'Reject a live BUY that would make one mapped sector more than this share of the portfolio.'),
    ('risk_block_max_drawdown_pct',
     '25',
     'float',
     'Reject live BUYs while the portfolio equity curve is this many percent below its one-year peak.')
ON CONFLICT (key) DO NOTHING;
