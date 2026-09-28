-- ============================================================
-- Migration 016: Market Calendar Enhancements
-- ============================================================

ALTER TABLE market_calendar
    ADD COLUMN IF NOT EXISTS segment TEXT NOT NULL DEFAULT 'equity',
    ADD COLUMN IF NOT EXISTS is_trading_holiday BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS special_session_open TIME,
    ADD COLUMN IF NOT EXISTS special_session_close TIME,
    ADD COLUMN IF NOT EXISTS description TEXT;

-- Seed Diwali Muhurat 2026 Special Session (18:15 to 19:15 IST)
UPDATE market_calendar
SET is_trading_holiday   = FALSE,
    special_session_open  = '18:15:00',
    special_session_close = '19:15:00',
    description           = 'Diwali Laxmi Pujan (Muhurat Trading evening session 18:15-19:15 IST)'
WHERE holiday_date = '2026-11-08' AND exchange = 'NSE';

-- Update descriptions for full holidays
UPDATE market_calendar
SET description = holiday_name || ' (Exchange Closed)'
WHERE description IS NULL AND is_trading_holiday = TRUE;

CREATE INDEX IF NOT EXISTS idx_calendar_segment_date
    ON market_calendar (segment, holiday_date);
