-- ============================================================
-- Migration 007: Target Allocations
-- Defines named allocation profiles and per-sector/stock targets.
-- The drift detector compares live weights against these targets.
-- ============================================================

-- Named allocation strategies (can have multiple profiles, one active)
CREATE TABLE IF NOT EXISTS allocation_profiles (
    id                  SERIAL PRIMARY KEY,
    user_id             TEXT NOT NULL DEFAULT 'default',
    profile_name        TEXT NOT NULL,
    description         TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_profile_user_name
        UNIQUE (user_id, profile_name)
);

-- Enforce only one active profile per user at the database level
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_profile
    ON allocation_profiles (user_id)
    WHERE is_active = TRUE;

-- Target weights per sector or individual stock
CREATE TABLE IF NOT EXISTS target_allocations (
    id                  SERIAL PRIMARY KEY,
    profile_id          INTEGER NOT NULL
                        REFERENCES allocation_profiles(id) ON DELETE CASCADE,

    -- What this target applies to
    allocation_type     TEXT NOT NULL
                        CHECK (allocation_type IN ('SECTOR', 'STOCK', 'CASH')),
    sector              TEXT,                     -- For SECTOR type
    instrument_token    INTEGER                   -- For STOCK type
                        REFERENCES instrument_master(instrument_token) ON DELETE RESTRICT,

    -- Target and tolerance
    target_weight_pct   NUMERIC(6, 3) NOT NULL,   -- e.g., 40.000 = 40%
    drift_threshold_pct NUMERIC(5, 2) NOT NULL DEFAULT 5.00,  -- Alert threshold

    -- Guardrails
    rebalance_priority  INTEGER DEFAULT 0,        -- Higher = rebalance this first
    max_concentration   NUMERIC(5, 2) DEFAULT 15.00,  -- Hard ceiling per stock

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_weight_range
        CHECK (target_weight_pct >= 0 AND target_weight_pct <= 100),
    CONSTRAINT chk_drift_threshold
        CHECK (drift_threshold_pct > 0 AND drift_threshold_pct <= 50),
    -- Ensure correct fields are filled based on type
    CONSTRAINT chk_allocation_target
        CHECK (
            (allocation_type = 'SECTOR' AND sector IS NOT NULL AND instrument_token IS NULL) OR
            (allocation_type = 'STOCK'  AND instrument_token IS NOT NULL AND sector IS NULL) OR
            (allocation_type = 'CASH'   AND sector IS NULL AND instrument_token IS NULL)
        )
);

CREATE INDEX IF NOT EXISTS idx_allocations_profile
    ON target_allocations (profile_id);

-- Seed a default profile for WJU490
INSERT INTO allocation_profiles (user_id, profile_name, description, is_active)
VALUES ('default', 'Balanced Growth', 'Default allocation strategy — customize in Streamlit UI', TRUE)
ON CONFLICT (user_id, profile_name) DO NOTHING;

-- Seed a CASH bucket (remaining allocation goes to cash)
INSERT INTO target_allocations (profile_id, allocation_type, target_weight_pct, drift_threshold_pct)
SELECT id, 'CASH', 10.000, 5.00
FROM allocation_profiles
WHERE user_id = 'default' AND profile_name = 'Balanced Growth'
ON CONFLICT DO NOTHING;
