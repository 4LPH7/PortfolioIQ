-- ============================================================
-- Migration 013: Partition Management Functions
-- Auto-creates and drops daily partitions for price_history.
-- Called by APScheduler daily at ~8 AM IST.
-- ============================================================

-- Create partition for a specific date
CREATE OR REPLACE FUNCTION create_price_partition(target_date DATE DEFAULT CURRENT_DATE)
RETURNS TEXT AS $$
DECLARE
    partition_name TEXT;
    next_date DATE;
BEGIN
    next_date := target_date + INTERVAL '1 day';
    partition_name := 'price_history_' || to_char(target_date, 'YYYY_MM_DD');

    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF price_history
         FOR VALUES FROM (%L) TO (%L)',
        partition_name,
        target_date,
        next_date
    );

    RETURN partition_name;
END;
$$ LANGUAGE plpgsql;

-- Create today's + next N days partitions (safety margin)
CREATE OR REPLACE FUNCTION create_upcoming_partitions(days_ahead INTEGER DEFAULT 3)
RETURNS TABLE(partition_name TEXT, created_at_ts TIMESTAMPTZ) AS $$
DECLARE
    target_date DATE;
    pname TEXT;
BEGIN
    FOR i IN 0..days_ahead LOOP
        target_date := CURRENT_DATE + (i || ' days')::INTERVAL;
        pname := create_price_partition(target_date);
        partition_name := pname;
        created_at_ts := NOW();
        RETURN NEXT;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Drop partitions older than retention_days
CREATE OR REPLACE FUNCTION drop_old_price_partitions(retention_days INTEGER DEFAULT 90)
RETURNS TABLE(dropped_partition TEXT, partition_date DATE) AS $$
DECLARE
    cutoff_date DATE;
    rec RECORD;
    pdate DATE;
    pname TEXT;
BEGIN
    cutoff_date := CURRENT_DATE - (retention_days || ' days')::INTERVAL;

    FOR rec IN
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename LIKE 'price_history_%'
          AND tablename ~ 'price_history_\d{4}_\d{2}_\d{2}'
        ORDER BY tablename ASC
    LOOP
        BEGIN
            -- Parse date from partition name: price_history_2026_06_30 → 2026-06-30
            pdate := to_date(
                substring(rec.tablename FROM 'price_history_(\d{4}_\d{2}_\d{2})'),
                'YYYY_MM_DD'
            );

            IF pdate < cutoff_date THEN
                pname := rec.tablename;
                EXECUTE format('DROP TABLE IF EXISTS %I', pname);
                dropped_partition := pname;
                partition_date := pdate;
                RETURN NEXT;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'Could not process partition %: %', rec.tablename, SQLERRM;
        END;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Convenience view: List all existing price_history partitions
CREATE OR REPLACE VIEW v_price_partitions AS
SELECT
    tablename AS partition_name,
    to_date(
        substring(tablename FROM 'price_history_(\d{4}_\d{2}_\d{2})'),
        'YYYY_MM_DD'
    ) AS partition_date,
    pg_size_pretty(pg_total_relation_size(quote_ident(tablename))) AS size
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename LIKE 'price_history_%'
  AND tablename ~ 'price_history_\d{4}_\d{2}_\d{2}'
ORDER BY partition_date DESC;
