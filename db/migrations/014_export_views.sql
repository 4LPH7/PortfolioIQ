-- ============================================================
-- Migration 014: Export Views
-- Analytics views joining holdings + live prices.
-- Used by: Streamlit dashboard, drift detector, Tableau export.
-- ============================================================

-- ============================================================
-- VIEW: v_portfolio_snapshot
-- Primary dashboard view — joins holdings with live prices.
-- Returns one row per holding with full valuation metrics.
-- ============================================================
CREATE OR REPLACE VIEW v_portfolio_snapshot AS
SELECT
    -- Identity
    h.user_id,
    h.instrument_token,
    h.tradingsymbol,
    h.exchange,
    im.name                                         AS company_name,
    im.sector,
    im.industry,
    im.market_cap_category,
    im.yf_ticker,

    -- Quantities
    h.quantity + h.t1_quantity                      AS total_quantity,
    h.quantity                                      AS settled_quantity,
    h.t1_quantity,
    h.used_quantity,
    h.authorised_quantity,

    -- Prices
    h.average_price,
    COALESCE(lp.last_price, h.last_price)           AS current_price,
    lp.open_price,
    lp.high_price,
    lp.low_price,
    COALESCE(lp.close_price, h.close_price)         AS prev_close,

    -- Valuation
    h.average_price * (h.quantity + h.t1_quantity)  AS invested_value,
    COALESCE(lp.last_price, h.last_price)
        * (h.quantity + h.t1_quantity)              AS current_value,

    -- Total P&L
    (COALESCE(lp.last_price, h.last_price) - h.average_price)
        * (h.quantity + h.t1_quantity)              AS total_pnl,

    -- Total return %
    CASE
        WHEN h.average_price > 0
        THEN ROUND(
            ((COALESCE(lp.last_price, h.last_price) - h.average_price)
             / h.average_price) * 100,
            2)
        ELSE 0
    END                                             AS total_return_pct,

    -- Day P&L
    (COALESCE(lp.last_price, h.last_price)
        - COALESCE(lp.close_price, h.close_price))
        * (h.quantity + h.t1_quantity)              AS day_pnl,

    -- Day change
    COALESCE(lp.change_absolute, h.day_change)      AS day_change,
    COALESCE(lp.change_percent,  h.day_change_pct)  AS day_change_pct,

    -- Data freshness
    lp.last_updated                                 AS price_updated_at,
    lp.is_stale                                     AS price_is_stale,
    h.last_synced_at                                AS holdings_synced_at,

    -- Metadata
    h.product,
    h.first_buy_date,
    h.has_discrepancy

FROM user_holdings h
JOIN  instrument_master im ON h.instrument_token = im.instrument_token
LEFT JOIN live_prices   lp ON h.instrument_token = lp.instrument_token
WHERE (h.quantity + h.t1_quantity) > 0;

-- ============================================================
-- VIEW: v_portfolio_summary
-- Single-row aggregate: total AUM, day P&L, total return, etc.
-- Used for the top KPI cards in Streamlit.
-- ============================================================
CREATE OR REPLACE VIEW v_portfolio_summary AS
SELECT
    user_id,
    COUNT(*)                            AS num_holdings,
    ROUND(SUM(invested_value),    2)    AS total_invested,
    ROUND(SUM(current_value),     2)    AS total_aum,
    ROUND(SUM(total_pnl),         2)    AS total_pnl,
    ROUND(SUM(day_pnl),           2)    AS day_pnl,
    CASE
        WHEN SUM(invested_value) > 0
        THEN ROUND((SUM(total_pnl) / SUM(invested_value)) * 100, 2)
        ELSE 0
    END                                 AS total_return_pct,
    CASE
        WHEN SUM(current_value) > 0
        THEN ROUND((SUM(day_pnl) / SUM(current_value)) * 100, 2)
        ELSE 0
    END                                 AS day_return_pct,
    MAX(price_updated_at)               AS last_price_update,
    MAX(holdings_synced_at)             AS last_holdings_sync
FROM v_portfolio_snapshot
GROUP BY user_id;

