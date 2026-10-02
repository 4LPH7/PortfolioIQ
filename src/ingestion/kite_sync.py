"""
PortfolioIQ — Kite Holdings & Margins Sync
Pulls current holdings and available margins from Zerodha Kite.
Runs daily at 09:15 AM IST via APScheduler.

All data is upserted (INSERT ... ON CONFLICT DO UPDATE)
so the function is idempotent and safe to re-run.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import text

from src.db.connection import get_db_session
from src.ingestion.kite_auth import get_authenticated_kite


def _classify_quantity_discrepancy(
    session: Any, token: int, delta: int, last_synced_at: datetime | None
) -> str:
    """Classify the reason for a quantity discrepancy using audit trails."""
    if last_synced_at:
        # Check order_audit_trail
        fills = session.execute(
            text("""
                SELECT SUM(CASE WHEN transaction_type = 'BUY' THEN requested_quantity
                                WHEN transaction_type = 'SELL' THEN -requested_quantity
                                ELSE 0 END) as net_fills
                FROM order_audit_trail
                WHERE instrument_token = :token
                  AND broker_status = 'COMPLETE'
                  AND (executed_at > :last_sync OR (executed_at IS NULL AND created_at > :last_sync))
            """),
            {"token": token, "last_sync": last_synced_at},
        ).fetchone()

        if fills and fills.net_fills == delta:
            return "TRADE_FILL"

    # Check corporate actions
    ca_check = session.execute(
        text("""
            SELECT action_type FROM corporate_actions
            WHERE instrument_token = :token
              AND ex_date >= NOW() - INTERVAL '14 days'
        """),
        {"token": token},
    ).fetchone()

    if ca_check:
        if ca_check.action_type == "SPLIT":
            return "CORPORATE_ACTION_SPLIT"
        elif ca_check.action_type == "BONUS":
            return "CORPORATE_ACTION_BONUS"

    return "DISCREPANCY"


def _record_reconciliation_log(
    session: Any,
    user_id: str,
    instrument_token: int,
    tradingsymbol: str,
    old_quantity: int,
    new_quantity: int,
    old_avg_price: float,
    new_avg_price: float,
    delta_quantity: int,
    reconciliation_reason: str,
) -> None:
    """Helper to insert records into holdings_reconciliation_log."""
    session.execute(
        text("""
            INSERT INTO holdings_reconciliation_log (
                user_id, instrument_token, tradingsymbol,
                old_quantity, new_quantity, old_avg_price, new_avg_price,
                delta_quantity, reconciliation_reason, detected_at
            ) VALUES (
                :user_id, :token, :symbol,
                :old_qty, :new_qty, :old_avg, :new_avg,
                :delta, :reason, NOW()
            )
        """),
        {
            "user_id": user_id,
            "token": instrument_token,
            "symbol": tradingsymbol,
            "old_qty": old_quantity,
            "new_qty": new_quantity,
            "old_avg": old_avg_price,
            "new_avg": new_avg_price,
            "delta": delta_quantity,
            "reason": reconciliation_reason,
        },
    )


def _ensure_instrument_exists(session, holding: dict) -> None:
    """
    Insert or update instrument_master record for the holding/position.
    Maps yf_ticker via tradingsymbol_to_yf_ticker so live quotes and analytics work immediately.
    """
    token = holding.get("instrument_token")
    if not token:
        return
    tradingsymbol = holding.get("tradingsymbol", "")
    exchange = holding.get("exchange", "NSE")
    isin = holding.get("isin")
    name = holding.get("name") or holding.get("company_name") or tradingsymbol

    from src.ingestion.instrument_mapper import tradingsymbol_to_yf_ticker
    yf_ticker = tradingsymbol_to_yf_ticker(tradingsymbol, exchange)

    # Check if (exchange, tradingsymbol) exists with different token
    existing_sym = session.execute(
        text("SELECT instrument_token FROM instrument_master WHERE exchange = :exchange AND tradingsymbol = :symbol"),
        {"exchange": exchange, "symbol": tradingsymbol},
    ).fetchone()

    if existing_sym and existing_sym.instrument_token != token:
        try:
            session.execute(
                text("""
                    UPDATE instrument_master
                    SET instrument_token = :token,
                        exchange_token = :token,
                        name = COALESCE(name, :name),
                        isin = COALESCE(:isin, isin),
                        yf_ticker = COALESCE(yf_ticker, :yf_ticker),
                        is_active = TRUE,
                        last_synced_at = NOW()
                    WHERE exchange = :exchange AND tradingsymbol = :symbol
                """),
                {
                    "token": token,
                    "name": name,
                    "isin": isin,
                    "yf_ticker": yf_ticker,
                    "exchange": exchange,
                    "symbol": tradingsymbol,
                },
            )
            return
        except Exception:
            logger.warning(
                f"Could not update instrument_token for {exchange}:{tradingsymbol}; keeping existing"
            )

    session.execute(
        text("""
            INSERT INTO instrument_master (
                instrument_token, exchange_token, tradingsymbol,
                name, isin, yf_ticker, exchange, instrument_type, is_active, last_synced_at
            )
            VALUES (
                :token, :token, :symbol,
                :name, :isin, :yf_ticker, :exchange, 'EQ', TRUE, NOW()
            )
            ON CONFLICT (instrument_token) DO UPDATE SET
                tradingsymbol = EXCLUDED.tradingsymbol,
                exchange = EXCLUDED.exchange,
                isin = COALESCE(EXCLUDED.isin, instrument_master.isin),
                name = COALESCE(instrument_master.name, EXCLUDED.name),
                yf_ticker = COALESCE(instrument_master.yf_ticker, EXCLUDED.yf_ticker),
                is_active = TRUE,
                last_synced_at = NOW()
        """),
        {
            "token": token,
            "symbol": tradingsymbol,
            "name": name,
            "isin": isin,
            "yf_ticker": yf_ticker,
            "exchange": exchange,
        },
    )


def sync_holdings(user_id: str = "default") -> int:
    """
    Pull holdings from Kite API and upsert into user_holdings table.
    Performs reconciliation against local holdings. Preserves imported
    (CSV/manual) data safely and reconciles matching securities seamlessly.

    Returns:
        Number of holdings upserted.
    """
    logger.info("Starting Kite holdings sync for user='{}'...", user_id)
    kite = get_authenticated_kite()

    try:
        raw_holdings: list[dict] = kite.holdings()
    except Exception as exc:
        logger.error("Failed to fetch holdings from Kite: {}", exc)
        raise

    if not raw_holdings:
        logger.warning("Kite returned 0 holdings. Portfolio may be empty.")

    upserted = 0
    with get_db_session() as session:
        # 1. Load local holdings
        local_rows = session.execute(
            text("""
                SELECT instrument_token, tradingsymbol, quantity, t1_quantity, average_price, last_synced_at,
                       COALESCE(data_source, 'KITE') AS data_source, id, exchange, isin
                FROM user_holdings
                WHERE user_id = :user_id
            """),
            {"user_id": user_id},
        ).fetchall()

        local_holdings_by_token = {
            r.instrument_token: {
                "id": r.id,
                "instrument_token": r.instrument_token,
                "tradingsymbol": r.tradingsymbol,
                "exchange": r.exchange,
                "isin": r.isin,
                "quantity": r.quantity,
                "t1_quantity": r.t1_quantity,
                "average_price": float(r.average_price),
                "last_synced_at": r.last_synced_at,
                "data_source": getattr(r, "data_source", "KITE") or "KITE",
            }
            for r in local_rows
        }

        local_holdings_by_symbol = {
            r.tradingsymbol.upper(): local_holdings_by_token[r.instrument_token]
            for r in local_rows
        }
        local_holdings_by_isin = {
            r.isin: local_holdings_by_token[r.instrument_token]
            for r in local_rows
            if r.isin
        }

        processed_tokens = set()

        for h in raw_holdings:
            instrument_token = h.get("instrument_token")
            if not instrument_token:
                logger.warning("Skipping holding with no instrument_token: {}", h)
                continue

            processed_tokens.add(instrument_token)

            # Ensure instrument exists in master FIRST before foreign key checks
            _ensure_instrument_exists(session, h)

            new_qty = h.get("quantity", 0)
            new_t1 = h.get("t1_quantity", 0)
            new_total = new_qty + new_t1
            new_avg_price = float(h.get("average_price", 0))
            tradingsymbol = h.get("tradingsymbol", "")
            isin = h.get("isin")

            # Match local holding: token match, or isin match, or symbol match
            local_h = (
                local_holdings_by_token.get(instrument_token)
                or (local_holdings_by_isin.get(isin) if isin else None)
                or local_holdings_by_symbol.get(tradingsymbol.upper())
            )

            # If local holding matched on symbol/isin with a different token (e.g. from CSV_IMPORT)
            if local_h and local_h["instrument_token"] != instrument_token:
                old_token = local_h["instrument_token"]
                old_source = local_h.get("data_source", "KITE")
                if old_source in ("CSV_IMPORT", "MANUAL", "legacy"):
                    logger.info(
                        "Reconciling imported holding {} (token {}) with live Kite token {}",
                        tradingsymbol,
                        old_token,
                        instrument_token,
                    )
                    session.execute(
                        text("""
                            DELETE FROM user_holdings
                            WHERE user_id = :user_id AND instrument_token = :old_token AND id = :old_id
                        """),
                        {"user_id": user_id, "old_token": old_token, "old_id": local_h["id"]},
                    )
                    processed_tokens.add(old_token)

            if not local_h:
                _record_reconciliation_log(
                    session,
                    user_id,
                    instrument_token,
                    tradingsymbol,
                    0,
                    new_total,
                    0.0,
                    new_avg_price,
                    new_total,
                    "INITIAL_SYNC",
                )
            else:
                old_qty = local_h["quantity"]
                old_t1 = local_h["t1_quantity"]
                old_total = old_qty + old_t1
                old_avg_price = local_h["average_price"]
                last_synced_at = local_h["last_synced_at"]

                delta = new_total - old_total

                if delta == 0:
                    if old_t1 > 0 and new_t1 == 0 and new_qty == old_total:
                        _record_reconciliation_log(
                            session,
                            user_id,
                            instrument_token,
                            tradingsymbol,
                            old_total,
                            new_total,
                            old_avg_price,
                            new_avg_price,
                            0,
                            "T1_SETTLEMENT",
                        )
                else:
                    reason = _classify_quantity_discrepancy(
                        session, instrument_token, delta, last_synced_at
                    )
                    if reason == "DISCREPANCY":
                        logger.warning(
                            "UNEXPLAINED QUANTITY JUMP for {}: old={}, new={}, delta={}",
                            tradingsymbol,
                            old_total,
                            new_total,
                            delta,
                        )
                    _record_reconciliation_log(
                        session,
                        user_id,
                        instrument_token,
                        tradingsymbol,
                        old_total,
                        new_total,
                        old_avg_price,
                        new_avg_price,
                        delta,
                        reason,
                    )

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
                        data_source,
                        last_synced_at,
                        updated_at
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
                        'KITE',
                        NOW(),
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
                        data_source         = 'KITE',
                        last_synced_at      = NOW(),
                        updated_at          = NOW()
                """),
                {
                    "user_id": user_id,
                    "instrument_token": instrument_token,
                    "tradingsymbol": h.get("tradingsymbol", ""),
                    "exchange": h.get("exchange", "NSE"),
                    "isin": h.get("isin"),
                    "quantity": h.get("quantity", 0),
                    "t1_quantity": h.get("t1_quantity", 0),
                    "opening_quantity": h.get("opening_quantity", 0),
                    "used_quantity": h.get("used_quantity", 0),
                    "authorised_quantity": h.get("authorised_quantity", 0),
                    "collateral_quantity": h.get("collateral_quantity", 0),
                    "average_price": float(h.get("average_price", 0)),
                    "last_price": float(h.get("last_price", 0)),
                    "close_price": float(h.get("close_price", 0)),
                    "pnl": float(h.get("pnl", 0)),
                    "day_change": float(h.get("day_change", 0)),
                    "day_change_pct": float(h.get("day_change_percentage", 0)),
                    "product": h.get("product", "CNC"),
                    "has_discrepancy": bool(h.get("discrepancy", False)),
                },
            )
            upserted += 1

            # Update live_prices with the fresh price from Kite holdings sync
            last_price = float(h.get("last_price", 0))
            close_price = float(h.get("close_price", 0))
            day_change = float(h.get("day_change", 0))
            day_change_pct = float(h.get("day_change_percentage", 0))
            if last_price > 0:
                session.execute(
                    text("""
                        INSERT INTO live_prices (
                            instrument_token, last_price, close_price,
                            change_absolute, change_percent, source, is_stale, last_updated
                        )
                        VALUES (
                            :token, :ltp, :close,
                            :chg, :chg_pct, 'kite', FALSE, NOW()
                        )
                        ON CONFLICT (instrument_token) DO UPDATE SET
                            last_price = EXCLUDED.last_price,
                            close_price = EXCLUDED.close_price,
                            change_absolute = EXCLUDED.change_absolute,
                            change_percent = EXCLUDED.change_percent,
                            source = 'kite',
                            is_stale = FALSE,
                            last_updated = NOW()
                    """),
                    {
                        "token": instrument_token,
                        "ltp": last_price,
                        "close": close_price,
                        "chg": day_change,
                        "chg_pct": day_change_pct,
                    },
                )

        # Check for missing holdings: reconcile KITE-sourced holdings that were liquidated
        # (never wipe user CSV or manual imports)
        for token, local_h in local_holdings_by_token.items():
            if local_h.get("data_source") in ("CSV_IMPORT", "MANUAL", "legacy"):
                continue
            if token not in processed_tokens:
                old_total = local_h["quantity"] + local_h["t1_quantity"]
                if old_total > 0:
                    reason = _classify_quantity_discrepancy(
                        session, token, -old_total, local_h["last_synced_at"]
                    )
                    if reason == "DISCREPANCY":
                        logger.warning(
                            "UNEXPLAINED QUANTITY JUMP (MISSING) for {}: old={}, new=0, delta={}",
                            local_h["tradingsymbol"],
                            old_total,
                            -old_total,
                        )
                    _record_reconciliation_log(
                        session,
                        user_id,
                        token,
                        local_h["tradingsymbol"],
                        old_total,
                        0,
                        local_h["average_price"],
                        0.0,
                        -old_total,
                        reason,
                    )

                    # Zero out missing holding
                    session.execute(
                        text("""
                            UPDATE user_holdings
                            SET quantity = 0, t1_quantity = 0, updated_at = NOW(), last_synced_at = NOW()
                            WHERE user_id = :user_id AND instrument_token = :token
                        """),
                        {"user_id": user_id, "token": token},
                    )

    logger.success("Holdings sync complete. {} holdings upserted.", upserted)
    return upserted


