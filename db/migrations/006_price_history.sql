-- ============================================================
-- Migration 006: Price History
-- Append-only time-series archive of intraday price snapshots.
-- Partitioned by date (RANGE) for efficient data retention.
-- Used for EOD Tableau exports and historical analytics.
-- ============================================================

CREATE TABLE IF NOT EXISTS price_history (
    id                  BIGSERIAL,
    instrument_token    INTEGER NOT NULL,           -- Intentionally no FK (partition tables)

    -- Price snapshot
    last_price          NUMERIC(15, 2) NOT NULL,
    open_price          NUMERIC(15, 2),
    high_price          NUMERIC(15, 2),
    low_price           NUMERIC(15, 2),
    close_price         NUMERIC(15, 2),
    volume              BIGINT,
    change_percent      NUMERIC(8, 4),

    -- Metadata
    source              TEXT NOT NULL DEFAULT 'yahoo',
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Partition key must be part of the primary key
    PRIMARY KEY (id, recorded_at)
) PARTITION BY RANGE (recorded_at);

-- BRIN index: Compact time-range index for append-only data
-- (BRIN = Block Range INdex, ideal when data is physically ordered by time)
CREATE INDEX IF NOT EXISTS idx_price_history_ts_brin
    ON price_history USING BRIN (recorded_at)
    WITH (pages_per_range = 32);

-- B-tree composite index: Lookup price history for a specific instrument
CREATE INDEX IF NOT EXISTS idx_price_history_token_ts
    ON price_history (instrument_token, recorded_at DESC);

-- ============================================================
-- Create initial partitions (today + next 2 days as safety margin)
-- The partition management function (migration 013) handles
-- ongoing partition creation via APScheduler.
-- ============================================================

DO $$
DECLARE
    target_date DATE;
    partition_name TEXT;
BEGIN
    FOR i IN 0..2 LOOP
        target_date := CURRENT_DATE + (i || ' days')::INTERVAL;
        partition_name := 'price_history_' || to_char(target_date, 'YYYY_MM_DD');

        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF price_history
             FOR VALUES FROM (%L) TO (%L)',
            partition_name,
            target_date,
            target_date + INTERVAL '1 day'
        );

        RAISE NOTICE 'Created partition: %', partition_name;
    END LOOP;
END;
$$;
