-- ============================================================
-- Migration 028: price alerts you set, and news headlines for holdings
-- After trader-alerts and trader-news in Jakeschincariol/day-trader-skill (MIT).
-- Both are information only: nothing here can place, size or cancel an order.
-- ============================================================

CREATE TABLE IF NOT EXISTS price_alerts (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             TEXT NOT NULL DEFAULT 'default',
    tradingsymbol       TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('above', 'below', 'pct_move')),
    price               NUMERIC(15, 2),            -- level for above / below
    pct                 NUMERIC(6, 2),             -- move from the previous close for pct_move
    direction           TEXT NOT NULL DEFAULT 'either' CHECK (direction IN ('up', 'down', 'either')),
    note                TEXT,
    rearm               BOOLEAN NOT NULL DEFAULT FALSE,  -- ring again after the price crosses back
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    armed               BOOLEAN NOT NULL DEFAULT TRUE,   -- false between a ring and the cross back
    triggered_at        TIMESTAMPTZ,
    triggered_price     NUMERIC(15, 2),
    triggered_on        DATE,                      -- IST day: pct_move rings at most once a day
    last_price          NUMERIC(15, 2),
    last_quote_source   TEXT,
    last_checked_at     TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_price_alert_value CHECK (
        (kind = 'pct_move' AND pct > 0) OR (kind <> 'pct_move' AND price > 0)
    )
);

CREATE INDEX IF NOT EXISTS idx_price_alerts_user ON price_alerts (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_alerts_active ON price_alerts (active) WHERE active;

-- Headlines fetched on a schedule, never on a page load. Titles are untrusted text.
CREATE TABLE IF NOT EXISTS news_items (
    id                  BIGSERIAL PRIMARY KEY,
    tradingsymbol       TEXT NOT NULL,
    title               TEXT NOT NULL,
    link                TEXT NOT NULL,
    source              TEXT,
    published_at        TIMESTAMPTZ,
    tags                TEXT[] NOT NULL DEFAULT '{}',
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_news_symbol_link UNIQUE (tradingsymbol, link)
);

CREATE INDEX IF NOT EXISTS idx_news_published ON news_items (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_symbol ON news_items (tradingsymbol, published_at DESC);
