"""
Sellable-quantity check: a SELL never exceeds what the broker says can be sold today.

Idea after Vibe-Trading's India equity engine (T+1 settlement, trade-to-trade scrips); the
quantities come from the broker, never from imported or estimated holdings.

Sellable today =
    settled quantity − already sold today − pledged
    + T1 quantity (only with btst_enabled, and never for trade-to-trade series)
    − quantity in the broker's open SELL orders
capped by today's e-DIS authorisation when the account uses TPIN rather than DDPI.

Kite's order book already contains orders PortfolioIQ placed, so the audit trail is not added on
top of it (that would count them twice). Where a field's meaning is uncertain the conservative
reading is used: under-counting blocks a sale that might have been allowed, over-counting would
send one the exchange rejects.

Live mode rejects whenever a figure can't be confirmed. Dry run passes with an "UNVERIFIED" note.
"""

from __future__ import annotations

import re
from datetime import datetime

from loguru import logger

from src.analytics.rebalancer import OrderSide, RebalanceOrder
from src.db.connection import execute_sql
from src.ingestion.kite_auth import get_authenticated_kite
from src.ingestion.market_hours import IST, today_ist

TRADE_TO_TRADE = re.compile(r"-(BE|BZ|BT)$")  # T2T series settle trade by trade: no BTST
TERMINAL_ORDER_STATUSES = {"COMPLETE", "CANCELLED", "REJECTED"}
UNCONFIRMED_SOURCES = {"CSV_IMPORT", "MANUAL", "legacy"}


def _config() -> dict[str, str]:
    rows = execute_sql(
        "SELECT key, value FROM system_config WHERE key IN ('btst_enabled', 'edis_mode')"
    )
    return {r["key"]: r["value"] for r in rows}


def _holding(order: RebalanceOrder) -> dict | None:
    rows = execute_sql(
        """
        SELECT h.quantity, h.t1_quantity, h.used_quantity, h.authorised_quantity,
               h.collateral_quantity, h.last_synced_at,
               COALESCE(h.data_source, 'KITE') AS data_source, im.instrument_type
        FROM user_holdings h
        LEFT JOIN instrument_master im ON im.instrument_token = h.instrument_token
        WHERE h.user_id = 'default' AND h.instrument_token = :token AND h.product = 'CNC'
        """,
        {"token": order.instrument_token},
    )
    return rows[0] if rows else None


def _open_sell_quantity(order: RebalanceOrder) -> int | None:
    """Pending SELL quantity in the broker's order book; None when it can't be read."""
    try:
        kite = get_authenticated_kite()
        if not kite:
            return None
        pending = 0
        for o in kite.orders():
            if (
                o.get("tradingsymbol") == order.tradingsymbol
                and o.get("exchange") == order.exchange
                and o.get("transaction_type") == "SELL"
                and o.get("status") not in TERMINAL_ORDER_STATUSES
            ):
                pending += int(o.get("pending_quantity") or 0)
        return pending
    except Exception as exc:
        logger.warning("Order book unavailable for {}: {}", order.tradingsymbol, exc)
        return None


def _synced_today(last_synced_at: datetime | None) -> bool:
    if last_synced_at is None:
        return False
    if last_synced_at.tzinfo is None:
        last_synced_at = last_synced_at.replace(tzinfo=IST)
    return last_synced_at.astimezone(IST).date() == today_ist()


def validate_sellable(order: RebalanceOrder, live: bool) -> tuple[bool, str]:
    """Returns (passed, message). Caveats in dry run start with "UNVERIFIED:"."""
    if order.side != OrderSide.SELL:
        return True, "Not a sell order."

    h = _holding(order)
    if h is None:
        return False, f"No {order.tradingsymbol} delivery holding to sell."

    caveats: list[str] = []
    if h["data_source"] in UNCONFIRMED_SOURCES:
        if live:
            return False, (
                f"{order.tradingsymbol} comes from an imported file, not the broker. "
                "Sync holdings from Kite before selling."
            )
        caveats.append("holding is imported, not broker-confirmed")
    if not _synced_today(h["last_synced_at"]):
        if live:
            return False, "Holdings weren't synced from Kite today. Sync holdings first."
        caveats.append("holdings not synced today")

    cfg = _config()
    btst = cfg.get("btst_enabled", "false").lower() == "true"
    trade_to_trade = bool(TRADE_TO_TRADE.search(order.tradingsymbol)) or (
        h.get("instrument_type") == "BE"
    )

    held = int(h["quantity"] or 0)
    sold_today = int(h["used_quantity"] or 0)
    pledged = int(h["collateral_quantity"] or 0)
    t1 = int(h["t1_quantity"] or 0)
    t1_counted = t1 if (btst and not trade_to_trade) else 0
    sellable = held - sold_today - pledged + t1_counted

    pending = _open_sell_quantity(order)
    if pending is None:
        if live:
            return False, "Broker order book unavailable: can't net open sell orders."
        caveats.append("open sell orders not checked (no broker session)")
        pending = 0
    sellable -= pending

    if cfg.get("edis_mode", "ddpi").lower() == "tpin":
        authorised_left = max(int(h["authorised_quantity"] or 0) - sold_today, 0)
        sellable = min(sellable, authorised_left)

    sellable = max(sellable, 0)
    if order.quantity > sellable:
        parts = [f"{held} held"]
        if sold_today:
            parts.append(f"{sold_today} sold today")
        if pledged:
            parts.append(f"{pledged} pledged")
        if pending:
            parts.append(f"{pending} in open sell orders")
        if t1 and not t1_counted:
            why = "trade-to-trade scrip" if trade_to_trade else "BTST off"
            parts.append(f"{t1} bought yesterday settle tomorrow ({why})")
        return False, (
            f"SELL {order.quantity} {order.tradingsymbol} exceeds the {sellable} sellable today "
            f"({', '.join(parts)})."
        )

    msg = f"{sellable} {order.tradingsymbol} sellable today; selling {order.quantity}."
    if caveats:
        msg = f"UNVERIFIED: {msg} ({'; '.join(caveats)})"
    return True, msg
