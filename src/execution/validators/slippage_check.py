"""Slippage check validator — ensures live price hasn't moved too far from estimated."""

from __future__ import annotations

from decimal import Decimal

from loguru import logger

from src.analytics.rebalancer import RebalanceOrder
from src.db.connection import execute_sql
from src.ingestion.kite_auth import get_authenticated_kite
from src.ingestion.market_hours import IST, is_market_open, now_ist

DEFAULT_STALENESS_THRESHOLD_SEC = 60


def _refresh_on_demand_price(order: RebalanceOrder) -> Decimal | None:
    symbol = f"{order.exchange}:{order.tradingsymbol}"
    try:
        kite = get_authenticated_kite()
        if not kite:
            return None
        ltp_dict = kite.ltp([symbol])
        if symbol in ltp_dict:
            raw_price = ltp_dict[symbol].get("last_price")
            if raw_price is not None and raw_price > 0:
                execute_sql(
                    """
                    INSERT INTO live_prices (
                        instrument_token, tradingsymbol, last_price, source, is_stale, last_updated
                    ) VALUES (
                        :token, :symbol_name, :last_price, 'kite', FALSE, NOW()
                    )
                    ON CONFLICT (instrument_token) DO UPDATE SET
                        last_price = EXCLUDED.last_price,
                        source = EXCLUDED.source,
                        is_stale = FALSE,
                        last_updated = EXCLUDED.last_updated
                    """,
                    {
                        "token": order.instrument_token,
                        "symbol_name": order.tradingsymbol,
                        "last_price": raw_price,
                    },
                )
                return Decimal(str(raw_price))
    except Exception as e:
        logger.error(f"On-demand price refresh failed for {symbol}: {e}")
    return None


def validate_slippage(order: RebalanceOrder) -> tuple[bool, str]:
    """
    Compare order's estimated_price against current live price.
    Reject if slippage exceeds the configured threshold.
    Returns (passed, message).
    """
    # Check if instrument is inactive
    active_rows = execute_sql(
        "SELECT is_active FROM instrument_master WHERE instrument_token = :token",
        {"token": order.instrument_token},
    )
    if active_rows and not active_rows[0]["is_active"]:
        return False, f"Instrument {order.tradingsymbol} is inactive or delisted."

    # Get config
    rows = execute_sql(
        "SELECT key, value FROM system_config WHERE key IN ('slippage_bound_pct', 'price_staleness_threshold_sec')"
    )
    config = {r["key"]: r["value"] for r in rows}
    slippage_bound = Decimal(config.get("slippage_bound_pct", "2.0"))
    staleness_thresh = int(
        config.get("price_staleness_threshold_sec", DEFAULT_STALENESS_THRESHOLD_SEC)
    )

    # Get current live price
    price_rows = execute_sql(
        """SELECT last_price, is_stale, last_updated FROM live_prices
           WHERE instrument_token = :token""",
        {"token": order.instrument_token},
    )

    needs_refresh = False
    live_price = None

    if price_rows:
        live_price = Decimal(str(price_rows[0]["last_price"]))
        is_stale = price_rows[0].get("is_stale", False)
        last_updated = price_rows[0].get("last_updated")

        if is_market_open():
            if is_stale or (last_updated is None):
                needs_refresh = True
            else:
                if last_updated.tzinfo is None:
                    last_updated = IST.localize(last_updated)
                else:
                    last_updated = last_updated.astimezone(IST)
                elapsed = (now_ist() - last_updated).total_seconds()
                if elapsed > staleness_thresh:
                    needs_refresh = True
    else:
        if is_market_open():
            needs_refresh = True
        else:
            return (
                True,
                f"No live price outside market hours. Using estimated price ₹{order.estimated_price}.",
            )

    if needs_refresh:
        new_price = _refresh_on_demand_price(order)
        if new_price is None:
            return (
                False,
                f"Price is stale (>{staleness_thresh}s) or missing, and on-demand broker refresh failed for {order.tradingsymbol}.",
            )
        live_price = new_price

    if live_price is None or live_price <= 0:
        return False, f"Live price for {order.tradingsymbol} is zero or unavailable."

    # Calculate slippage %
    slippage_pct = abs(((live_price - order.estimated_price) / order.estimated_price) * 100)

    if slippage_pct > slippage_bound:
        return False, (
            f"Slippage too high: {slippage_pct:.2f}% (limit: {slippage_bound}%). "
            f"Estimated: {order.estimated_price}, Live: {live_price}"
        )

    return True, f"Slippage OK: {slippage_pct:.2f}% (limit: {slippage_bound}%)"
