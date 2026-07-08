"""Duplicate check — prevents placing the same order twice within a cooldown window."""
from __future__ import annotations
from decimal import Decimal
from src.analytics.rebalancer import RebalanceOrder
from src.db.connection import execute_sql


def validate_no_duplicate(order: RebalanceOrder) -> tuple[bool, str]:
    """
    Check if the same (symbol, side, quantity) order was placed
    within the duplicate cooldown window.
    Returns (passed, message).
    """
    # Get cooldown window from config
    rows = execute_sql("SELECT value FROM system_config WHERE key = 'duplicate_window_sec'")
    window_sec = int(rows[0]["value"]) if rows else 300  # 5 minutes default

    # Check order_audit_trail for recent identical orders
    dup_rows = execute_sql("""
        SELECT id, placed_at, status
        FROM order_audit_trail
        WHERE tradingsymbol = :sym
          AND transaction_type = :txn_type
          AND quantity = :qty
          AND placed_at > NOW() - INTERVAL ':window seconds'
          AND status IN ('PLACED', 'COMPLETE', 'OPEN')
        LIMIT 1
    """, {
        "sym": order.tradingsymbol,
        "txn_type": order.side.value,
        "qty": order.quantity,
        "window": window_sec,
    })

    if dup_rows:
        dup = dup_rows[0]
        return False, (
            f"Duplicate order detected. Order #{dup['id']} for "
            f"{order.side.value} {order.quantity}x {order.tradingsymbol} "
            f"was placed at {dup['placed_at']} (status: {dup['status']}). "
            f"Cooldown: {window_sec}s."
        )

    return True, f"No duplicate orders in the last {window_sec}s."
