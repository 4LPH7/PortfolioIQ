"""Concentration check — ensures a buy order won't breach the single-holding limit."""
from __future__ import annotations
from decimal import Decimal
from src.analytics.rebalancer import RebalanceOrder
from src.db.connection import execute_sql


def validate_concentration(order: RebalanceOrder, user_id: str = "default") -> tuple[bool, str]:
    """
    Check if buying more of this holding would breach the concentration limit.
    Returns (passed, message).
    """
    # Get concentration limit from config
    rows = execute_sql("SELECT value FROM system_config WHERE key = 'concentration_limit_pct'")
    limit_pct = Decimal(rows[0]["value"]) if rows else Decimal("15.0")

    # Get current AUM
    aum_rows = execute_sql("""
        SELECT SUM(
            (h.quantity + h.t1_quantity) * COALESCE(lp.last_price, h.last_price, 0)
        ) AS total_aum
        FROM user_holdings h
        LEFT JOIN live_prices lp ON h.instrument_token = lp.instrument_token
        WHERE h.user_id = :uid AND (h.quantity + h.t1_quantity) > 0
    """, {"uid": user_id})

    total_aum = Decimal(str(aum_rows[0]["total_aum"] or 0)) if aum_rows else Decimal("0")
    if total_aum <= 0:
        return True, "AUM is zero. Concentration check skipped."

    # Current holding value
    holding_rows = execute_sql("""
        SELECT (h.quantity + h.t1_quantity) * COALESCE(lp.last_price, h.last_price, 0) AS holding_value
        FROM user_holdings h
        LEFT JOIN live_prices lp ON h.instrument_token = lp.instrument_token
        WHERE h.instrument_token = :token AND h.user_id = :uid
    """, {"token": order.instrument_token, "uid": user_id})

    current_value = Decimal(str(holding_rows[0]["holding_value"] or 0)) if holding_rows else Decimal("0")

    # Post-order value
    new_value = current_value + order.estimated_value
    new_aum = total_aum + order.estimated_value
    new_weight = (new_value / new_aum * 100) if new_aum > 0 else Decimal("0")

    if new_weight > limit_pct:
        return False, (
            f"Concentration breach: {order.tradingsymbol} would be {new_weight:.1f}% "
            f"of portfolio (limit: {limit_pct}%). "
            f"Current: {(current_value/total_aum*100):.1f}%"
        )

    return True, f"Concentration OK: {new_weight:.1f}% (limit: {limit_pct}%)"
