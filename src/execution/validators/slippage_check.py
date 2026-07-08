"""Slippage check validator — ensures live price hasn't moved too far from estimated."""
from __future__ import annotations
from decimal import Decimal
from src.analytics.rebalancer import RebalanceOrder
from src.db.connection import execute_sql


def validate_slippage(order: RebalanceOrder) -> tuple[bool, str]:
    """
    Compare order's estimated_price against current live price.
    Reject if slippage exceeds the configured threshold.
    Returns (passed, message).
    """
    # Get slippage bound from config
    rows = execute_sql("SELECT value FROM system_config WHERE key = 'slippage_bound_pct'")
    slippage_bound = Decimal(rows[0]["value"]) if rows else Decimal("2.0")

    # Get current live price
    price_rows = execute_sql(
        """SELECT last_price, is_stale FROM live_prices
           WHERE instrument_token = :token""",
        {"token": order.instrument_token}
    )

    if not price_rows:
        # No live price — use broker price, pass with warning
        return True, "No live price available. Using estimated price."

    live_price = Decimal(str(price_rows[0]["last_price"]))
    is_stale = price_rows[0].get("is_stale", False)

    if is_stale:
        return True, f"Live price is stale. Using estimated price {order.estimated_price}."

    if live_price <= 0:
        return False, "Live price is zero or negative."

    # Calculate slippage %
    slippage_pct = abs(
        ((live_price - order.estimated_price) / order.estimated_price) * 100
    )

    if slippage_pct > slippage_bound:
        return False, (
            f"Slippage too high: {slippage_pct:.2f}% (limit: {slippage_bound}%). "
            f"Estimated: {order.estimated_price}, Live: {live_price}"
        )

    return True, f"Slippage OK: {slippage_pct:.2f}% (limit: {slippage_bound}%)"
