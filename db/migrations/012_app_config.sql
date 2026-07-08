-- ============================================================
-- Migration 012: App Configuration & Broker Sessions
-- Runtime config flags and Kite Connect session token storage.
-- ============================================================

-- ============================================================
-- Broker session tokens (one active row per user)
-- access_token is valid for one trading day (until ~6 AM next day)
-- ============================================================
CREATE TABLE IF NOT EXISTS broker_sessions (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'default',
    api_key         TEXT NOT NULL,
    access_token    TEXT NOT NULL,           -- From kite.generate_session()
    issued_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,             -- Approx 6 AM next calendar day
    is_valid        BOOLEAN NOT NULL DEFAULT TRUE,

    CONSTRAINT uq_session_user
        UNIQUE (user_id)
);

-- ============================================================
-- System configuration — runtime flags as key-value pairs.
-- Editable from the Streamlit Settings page.
-- ============================================================
CREATE TABLE IF NOT EXISTS system_config (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    value_type      TEXT NOT NULL DEFAULT 'string'
                    CHECK (value_type IN ('string', 'boolean', 'integer', 'float')),
    description     TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed all default runtime configuration values
INSERT INTO system_config (key, value, value_type, description)
VALUES
    ('dry_run_mode',
     'true',
     'boolean',
     'CRITICAL: When true, orders are simulated and logged but NOT sent to broker. '
     'Keep enabled for at least 5 full market sessions before going live.'),

    ('polling_interval_sec',
     '50',
     'integer',
     'Seconds between Yahoo Finance price polls. Recommended: 45-60. '
     'Lower values risk rate limiting (HTTP 403/999).'),

    ('slippage_bound_pct',
     '2.0',
     'float',
     'Maximum allowed price drift (%) since recommendation. Orders blocked if '
     'current price has moved more than this % since the rebalancer signal.'),

    ('concentration_limit_pct',
     '15.0',
     'float',
     'Maximum single-stock allocation as % of total portfolio value. '
     'Buying blocked if it would push any stock above this threshold.'),

    ('duplicate_window_sec',
     '300',
     'integer',
     'Seconds to look back for duplicate orders. If an identical order '
     '(same instrument + transaction type) was placed in this window, block it.'),

    ('market_open_time',
     '09:15',
     'string',
     'NSE normal market open time in IST (HH:MM). '
     'Pre-open session starts at 09:00 but orders execute from 09:15.'),

    ('market_close_time',
     '15:30',
     'string',
     'NSE normal market close time in IST (HH:MM). '
     'F&O extends to 15:40 from Aug 3 2026.'),

    ('eod_export_time',
     '15:45',
     'string',
     'Time (IST) for end-of-day Tableau export job. '
     'Runs 15 minutes after market close to capture final settlement prices.'),

    ('max_rebalance_orders',
     '10',
     'integer',
     'Maximum simultaneous orders per rebalance session. '
     'Safety limit to prevent runaway order floods.'),

    ('price_staleness_threshold_sec',
     '120',
     'integer',
     'Seconds after which a price in live_prices is considered stale '
     'and marked with is_stale=true.')

ON CONFLICT (key) DO UPDATE
    SET value       = EXCLUDED.value,
        description = EXCLUDED.description,
        updated_at  = NOW();

-- Auto-update timestamp on config change
CREATE OR REPLACE FUNCTION update_config_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_config_updated_at ON system_config;
CREATE TRIGGER trg_config_updated_at
    BEFORE UPDATE ON system_config
    FOR EACH ROW EXECUTE FUNCTION update_config_timestamp();
