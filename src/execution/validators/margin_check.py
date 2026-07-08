"""Margin check validator — ensures sufficient cash for buy orders."""
from __future__ import annotations
from decimal import Decimal
from src.analytics.rebalancer import RebalanceOrder
from src.db.connection import execute_sql


def validate_margin(order: RebalanceOrder, user_id: str = "default") -> tuple[bool, str]:
    """
    Check if sufficient margin is available for a BUY order.
    Returns (passed, message).
    """
    rows = execute_sql(
        "SELECT available_cash FROM user_margins WHERE user_id = :uid AND segment = 'equity'",
        {"uid": user_id}
    )
    if not rows:
        return False, "No margin data available. Run Kite sync first."

    available = Decimal(str(rows[0]["available_cash"]))
    required = order.estimated_value

    if available < required:
        return False, (
            f"Insufficient margin. Required: {required}, Available: {available}. "
            f"Shortfall: {required - available}"
        )

    return True, f"Margin OK. Available: {available}, Required: {required}"