def sync_margins(user_id: str = "default") -> dict[str, Any]:
    """
    Pull equity margins from Kite API and upsert into user_margins table.

    Returns:
        Dictionary with equity margin data.
    """
    logger.info("Starting Kite margins sync for user='{}'...", user_id)
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
                "user_id": user_id,
                "available_cash": float(available.get("cash", 0)),
                "available_collateral": float(available.get("collateral", 0)),
                "opening_balance": float(available.get("opening_balance", 0)),
                "live_balance": float(available.get("live_balance", 0)),
                "intraday_payin": float(available.get("intraday_payin", 0)),
                "adhoc_margin": float(available.get("adhoc_margin", 0)),
                "utilised_debits": float(utilised.get("debits", 0)),
                "utilised_exposure": float(utilised.get("exposure", 0)),
                "utilised_span": float(utilised.get("span", 0)),
                "option_premium": float(utilised.get("option_premium", 0)),
                "holding_sales": float(utilised.get("holding_sales", 0)),
                "turnover": float(utilised.get("turnover", 0)),
                "m2m_realised": float(utilised.get("m2m_realised", 0)),
                "m2m_unrealised": float(utilised.get("m2m_unrealised", 0)),
                "payout": float(utilised.get("payout", 0)),
                "net": float(equity.get("net", 0)),
            },
        )

    available_cash = available.get("cash", 0)
    net = equity.get("net", 0)
    logger.success(
        "Margins sync complete for user '{}'. Available cash: ₹{:,.2f} | Net: ₹{:,.2f}",
        user_id,
        available_cash,
        net,
    )
    return equity


