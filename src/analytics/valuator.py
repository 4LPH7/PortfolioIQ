"""
PortfolioIQ — Portfolio Valuator
Real-time P&L, AUM, and per-holding valuation engine.

Combines static broker data (average_price from Kite) with
live market prices (from Yahoo via live_prices table) to compute:
    - Per-holding: current_value, unrealised_pnl, weight_pct, day_change
    - Portfolio-level: total_aum, total_invested, total_pnl, pnl_pct
    - Sector-level: sector_value, sector_weight
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from loguru import logger
from sqlalchemy import text

from src.db.connection import get_db_session, execute_sql


# ============================================================
# Data Classes
# ============================================================

@dataclass
class HoldingValuation:
    """Valuation for a single holding."""
    instrument_token: int
    tradingsymbol: str
    exchange: str
    isin: str | None
    sector: str | None
    industry: str | None

    # Quantities
    quantity: int
    t1_quantity: int

    # Prices
    average_price: Decimal
    current_price: Decimal
    close_price: Decimal | None  # previous close
    price_source: str  # 'live' | 'broker' | 'stale'

    # Computed
    invested_value: Decimal = Decimal("0")
    current_value: Decimal = Decimal("0")
    unrealised_pnl: Decimal = Decimal("0")
    unrealised_pnl_pct: Decimal = Decimal("0")
    day_change: Decimal = Decimal("0")
    day_change_pct: Decimal = Decimal("0")
    weight_pct: Decimal = Decimal("0")  # % of total AUM
    product: str = "CNC"

    def __post_init__(self):
        total_qty = self.quantity + self.t1_quantity
        self.invested_value = self.average_price * total_qty
        self.current_value = self.current_price * total_qty
        self.unrealised_pnl = self.current_value - self.invested_value
        if self.invested_value > 0:
            self.unrealised_pnl_pct = (
                (self.unrealised_pnl / self.invested_value) * 100
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if self.close_price and self.close_price > 0:
            self.day_change = (self.current_price - self.close_price) * total_qty
            self.day_change_pct = (
                ((self.current_price - self.close_price) / self.close_price) * 100
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class PortfolioValuation:
    """Complete portfolio valuation."""
    user_id: str = "default"
    holdings: list[HoldingValuation] = field(default_factory=list)

    # Portfolio totals
    total_aum: Decimal = Decimal("0")
    total_invested: Decimal = Decimal("0")
    total_pnl: Decimal = Decimal("0")
    total_pnl_pct: Decimal = Decimal("0")
    total_day_change: Decimal = Decimal("0")
    available_cash: Decimal = Decimal("0")
    net_worth: Decimal = Decimal("0")  # AUM + cash

    # Sector breakdown
    sector_weights: dict[str, Decimal] = field(default_factory=dict)

    # Data quality
    stale_count: int = 0
    live_count: int = 0


# ============================================================
# Valuation Engine
# ============================================================

def compute_portfolio_valuation(user_id: str = "default") -> PortfolioValuation:
    """
    Compute full portfolio valuation by joining holdings with live prices.

    Priority for current price:
        1. live_prices.last_price (if not stale)
        2. user_holdings.last_price (from Kite SOD sync)
        3. user_holdings.close_price (previous close fallback)

    Returns:
        PortfolioValuation with all holdings valued and portfolio totals computed.
    """
    logger.info("Computing portfolio valuation for user='{}'...", user_id)

    # Pull holdings joined with live prices and instrument metadata
    rows = execute_sql("""
        SELECT
            h.instrument_token,
            h.tradingsymbol,
            h.exchange,
            h.isin,
            h.quantity,
            h.t1_quantity,
            h.average_price,
            h.last_price      AS broker_last_price,
            h.close_price     AS broker_close_price,
            h.pnl             AS broker_pnl,
            h.product,
            im.sector,
            im.industry,
            im.yf_ticker,
            lp.last_price     AS live_price,
            lp.close_price    AS live_close,
            lp.is_stale,
            lp.last_updated   AS price_updated_at
        FROM user_holdings h
        LEFT JOIN instrument_master im
            ON h.instrument_token = im.instrument_token
        LEFT JOIN live_prices lp
            ON h.instrument_token = lp.instrument_token
        WHERE h.user_id = :user_id
          AND (h.quantity + h.t1_quantity) > 0
        ORDER BY h.tradingsymbol
    """, {"user_id": user_id})

    pv = PortfolioValuation(user_id=user_id)

    for row in rows:
        # Determine best available price
        live_price = row.get("live_price")
        broker_price = row.get("broker_last_price")
        broker_close = row.get("broker_close_price")
        is_stale = row.get("is_stale", True)

        if live_price and not is_stale:
            current_price = Decimal(str(live_price))
            close_price = Decimal(str(row["live_close"])) if row.get("live_close") else None
            price_source = "live"
            pv.live_count += 1
        elif broker_price and broker_price > 0:
            current_price = Decimal(str(broker_price))
            close_price = Decimal(str(broker_close)) if broker_close else None
            price_source = "broker"
            pv.stale_count += 1
        else:
            current_price = Decimal(str(broker_close or 0))
            close_price = None
            price_source = "stale"
            pv.stale_count += 1

        hv = HoldingValuation(
            instrument_token=row["instrument_token"],
            tradingsymbol=row["tradingsymbol"],
            exchange=row["exchange"],
            isin=row.get("isin"),
            sector=row.get("sector"),
            industry=row.get("industry"),
            quantity=row["quantity"],
            t1_quantity=row.get("t1_quantity", 0),
            average_price=Decimal(str(row["average_price"])),
            current_price=current_price,
            close_price=close_price,
            price_source=price_source,
            product=row.get("product", "CNC"),
        )
        pv.holdings.append(hv)

    # Portfolio totals
    pv.total_invested = sum(h.invested_value for h in pv.holdings)
    pv.total_aum = sum(h.current_value for h in pv.holdings)
    pv.total_pnl = sum(h.unrealised_pnl for h in pv.holdings)
    pv.total_day_change = sum(h.day_change for h in pv.holdings)

    if pv.total_invested > 0:
        pv.total_pnl_pct = (
            (pv.total_pnl / pv.total_invested) * 100
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Fetch available cash from margins
    margin_rows = execute_sql(
        "SELECT available_cash FROM user_margins WHERE user_id = :uid AND segment = 'equity'",
        {"uid": user_id}
    )
    if margin_rows:
        pv.available_cash = Decimal(str(margin_rows[0]["available_cash"]))
    pv.net_worth = pv.total_aum + pv.available_cash

    # Compute per-holding weight as % of AUM
    if pv.total_aum > 0:
        for h in pv.holdings:
            h.weight_pct = (
                (h.current_value / pv.total_aum) * 100
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Sector aggregation
    for h in pv.holdings:
        sector = h.sector or "Uncategorised"
        pv.sector_weights[sector] = pv.sector_weights.get(sector, Decimal("0")) + h.current_value

    # Convert sector values to percentages
    if pv.total_aum > 0:
        pv.sector_weights = {
            s: ((v / pv.total_aum) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            for s, v in pv.sector_weights.items()
        }

    logger.success(
        "Valuation complete: AUM={}, PnL={} ({}%), {} holdings ({} live, {} stale/broker)",
        pv.total_aum, pv.total_pnl, pv.total_pnl_pct,
        len(pv.holdings), pv.live_count, pv.stale_count
    )
    return pv


def get_valuation_summary(user_id: str = "default") -> dict[str, Any]:
    """
    Return a JSON-friendly summary of the portfolio valuation.
    Used by the Streamlit dashboard and export modules.
    """
    pv = compute_portfolio_valuation(user_id)
    return {
        "user_id": pv.user_id,
        "total_aum": float(pv.total_aum),
        "total_invested": float(pv.total_invested),
        "total_pnl": float(pv.total_pnl),
        "total_pnl_pct": float(pv.total_pnl_pct),
        "total_day_change": float(pv.total_day_change),
        "available_cash": float(pv.available_cash),
        "net_worth": float(pv.net_worth),
        "holdings_count": len(pv.holdings),
        "live_prices": pv.live_count,
        "stale_prices": pv.stale_count,
        "sector_weights": {k: float(v) for k, v in pv.sector_weights.items()},
        "holdings": [
            {
                "symbol": h.tradingsymbol,
                "exchange": h.exchange,
                "sector": h.sector,
                "qty": h.quantity + h.t1_quantity,
                "avg_price": float(h.average_price),
                "current_price": float(h.current_price),
                "invested": float(h.invested_value),
                "current_value": float(h.current_value),
                "pnl": float(h.unrealised_pnl),
                "pnl_pct": float(h.unrealised_pnl_pct),
                "day_change": float(h.day_change),
                "weight_pct": float(h.weight_pct),
                "price_source": h.price_source,
            }
            for h in pv.holdings
        ],
    }


if __name__ == "__main__":
    """Test valuation: python -m src.analytics.valuator"""
    import json
    summary = get_valuation_summary()
    print(json.dumps(summary, indent=2))