-- ============================================================
-- VIEW: v_sector_allocation
-- Current vs target sector weights for drift detection.
-- ============================================================
CREATE OR REPLACE VIEW v_sector_allocation AS
WITH portfolio_total AS (
    SELECT user_id, SUM(current_value) AS total_aum
    FROM v_portfolio_snapshot
    GROUP BY user_id
),
sector_values AS (
    SELECT
        ps.user_id,
        COALESCE(ps.sector, 'Unclassified')         AS sector,
        COUNT(*)                                    AS num_holdings,
        SUM(ps.current_value)                       AS sector_value
    FROM v_portfolio_snapshot ps
    GROUP BY ps.user_id, ps.sector
)
SELECT
    sv.user_id,
    sv.sector,
    sv.num_holdings,
    ROUND(sv.sector_value, 2)                       AS sector_value,
    pt.total_aum,
    CASE
        WHEN pt.total_aum > 0
        THEN ROUND((sv.sector_value / pt.total_aum) * 100, 3)
        ELSE 0
    END                                             AS current_weight_pct,
    ta.target_weight_pct,
    ta.drift_threshold_pct,
    CASE
        WHEN pt.total_aum > 0 AND ta.target_weight_pct IS NOT NULL
        THEN ROUND(
            ((sv.sector_value / pt.total_aum) * 100) - ta.target_weight_pct,
            3)
        ELSE NULL
    END                                             AS drift_pct,
    CASE
        WHEN pt.total_aum > 0 AND ta.target_weight_pct IS NOT NULL
        AND ABS(((sv.sector_value / pt.total_aum) * 100) - ta.target_weight_pct)
            > ta.drift_threshold_pct
        THEN TRUE
        ELSE FALSE
    END                                             AS is_drifted
FROM sector_values sv
JOIN portfolio_total pt ON sv.user_id = pt.user_id
LEFT JOIN allocation_profiles ap
    ON sv.user_id = ap.user_id AND ap.is_active = TRUE
LEFT JOIN target_allocations ta
    ON ap.id = ta.profile_id
    AND ta.allocation_type = 'SECTOR'
    AND ta.sector = sv.sector
ORDER BY ABS(COALESCE(
    CASE
        WHEN pt.total_aum > 0 AND ta.target_weight_pct IS NOT NULL
        THEN ((sv.sector_value / pt.total_aum) * 100) - ta.target_weight_pct
        ELSE NULL
    END, 0)) DESC;

-- ============================================================
-- VIEW: v_tax_summary
-- STCG vs LTCG lot summary per holding for the tax guard.
-- ============================================================
CREATE OR REPLACE VIEW v_tax_summary AS
SELECT
    h.user_id,
    h.tradingsymbol,
    h.exchange,
    h.average_price,
    COALESCE(lp.last_price, h.last_price)           AS current_price,
    -- LTCG lots (held > 365 days)
    SUM(CASE WHEN tl.buy_date < CURRENT_DATE - INTERVAL '365 days'
             THEN tl.remaining_quantity ELSE 0 END) AS ltcg_quantity,
    SUM(CASE WHEN tl.buy_date < CURRENT_DATE - INTERVAL '365 days'
             THEN tl.remaining_quantity * tl.buy_price ELSE 0 END) AS ltcg_cost_basis,
    -- STCG lots (held <= 365 days)
    SUM(CASE WHEN tl.buy_date >= CURRENT_DATE - INTERVAL '365 days'
             THEN tl.remaining_quantity ELSE 0 END) AS stcg_quantity,
    SUM(CASE WHEN tl.buy_date >= CURRENT_DATE - INTERVAL '365 days'
             THEN tl.remaining_quantity * tl.buy_price ELSE 0 END) AS stcg_cost_basis,
    -- Nearest STCG to LTCG conversion date
    MIN(CASE WHEN tl.buy_date >= CURRENT_DATE - INTERVAL '365 days'
                  AND tl.remaining_quantity > 0
             THEN tl.buy_date + INTERVAL '365 days' ELSE NULL END) AS next_ltcg_date
FROM user_holdings h
JOIN holding_tax_lots tl ON h.id = tl.holding_id
LEFT JOIN live_prices lp ON h.instrument_token = lp.instrument_token
WHERE tl.remaining_quantity > 0
GROUP BY h.user_id, h.tradingsymbol, h.exchange, h.average_price,
         lp.last_price, h.last_price;
