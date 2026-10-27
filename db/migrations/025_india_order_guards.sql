-- ============================================================
-- Migration 025: India order guards
-- Two gatekeeper checks before an order reaches the broker:
--   SELLABLE_CHECK    a SELL never exceeds what the broker says can be sold today
--   PRICE_BAND_CHECK  the price sits inside the scrip's live circuit limits
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
        'PRICE_BAND_CHECK'
    ));

INSERT INTO system_config (key, value, value_type, description)
VALUES
    ('btst_enabled',
     'false',
     'boolean',
     'Count T1 (bought yesterday, not yet in demat) shares as sellable. Only if your broker '
     'supports BTST. Trade-to-trade (BE/BZ series) scrips are never counted.'),
    ('edis_mode',
     'ddpi',
     'string',
     'ddpi: broker can debit your demat directly. tpin: each sale needs e-DIS authorisation, '
     'so a SELL is capped by the quantity authorised today.'),
    ('price_band_max_age_sec',
     '30',
     'integer',
     'Maximum age of the broker quote used for the circuit-limit check. A live order with an '
     'older or missing quote is rejected.')
ON CONFLICT (key) DO NOTHING;
