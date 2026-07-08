"""
PortfolioIQ — Kite Holdings & Margins Sync
Pulls current holdings and available margins from Zerodha Kite.
Runs daily at 09:15 AM IST via APScheduler.

All data is upserted (INSERT ... ON CONFLICT DO UPDATE)
so the function is idempotent and safe to re-run.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from loguru import logger
from sqlalchemy import text

from src.config.settings import get_settings
from src.db.connection import get_db_session
from src.ingestion.kite_auth import get_authenticated_kite


def sync_holdings() -> int:
    """
    Pull holdings from Kite API and upsert into user_holdings table.

    Returns:
        Number of holdings upserted.
    """
    logger.info("Starting Kite holdings sync...")
    kite = get_authenticated_kite()

    try:
        raw_holdings: list[dict] = kite.holdings()
    except Exception as exc:
        logger.error("Failed to fetch holdings from Kite: {}", exc)
        raise

    if not raw_holdings:
        logger.warning("Kite returned 0 holdings. Portfolio may be empty.")
        return 0

    logger.info("Fetched {} holdings from Kite", len(raw_holdings))

    upserted = 0
    with get_db_session() as session:
        for h in raw_holdings:
            instrument_token = h.get("instrument_token")
            if not instrument_token:
                logger.warning("Skipping holding with no instrument_token: {}", h)
                continue

            # Ensure instrument exists in master (upsert minimal record)
            _ensure_instrument_exists(session, h)

            # Upsert the holding
            session.execute(
                text("""
                    INSERT INTO user_holdings (
                        user_id,
                        instrument_token,
                        tradingsymbol,
                        exchange,
                        isin,
                        quantity,
                        t1_quantity,
                        opening_quantity,
                        used_quantity,
                        authorised_quantity,
                        collateral_quantity,
                        average_price,
                        last_price,
                        close_price,
                        pnl,
                        day_change,
                        day_change_pct,
                        product,
                        has_discrepancy,
                        last_synced_at
                    )
                    VALUES (
                        :user_id,
                        :instrument_token,
                        :tradingsymbol,
                        :exchange,
                        :isin,
                        :quantity,
                        :t1_quantity,
                        :opening_quantity,
                        :used_quantity,
                        :authorised_quantity,
                        :collateral_quantity,
                        :average_price,
                        :last_price,
                        :close_price,
                        :pnl,
                        :day_change,
                        :day_change_pct,
                        :product,
                        :has_discrepancy,
                        NOW()
                    )
                    ON CONFLICT (user_id, instrument_token, product) DO UPDATE SET
                        quantity            = EXCLUDED.quantity,
                        t1_quantity         = EXCLUDED.t1_quantity,
                        opening_quantity    = EXCLUDED.opening_quantity,
                        used_quantity       = EXCLUDED.used_quantity,
                        authorised_quantity = EXCLUDED.authorised_quantity,
                        collateral_quantity = EXCLUDED.collateral_quantity,
                        average_price       = EXCLUDED.average_price,
                        last_price          = EXCLUDED.last_price,
                        close_price         = EXCLUDED.close_price,
                        pnl                 = EXCLUDED.pnl,
                        day_change          = EXCLUDED.day_change,
                        day_change_pct      = EXCLUDED.day_change_pct,
                        has_discrepancy     = EXCLUDED.has_discrepancy,
                        last_synced_at      = NOW(),
                        updated_at          = NOW()
                """),
                {
                    "user_id":              "default",
                    "instrument_token":     instrument_token,
                    "tradingsymbol":        h.get("tradingsymbol", ""),
                    "exchange":             h.get("exchange", "NSE"),
                    "isin":                 h.get("isin"),
                    "quantity":             h.get("quantity", 0),
                    "t1_quantity":          h.get("t1_quantity", 0),
                    "opening_quantity":     h.get("opening_quantity", 0),
                    "used_quantity":        h.get("used_quantity", 0),
                    "authorised_quantity":  h.get("authorised_quantity", 0),
                    "collateral_quantity":  h.get("collateral_quantity", 0),
                    "average_price":        float(h.get("average_price", 0)),
                    "last_price":           float(h.get("last_price", 0)),
                    "close_price":          float(h.get("close_price", 0)),
                    "pnl":                  float(h.get("pnl", 0)),
                    "day_change":           float(h.get("day_change", 0)),
                    "day_change_pct":       float(h.get("day_change_percentage", 0)),
                    "product":              h.get("product", "CNC"),
                    "has_discrepancy":      bool(h.get("discrepancy", False)),
                },
            )
            upserted += 1

    logger.success("Holdings sync complete. {} holdings upserted.", upserted)
    return upserted


def sync_margins() -> dict[str, Any]:
    """
    Pull equity margins from Kite API and upsert into user_margins table.

    Returns:
        Dictionary with equity margin data.
    """
    logger.info("Starting Kite margins sync...")
    kite = get_authenticated_kite()

    try:
        all_margins = kite.margins()
    except Exception as exc:
        logger.error("Failed to fetch margins from Kite: {}", exc)
        raise

    equity = all_margins.get("equity", {})
    available = equity.get("available", {})
    utilised = equity.get("utilised", {})

    with get_db_session() as session:
        session.execute(
            text("""
                INSERT INTO user_margins (
                    user_id,
                    segment,
                    available_cash,
                    available_collateral,
                    opening_balance,
                    live_balance,
                    intraday_payin,
                    adhoc_margin,
                    utilised_debits,
                    utilised_exposure,
                    utilised_span,
                    option_premium,
                    holding_sales,
                    turnover,
                    m2m_realised,
                    m2m_unrealised,
                    payout,
                    net,
                    synced_at
                )
                VALUES (
                    :user_id, 'equity',
                    :available_cash,
                    :available_collateral,
                    :opening_balance,
                    :live_balance,
                    :intraday_payin,
                    :adhoc_margin,
                    :utilised_debits,
                    :utilised_exposure,
                    :utilised_span,
                    :option_premium,
                    :holding_sales,
                    :turnover,
                    :m2m_realised,
                    :m2m_unrealised,
                    :payout,
                    :net,
                    NOW()
                )
                ON CONFLICT (user_id, segment) DO UPDATE SET
                    available_cash       = EXCLUDED.available_cash,
                    available_collateral = EXCLUDED.available_collateral,
                    opening_balance      = EXCLUDED.opening_balance,
                    live_balance         = EXCLUDED.live_balance,
                    intraday_payin       = EXCLUDED.intraday_payin,
                    adhoc_margin         = EXCLUDED.adhoc_margin,
                    utilised_debits      = EXCLUDED.utilised_debits,
                    utilised_exposure    = EXCLUDED.utilised_exposure,
                    utilised_span        = EXCLUDED.utilised_span,
                    option_premium       = EXCLUDED.option_premium,
                    holding_sales        = EXCLUDED.holding_sales,
                    turnover             = EXCLUDED.turnover,
                    m2m_realised         = EXCLUDED.m2m_realised,
                    m2m_unrealised       = EXCLUDED.m2m_unrealised,
                    payout               = EXCLUDED.payout,
                    net                  = EXCLUDED.net,
                    synced_at            = NOW()
            """),
            {
                "user_id":              "default",
                "available_cash":       float(available.get("cash", 0)),
                "available_collateral": float(available.get("collateral", 0)),
                "opening_balance":      float(available.get("opening_balance", 0)),
                "live_balance":         float(available.get("live_balance", 0)),
                "intraday_payin":       float(available.get("intraday_payin", 0)),
                "adhoc_margin":         float(available.get("adhoc_margin", 0)),
                "utilised_debits":      float(utilised.get("debits", 0)),
                "utilised_exposure":    float(utilised.get("exposure", 0)),
                "utilised_span":        float(utilised.get("span", 0)),
                "option_premium":       float(utilised.get("option_premium", 0)),
                "holding_sales":        float(utilised.get("holding_sales", 0)),
                "turnover":             float(utilised.get("turnover", 0)),
                "m2m_realised":         float(utilised.get("m2m_realised", 0)),
                "m2m_unrealised":       float(utilised.get("m2m_unrealised", 0)),
                "payout":               float(utilised.get("payout", 0)),
                "net":                  float(equity.get("net", 0)),
            },
        )

    available_cash = available.get("cash", 0)
    net = equity.get("net", 0)
    logger.success(
        "Margins sync complete. Available cash: ₹{:,.2f} | Net: ₹{:,.2f}",
        available_cash, net
    )
    return equity


def _ensure_instrument_exists(session, holding: dict) -> None:
    """
    Insert a minimal instrument_master record if one doesn't exist.
    The instrument_mapper will fill in yf_ticker and sector later.
    """
    session.execute(
        text("""
            INSERT INTO instrument_master (
                instrument_token, exchange_token, tradingsymbol,
                name, isin, exchange, instrument_type, is_active
            )
            VALUES (
                :token, :exchange_token, :tradingsymbol,
                :name, :isin, :exchange, 'EQ', TRUE
            )
            ON CONFLICT (instrument_token) DO NOTHING
        """),
        {
            "token":          holding.get("instrument_token"),
            "exchange_token": holding.get("instrument_token"),  # fallback
            "tradingsymbol":  holding.get("tradingsymbol", ""),
            "name":           holding.get("tradingsymbol", ""),  # will be enriched later
            "isin":           holding.get("isin"),
            "exchange":       holding.get("exchange", "NSE"),
        },
    )


def run_start_of_day_sync() -> dict[str, Any]:
    """
    Full start-of-day sync: holdings + margins.
    Called by APScheduler at 09:15 AM IST on market days.

    Returns:
        Summary dict with sync results.
    """
    logger.info("=" * 50)
    logger.info("START OF DAY SYNC — {}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 50)

    results = {}
    try:
        results["holdings_upserted"] = sync_holdings()
    except Exception as exc:
        logger.error("Holdings sync failed: {}", exc)
        results["holdings_error"] = str(exc)

    try:
        margins = sync_margins()
        results["available_cash"] = margins.get("available", {}).get("cash", 0)
        results["net_margin"] = margins.get("net", 0)
    except Exception as exc:
        logger.error("Margins sync failed: {}", exc)
        results["margins_error"] = str(exc)

    logger.info("Start-of-day sync complete: {}", results)
    return results


if __name__ == "__main__":
    """Test sync manually: python -m src.ingestion.kite_sync"""
    result = run_start_of_day_sync()
    print(result)