def sync_positions(user_id: str = "default") -> int:
    """
    Pull open positions from Kite API (net positions, both carry-forward and intraday)
    and upsert into user_positions table. Preserves CSV/manual position imports safely.

    Returns:
        Number of position rows upserted.
    """
    logger.info("Starting Kite positions sync for user='{}'...", user_id)
    kite = get_authenticated_kite()

    try:
        raw = kite.positions()
    except Exception as exc:
        logger.error("Failed to fetch positions from Kite: {}", exc)
        raise

    net_positions: list[dict] = raw.get("net", [])
    upserted = 0
    processed_tokens = set()

    with get_db_session() as session:
        for p in net_positions:
            instrument_token = p.get("instrument_token")
            if not instrument_token:
                logger.warning("Skipping position with no instrument_token: {}", p)
                continue

            processed_tokens.add(instrument_token)
            tradingsymbol = p.get("tradingsymbol", "")
            exchange = p.get("exchange", "NSE")
            product = p.get("product", "CNC")
            if product not in {"CNC", "MIS", "NRML", "MTF"}:
                product = "CNC"

            quantity = p.get("quantity", 0)
            average_price = float(p.get("average_price", 0))
            last_price = float(p.get("last_price", 0))
            pnl = float(p.get("pnl", 0))
            day_change = float(p.get("day_change", 0))
            day_change_pct = float(p.get("day_change_percentage", 0))

            # Ensure instrument exists in master FIRST
            _ensure_instrument_exists(
                session,
                {
                    "instrument_token": instrument_token,
                    "tradingsymbol": tradingsymbol,
                    "exchange": exchange,
                    "isin": p.get("isin"),
                },
            )

            session.execute(
                text("""
                    INSERT INTO user_positions (
                        user_id, instrument_token, tradingsymbol, exchange,
                        product, quantity, average_price, last_price,
                        pnl, day_change, day_change_pct,
                        data_source, last_synced_at, updated_at
                    )
                    VALUES (
                        :user_id, :instrument_token, :tradingsymbol, :exchange,
                        :product, :quantity, :average_price, :last_price,
                        :pnl, :day_change, :day_change_pct,
                        'KITE', NOW(), NOW()
                    )
                    ON CONFLICT (user_id, instrument_token, product) DO UPDATE SET
                        quantity         = EXCLUDED.quantity,
                        average_price    = EXCLUDED.average_price,
                        last_price       = EXCLUDED.last_price,
                        pnl              = EXCLUDED.pnl,
                        day_change       = EXCLUDED.day_change,
                        day_change_pct   = EXCLUDED.day_change_pct,
                        data_source      = 'KITE',
                        last_synced_at   = NOW(),
                        updated_at       = NOW()
                """),
                {
                    "user_id": user_id,
                    "instrument_token": instrument_token,
                    "tradingsymbol": tradingsymbol,
                    "exchange": exchange,
                    "product": product,
                    "quantity": quantity,
                    "average_price": average_price,
                    "last_price": last_price,
                    "pnl": pnl,
                    "day_change": day_change,
                    "day_change_pct": day_change_pct,
                },
            )
            upserted += 1

            if last_price > 0:
                session.execute(
                    text("""
                        INSERT INTO live_prices (
                            instrument_token, last_price, close_price,
                            change_absolute, change_percent, source, is_stale, last_updated
                        )
                        VALUES (
                            :token, :ltp, :close,
                            :chg, :chg_pct, 'kite', FALSE, NOW()
                        )
                        ON CONFLICT (instrument_token) DO UPDATE SET
                            last_price = EXCLUDED.last_price,
                            close_price = EXCLUDED.close_price,
                            change_absolute = EXCLUDED.change_absolute,
                            change_percent = EXCLUDED.change_percent,
                            source = 'kite',
                            is_stale = FALSE,
                            last_updated = NOW()
                    """),
                    {
                        "token": instrument_token,
                        "ltp": last_price,
                        "close": float(p.get("close_price", last_price)),
                        "chg": day_change,
                        "chg_pct": day_change_pct,
                    },
                )

        # Zero out any KITE-sourced positions in DB that are no longer reported open by Kite
        # (Never touch CSV_IMPORT positions)
        if processed_tokens:
            session.execute(
                text("""
                    UPDATE user_positions
                    SET quantity = 0, pnl = 0, updated_at = NOW(), last_synced_at = NOW()
                    WHERE user_id = :user_id
                      AND data_source = 'KITE'
                      AND quantity <> 0
                      AND instrument_token NOT IN :tokens
                """),
                {"user_id": user_id, "tokens": tuple(processed_tokens)},
            )
        else:
            session.execute(
                text("""
                    UPDATE user_positions
                    SET quantity = 0, pnl = 0, updated_at = NOW(), last_synced_at = NOW()
                    WHERE user_id = :user_id
                      AND data_source = 'KITE'
                      AND quantity <> 0
                """),
                {"user_id": user_id},
            )

    logger.success("Positions sync complete. {} positions upserted.", upserted)
    return upserted


def run_start_of_day_sync(user_id: str = "default") -> dict[str, Any]:
    """
    Full start-of-day sync: holdings + positions + margins.
    Called manually or by APScheduler at 09:15 AM IST on market days.

    Returns:
        Summary dict with sync results.
    """
    logger.info("=" * 50)
    logger.info("START OF DAY SYNC — {}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 50)

    results: dict[str, Any] = {"ok": True}
    try:
        results["holdings_upserted"] = sync_holdings(user_id=user_id)
    except Exception as exc:
        logger.error("Holdings sync failed: {}", exc)
        results["holdings_error"] = str(exc)
        results["ok"] = False

    try:
        results["positions_upserted"] = sync_positions(user_id=user_id)
    except Exception as exc:
        logger.error("Positions sync failed: {}", exc)
        results["positions_error"] = str(exc)

    try:
        margins = sync_margins(user_id=user_id)
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
