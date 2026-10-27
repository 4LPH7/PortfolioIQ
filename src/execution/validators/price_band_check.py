"""
Price-band check: orders stay inside the scrip's live circuit limits.

Idea after Vibe-Trading's India equity engine. The limits come only from a fresh broker quote
(`upper_circuit_limit` / `lower_circuit_limit`), which covers both fixed price bands and the
dynamic operating ranges of F&O stocks. There is no fixed-percentage fallback: a live order with a
missing or stale quote is rejected, because an approximate band is not what the exchange enforces.

Orders go out as MARKET orders, so the check rejects:
  - an estimated price outside today's band (the plan was built on a stale price);
  - a SELL while the stock is locked at its lower circuit (no buyers);
  - a BUY while it is locked at its upper circuit (no sellers).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from loguru import logger

from src.analytics.rebalancer import OrderSide, RebalanceOrder
from src.db.connection import execute_sql
from src.ingestion.kite_auth import get_authenticated_kite
from src.ingestion.market_hours import IST, now_ist

DEFAULT_MAX_AGE_SEC = 30


def _max_age_sec() -> int:
    rows = execute_sql("SELECT value FROM system_config WHERE key = 'price_band_max_age_sec'")
    try:
        return int(rows[0]["value"]) if rows else DEFAULT_MAX_AGE_SEC
    except (TypeError, ValueError):
        return DEFAULT_MAX_AGE_SEC


def _quote(order: RebalanceOrder) -> dict | None:
    key = f"{order.exchange}:{order.tradingsymbol}"
    try:
        kite = get_authenticated_kite()
        if not kite:
            return None
        return kite.quote([key]).get(key)
    except Exception as exc:
        logger.warning("Quote unavailable for {}: {}", key, exc)
        return None


def _age_sec(ts: datetime | None) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = IST.localize(ts)
    return (now_ist() - ts.astimezone(IST)).total_seconds()


def _unverified(live: bool, reason: str) -> tuple[bool, str]:
    if live:
        return False, f"Circuit limits can't be confirmed: {reason}. Order not sent."
    return True, f"UNVERIFIED: circuit limits not checked ({reason})."


def validate_price_band(order: RebalanceOrder, live: bool) -> tuple[bool, str]:
    """Returns (passed, message). Caveats in dry run start with "UNVERIFIED:"."""
    q = _quote(order)
    if q is None:
        return _unverified(live, "no broker quote")

    upper, lower = q.get("upper_circuit_limit"), q.get("lower_circuit_limit")
    if not upper or not lower:
        return _unverified(live, "the quote carries no circuit limits")

    max_age = _max_age_sec()
    age = _age_sec(q.get("timestamp"))
    if age is None or age > max_age:
        shown = "unknown" if age is None else f"{age:.0f}s"
        return _unverified(live, f"quote is {shown} old (limit {max_age}s)")

    upper, lower = Decimal(str(upper)), Decimal(str(lower))
    band = f"₹{lower}–₹{upper}"
    if not lower <= order.estimated_price <= upper:
        return False, (
            f"Estimated ₹{order.estimated_price} for {order.tradingsymbol} is outside today's "
            f"band {band}. Rebuild the plan with a fresh price."
        )

    ltp = Decimal(str(q.get("last_price") or 0))
    if order.side == OrderSide.SELL and ltp <= lower:
        return False, f"{order.tradingsymbol} is locked at its lower circuit ₹{lower}: no buyers."
    if order.side == OrderSide.BUY and ltp >= upper:
        return False, f"{order.tradingsymbol} is locked at its upper circuit ₹{upper}: no sellers."

    return True, f"Inside today's band {band} (quote {age:.0f}s old)."
