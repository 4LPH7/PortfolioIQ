-- ============================================================
-- Migration 010: Market Calendar
-- NSE trading holidays and special sessions for 2026.
-- Used by the polling daemon and Gatekeeper to detect
-- whether the market is currently open.
-- ============================================================

CREATE TABLE IF NOT EXISTS market_calendar (
    id              SERIAL PRIMARY KEY,
    holiday_date    DATE NOT NULL,
    exchange        TEXT NOT NULL DEFAULT 'NSE',
    holiday_name    TEXT NOT NULL,
    session_type    TEXT NOT NULL DEFAULT 'CLOSED'
                    CHECK (session_type IN ('CLOSED', 'MUHURAT', 'HALF_DAY')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_calendar_date_exchange
        UNIQUE (holiday_date, exchange)
);

CREATE INDEX IF NOT EXISTS idx_calendar_date
    ON market_calendar (holiday_date);

-- ============================================================
-- NSE 2026 Holiday Calendar
-- Source: NSE India official circular (https://www.nseindia.com)
-- Weekends (Sat/Sun) are implicit market closures — not stored here.
-- ============================================================
INSERT INTO market_calendar (holiday_date, exchange, holiday_name, session_type)
VALUES
    -- Q1 2026
    ('2026-01-26', 'NSE', 'Republic Day',                           'CLOSED'),
    ('2026-03-03', 'NSE', 'Holi',                                   'CLOSED'),
    ('2026-03-26', 'NSE', 'Shri Ram Navami',                        'CLOSED'),
    ('2026-03-31', 'NSE', 'Shri Mahavir Jayanti',                   'CLOSED'),

    -- Q2 2026
    ('2026-04-03', 'NSE', 'Good Friday',                            'CLOSED'),
    ('2026-04-14', 'NSE', 'Dr. B.R. Ambedkar Jayanti',              'CLOSED'),
    ('2026-05-01', 'NSE', 'Maharashtra Day',                        'CLOSED'),
    ('2026-05-28', 'NSE', 'Bakri Id',                               'CLOSED'),

    -- Q3 2026
    ('2026-06-26', 'NSE', 'Muharram',                               'CLOSED'),
    ('2026-09-14', 'NSE', 'Ganesh Chaturthi',                       'CLOSED'),

    -- Q4 2026
    ('2026-10-02', 'NSE', 'Mahatma Gandhi Jayanti',                 'CLOSED'),
    ('2026-10-20', 'NSE', 'Dussehra',                               'CLOSED'),
    ('2026-11-08', 'NSE', 'Diwali - Muhurat Trading',               'MUHURAT'),
    ('2026-11-10', 'NSE', 'Diwali - Balipratipada',                 'CLOSED'),
    ('2026-11-24', 'NSE', 'Prakash Gurpurb Sri Guru Nanak Dev',     'CLOSED'),
    ('2026-12-25', 'NSE', 'Christmas',                              'CLOSED')
ON CONFLICT (holiday_date, exchange) DO NOTHING;

-- ============================================================
-- Market Hours Reference
-- As of June 30, 2026 (new F&O extension from Aug 3, 2026)
-- ============================================================
COMMENT ON TABLE market_calendar IS
'NSE trading holidays. Market hours (IST): Pre-open 09:00-09:15, 
Regular session 09:15-15:30, Closing auction 15:15-15:35.
F&O session extends to 15:40 effective Aug 3 2026.
Check system_config for configurable open/close times.';
