"""
PortfolioIQ — Corporate Actions Processor
Handles stock splits, bonuses, and dividends by adjusting
FIFO tax lots and average prices.

Supported actions:
    - SPLIT: Multiplies quantity, divides price by split ratio
    - BONUS: Adds new lots at zero cost basis
    - DIVIDEND: Records cash dividend (informational only)

Data source: corporate_actions table (manually populated or
scraped from exchange announcements).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from loguru import logger
from sqlalchemy import text

from src.db.connection import get_db_session, execute_sql


@dataclass
class CorporateAction:
    """Represents a single corporate action event."""
    id: int
    instrument_token: int
    tradingsymbol: str
    action_type: str  # 'SPLIT' | 'BONUS' | 'DIVIDEND'
    ex_date: date
    ratio_from: int  # e.g., split 1:5 → from=1, to=5
    ratio_to: int
    old_face_value: Decimal | None
    new_face_value: Decimal | None
    dividend_per_share: Decimal | None
    is_processed: bool


def get_pending_actions(user_id: str = "default") -> list[CorporateAction]:
    """
    Fetch unprocessed corporate actions for holdings in the portfolio.
    Only returns actions where ex_date has passed.
    """
    rows = execute_sql("""
        SELECT
            ca.id,
            ca.instrument_token,
            ca.tradingsymbol,
            ca.action_type,
            ca.ex_date,
            ca.ratio_from,
            ca.ratio_to,
            ca.old_face_value,
            ca.new_face_value,
            ca.dividend_per_share,
            ca.is_processed
        FROM corporate_actions ca
        JOIN user_holdings h
            ON ca.instrument_token = h.instrument_token
            AND h.user_id = :uid
        WHERE ca.is_processed = FALSE
          AND ca.ex_date <= CURRENT_DATE
        ORDER BY ca.ex_date ASC
    """, {"uid": user_id})

    return [
        CorporateAction(
            id=r["id"],
            instrument_token=r["instrument_token"],
            tradingsymbol=r["tradingsymbol"],
            action_type=r["action_type"],
            ex_date=r["ex_date"],
            ratio_from=r["ratio_from"],
            ratio_to=r["ratio_to"],
            old_face_value=Decimal(str(r["old_face_value"])) if r.get("old_face_value") else None,
            new_face_value=Decimal(str(r["new_face_value"])) if r.get("new_face_value") else None,
            dividend_per_share=Decimal(str(r["dividend_per_share"])) if r.get("dividend_per_share") else None,
            is_processed=r["is_processed"],
        )
        for r in rows
    ]


def process_split(action: CorporateAction) -> dict[str, Any]:
    """
    Process a stock split. Example: 1:5 split means
    each old share becomes 5 new shares, price divides by 5.

    Adjusts:
        - holding_tax_lots: multiply quantity, divide buy_price
        - user_holdings: multiply quantity, divide average_price
    """
    ratio = Decimal(str(action.ratio_to)) / Decimal(str(action.ratio_from))
    logger.info(
        "Processing SPLIT for {} — ratio {}:{} (multiplier: {})",
        action.tradingsymbol, action.ratio_from, action.ratio_to, ratio
    )

    with get_db_session() as session:
        # Get the holding ID
        rows = session.execute(
            text("SELECT id FROM user_holdings WHERE instrument_token = :token LIMIT 1"),
            {"token": action.instrument_token}
        ).mappings().all()

        if not rows:
            logger.warning("No holding found for {} — skipping split", action.tradingsymbol)
            return {"status": "skipped", "reason": "no_holding"}

        holding_id = rows[0]["id"]

        # Adjust tax lots
        session.execute(
            text("""
                UPDATE holding_tax_lots SET
                    quantity = CEIL(quantity * :ratio),
                    remaining_quantity = CEIL(remaining_quantity * :ratio),
                    buy_price = ROUND(buy_price / :ratio, 2),
                    adjusted_price = ROUND(COALESCE(adjusted_price, buy_price) / :ratio, 2),
                    split_adjusted = TRUE,
                    adjustment_factor = COALESCE(adjustment_factor, 1) * :ratio
                WHERE holding_id = :hid
                  AND remaining_quantity > 0
            """),
            {"ratio": float(ratio), "hid": holding_id}
        )

        # Adjust holdings
        session.execute(
            text("""
                UPDATE user_holdings SET
                    quantity = CEIL(quantity * :ratio),
                    t1_quantity = CEIL(t1_quantity * :ratio),
                    average_price = ROUND(average_price / :ratio, 2)
                WHERE id = :hid
            """),
            {"ratio": float(ratio), "hid": holding_id}
        )

        # Mark action as processed
        session.execute(
            text("UPDATE corporate_actions SET is_processed = TRUE WHERE id = :aid"),
            {"aid": action.id}
        )

    logger.success("Split processed for {}: qty * {}, price / {}", action.tradingsymbol, ratio, ratio)
    return {"status": "processed", "symbol": action.tradingsymbol, "ratio": float(ratio)}


def process_bonus(action: CorporateAction) -> dict[str, Any]:
    """
    Process a bonus issue. Example: 1:1 bonus means
    for every 1 share held, you get 1 free share.

    Adjusts:
        - Creates new tax lots at zero cost basis (bonus shares)
        - Updates user_holdings quantity
    """
    bonus_ratio = Decimal(str(action.ratio_to)) / Decimal(str(action.ratio_from))
    logger.info(
        "Processing BONUS for {} — ratio {}:{} (bonus: {} free per {} held)",
        action.tradingsymbol, action.ratio_from, action.ratio_to,
        action.ratio_to, action.ratio_from
    )

    with get_db_session() as session:
        rows = session.execute(
            text("""
                SELECT id, quantity, t1_quantity
                FROM user_holdings
                WHERE instrument_token = :token LIMIT 1
            """),
            {"token": action.instrument_token}
        ).mappings().all()

        if not rows:
            logger.warning("No holding found for {} — skipping bonus", action.tradingsymbol)
            return {"status": "skipped", "reason": "no_holding"}

        holding_id = rows[0]["id"]
        current_qty = rows[0]["quantity"]

        # Calculate bonus shares
        bonus_shares = int(current_qty * bonus_ratio)

        # Insert new tax lot for bonus shares at zero cost
        session.execute(
            text("""
                INSERT INTO holding_tax_lots (
                    holding_id, buy_date, buy_price, adjusted_price,
                    quantity, remaining_quantity,
                    bonus_adjusted, adjustment_factor
                )
                VALUES (
                    :hid, :ex_date, 0, 0,
                    :qty, :qty,
                    TRUE, :ratio
                )
            """),
            {
                "hid": holding_id,
                "ex_date": action.ex_date,
                "qty": bonus_shares,
                "ratio": float(bonus_ratio),
            }
        )

        # Update total quantity in holdings
        session.execute(
            text("""
                UPDATE user_holdings SET
                    quantity = quantity + :bonus_qty,
                    average_price = CASE
                        WHEN quantity + :bonus_qty > 0
                        THEN ROUND((average_price * quantity) / (quantity + :bonus_qty), 2)
                        ELSE average_price
                    END
                WHERE id = :hid
            """),
            {"bonus_qty": bonus_shares, "hid": holding_id}
        )

        # Mark processed
        session.execute(
            text("UPDATE corporate_actions SET is_processed = TRUE WHERE id = :aid"),
            {"aid": action.id}
        )

    logger.success(
        "Bonus processed for {}: +{} shares (ratio {}:{})",
        action.tradingsymbol, bonus_shares, action.ratio_from, action.ratio_to
    )
    return {"status": "processed", "symbol": action.tradingsymbol, "bonus_shares": bonus_shares}


def process_dividend(action: CorporateAction) -> dict[str, Any]:
    """
    Record a dividend event. Informational only — doesn't change quantities.
    Could be extended to update a dividend income tracker.
    """
    logger.info(
        "Recording DIVIDEND for {} — Rs {} per share, ex-date {}",
        action.tradingsymbol, action.dividend_per_share, action.ex_date
    )

    with get_db_session() as session:
        session.execute(
            text("UPDATE corporate_actions SET is_processed = TRUE WHERE id = :aid"),
            {"aid": action.id}
        )

    return {
        "status": "recorded",
        "symbol": action.tradingsymbol,
        "dividend_per_share": float(action.dividend_per_share or 0),
    }


def process_all_pending(user_id: str = "default") -> list[dict[str, Any]]:
    """
    Process all pending corporate actions for the portfolio.

    Returns:
        List of result dicts for each processed action.
    """
    actions = get_pending_actions(user_id)
    if not actions:
        logger.info("No pending corporate actions.")
        return []

    logger.info("Processing {} pending corporate actions...", len(actions))
    results = []

    for action in actions:
        try:
            if action.action_type == "SPLIT":
                result = process_split(action)
            elif action.action_type == "BONUS":
                result = process_bonus(action)
            elif action.action_type == "DIVIDEND":
                result = process_dividend(action)
            else:
                logger.warning("Unknown action type: {}", action.action_type)
                result = {"status": "skipped", "reason": f"unknown_type_{action.action_type}"}
            results.append(result)
        except Exception as exc:
            logger.exception("Error processing action {}: {}", action.id, exc)
            results.append({"status": "error", "action_id": action.id, "error": str(exc)})

    return results


if __name__ == "__main__":
    """Test: python -m src.analytics.corporate_actions"""
    import json
    results = process_all_pending()
    print(json.dumps(results, indent=2, default=str))
