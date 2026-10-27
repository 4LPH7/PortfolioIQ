-- ============================================================
-- Migration 026: Risk guard thresholds
-- Alerts only (never orders), after QuantConnect Lean's risk models:
--   MaximumDrawdownPercentPerSecurity, TrailingStopRiskManagementModel,
--   MaximumSectorExposureRiskManagementModel.
-- ============================================================

INSERT INTO system_config (key, value, value_type, description)
VALUES
    ('risk_max_holding_loss_pct',
     '15',
     'float',
     'Alert when a holding is this many percent below its average cost.'),
    ('risk_trailing_stop_pct',
     '12',
     'float',
     'Alert when a holding closes this many percent below its 60-day high (dividend-adjusted).'),
    ('risk_max_sector_pct',
     '30',
     'float',
     'Alert when one mapped sector is more than this share of the portfolio.')
ON CONFLICT (key) DO NOTHING;
