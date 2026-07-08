"""
PortfolioIQ — Rebalancer
Generates corrective orders to close allocation drift.

Pipeline:
    1. Detect drift (via DriftDetector)
    2. Prioritise signals (HIGH severity first)
    3. Compute sell orders for overweight holdings
    4. Compute buy orders for underweight holdings (constrained by cash)
    5. Validate against tax guard (avoid STCG if near LTCG)
    6. Round to lot sizes
    7. Output RebalanceOrder list → fed into Gatekeeper (Phase 4)

This module NEVER places orders directly. It only produces an order
manifest that the Gatekeeper validates before execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
from enum import Enum
from typing import Any

from loguru import logger

from src.analytics.valuator import compute_portfolio_valuation, PortfolioValuation
from src.analytics.drift_detector import detect_drift, DriftSignal, DriftType, DriftDirection
from src.analytics.tax_guard import check_sell_tax_impact, TaxWarning
from src.db.connection import execute_sql


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderReason(str, Enum):
    SECTOR_DRIFT = "SECTOR_DRIFT"
    CONCENTRATION_BREACH = "CONCENTRATION_BREACH"
    HOLDING_DRIFT = "HOLDING_DRIFT"
    CASH_REBALANCE = "CASH_REBALANCE"
    MANUAL = "MANUAL"


@dataclass
class RebalanceOrder:
    """A single proposed rebalance order (not yet validated or placed)."""
    tradingsymbol: str
    exchange: str
    instrument_token: int
    side: OrderSide
    quantity: int
    estimated_price: Decimal
    estimated_value: Decimal = Decimal("0")

    # Rebalance context
    reason: OrderReason = OrderReason.SECTOR_DRIFT
    drift_signal: DriftSignal | None = None
    tax_warnings: list[TaxWarning] = field(default_factory=list)

    # Flags
    has_tax_warning: bool = False
    is_approved: bool = False  # Set by gatekeeper

    def __post_init__(self):
        self.estimated_value = (self.estimated_price * self.quantity).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.has_tax_warning = len(self.tax_warnings) > 0


@dataclass
class RebalancePlan:
    """Complete rebalance plan with sell and buy orders."""
    orders: list[RebalanceOrder] = field(default_factory=list)
    sell_orders: list[RebalanceOrder] = field(default_factory=list)
    buy_orders: list[RebalanceOrder] = field(default_factory=list)

    total_sell_value: Decimal = Decimal("0")
    total_buy_value: Decimal = Decimal("0")
    net_cash_impact: Decimal = Decimal("0")  # positive = cash freed

    drift_signals_addressed: int = 0
    tax_warnings_count: int = 0
    max_orders_limit: int = 10

    def add_order(self, order: RebalanceOrder):
        self.orders.append(order)
        if order.side == OrderSide.SELL:
            self.sell_orders.append(order)
            self.total_sell_value += order.estimated_value
        else:
            self.buy_orders.append(order)
            self.total_buy_value += order.estimated_value
        self.net_cash_impact = self.total_sell_value - self.total_buy_value
        if order.has_tax_warning:
            self.tax_warnings_count += 1


def _get_max_rebalance_orders() -> int:
    """Get the max orders limit from config."""
    rows = execute_sql(
        "SELECT value FROM system_config WHERE key = 'max_rebalance_orders'"
    )
    return int(rows[0]["value"]) if rows else 10


def _get_holding_details(tradingsymbol: str, user_id: str = "default") -> dict | None:
    """Get holding details needed for order generation."""
    rows = execute_sql("""
        SELECT
            h.instrument_token,
            h.exchange,
            h.quantity,
            h.t1_quantity,
            h.average_price,
            COALESCE(lp.last_price, h.last_price) AS current_price,
            im.lot_size,
            im.tick_size
        FROM user_holdings h
        LEFT JOIN live_prices lp ON h.instrument_token = lp.instrument_token
        LEFT JOIN instrument_master im ON h.instrument_token = im.instrument_token
        WHERE h.tradingsymbol = :sym AND h.user_id = :uid
        LIMIT 1
    """, {"sym": tradingsymbol, "uid": user_id})
    return rows[0] if rows else None


def _round_to_lot_size(quantity: int, lot_size: int = 1) -> int:
    """Round quantity down to the nearest lot size."""
    if lot_size <= 1:
        return max(quantity, 0)
    return (quantity // lot_size) * lot_size


def generate_rebalance_plan(
    user_id: str = "default",
    dry_run: bool = True,
    skip_tax_warnings: bool = False,
) -> RebalancePlan:
    """
    Generate a complete rebalance plan based on current drift signals.

    Args:
        user_id: Portfolio owner.
        dry_run: If True, only simulate (default). Phase 4 gatekeeper will use False.
        skip_tax_warnings: If True, ignore tax warnings and generate orders anyway.

    Returns:
        RebalancePlan with proposed orders.
    """
    logger.info("Generating rebalance plan (dry_run={})...", dry_run)

    valuation = compute_portfolio_valuation(user_id)
    signals = detect_drift(user_id, valuation)
    max_orders = _get_max_rebalance_orders()

    plan = RebalancePlan(max_orders_limit=max_orders)

    # Only process actionable signals
    actionable = [s for s in signals if s.is_actionable]
    if not actionable:
        logger.info("No actionable drift signals. Portfolio is balanced.")
        return plan

    logger.info("Processing {} actionable drift signals...", len(actionable))

    # ─── SELL ORDERS (overweight → sell to target) ────────────
    for signal in actionable:
        if len(plan.orders) >= max_orders:
            logger.warning("Reached max orders limit ({}). Stopping.", max_orders)
            break

        if signal.direction != DriftDirection.OVERWEIGHT:
            continue

        if signal.drift_type == DriftType.CASH:
            continue  # cash drift doesn't generate orders directly

        # For sector drift, we need to pick which holding(s) to sell
        if signal.drift_type == DriftType.SECTOR:
            # Find overweight holdings in this sector
            sector_holdings = [
                h for h in valuation.holdings
                if (h.sector or "Uncategorised") == signal.name
            ]
            # Sort by weight (heaviest first — trim the biggest position)
            sector_holdings.sort(key=lambda h: h.weight_pct, reverse=True)

            sell_value_needed = abs(signal.rebalance_amount)
            for sh in sector_holdings:
                if sell_value_needed <= 0:
                    break
                details = _get_holding_details(sh.tradingsymbol, user_id)
                if details is None:
                    continue

                current_price = Decimal(str(details["current_price"]))
                if current_price <= 0:
                    continue

                lot_size = int(details.get("lot_size") or 1)
                max_sellable = details["quantity"]  # don't sell T+1

                sell_qty = int(
                    (sell_value_needed / current_price).to_integral_value(rounding=ROUND_DOWN)
                )
                sell_qty = min(sell_qty, max_sellable)
                sell_qty = _round_to_lot_size(sell_qty, lot_size)

                if sell_qty <= 0:
                    continue

                # Tax check
                tax_warnings = []
                if not skip_tax_warnings:
                    tax_warnings = check_sell_tax_impact(sh.tradingsymbol, sell_qty, user_id)

                order = RebalanceOrder(
                    tradingsymbol=sh.tradingsymbol,
                    exchange=sh.exchange,
                    instrument_token=details["instrument_token"],
                    side=OrderSide.SELL,
                    quantity=sell_qty,
                    estimated_price=current_price,
                    reason=OrderReason.SECTOR_DRIFT,
                    drift_signal=signal,
                    tax_warnings=tax_warnings,
                )
                plan.add_order(order)
                plan.drift_signals_addressed += 1
                sell_value_needed -= order.estimated_value

        elif signal.drift_type == DriftType.HOLDING:
            # Direct holding drift — sell specific holding
            details = _get_holding_details(signal.name, user_id)
            if details is None:
                continue

            current_price = Decimal(str(details["current_price"]))
            if current_price <= 0:
                continue

            lot_size = int(details.get("lot_size") or 1)
            sell_value_needed = abs(signal.rebalance_amount)
            sell_qty = int(
                (sell_value_needed / current_price).to_integral_value(rounding=ROUND_DOWN)
            )
            sell_qty = min(sell_qty, details["quantity"])
            sell_qty = _round_to_lot_size(sell_qty, lot_size)

            if sell_qty <= 0:
                continue

            tax_warnings = []
            if not skip_tax_warnings:
                tax_warnings = check_sell_tax_impact(signal.name, sell_qty, user_id)

            reason = (
                OrderReason.CONCENTRATION_BREACH
                if signal.target_weight_pct == Decimal(str(
                    execute_sql("SELECT value FROM system_config WHERE key = 'concentration_limit_pct'")[0]["value"]
                ))
                else OrderReason.HOLDING_DRIFT
            )

            order = RebalanceOrder(
                tradingsymbol=signal.name,
                exchange=details.get("exchange", "NSE"),
                instrument_token=details["instrument_token"],
                side=OrderSide.SELL,
                quantity=sell_qty,
                estimated_price=current_price,
                reason=reason,
                drift_signal=signal,
                tax_warnings=tax_warnings,
            )
            plan.add_order(order)
            plan.drift_signals_addressed += 1

    # ─── BUY ORDERS (underweight → buy up to target, limited by cash) ─
    available_cash = valuation.available_cash + plan.net_cash_impact

    for signal in actionable:
        if len(plan.orders) >= max_orders:
            break

        if signal.direction != DriftDirection.UNDERWEIGHT:
            continue

        if signal.drift_type == DriftType.CASH:
            continue

        # For sector drift, find an underweight holding to buy
        if signal.drift_type in (DriftType.SECTOR, DriftType.HOLDING):
            target_symbol = None

            if signal.drift_type == DriftType.HOLDING:
                target_symbol = signal.name
            else:
                # Pick the most underweight holding in the sector
                sector_holdings = [
                    h for h in valuation.holdings
                    if (h.sector or "Uncategorised") == signal.name
                ]
                if sector_holdings:
                    sector_holdings.sort(key=lambda h: h.weight_pct)
                    target_symbol = sector_holdings[0].tradingsymbol
                else:
                    # No existing holdings in this sector — can't auto-buy new stock
                    logger.debug(
                        "Sector '{}' underweight but no existing holdings to buy more of.",
                        signal.name
                    )
                    continue

            if target_symbol is None:
                continue

            details = _get_holding_details(target_symbol, user_id)
            if details is None:
                continue

            current_price = Decimal(str(details["current_price"]))
            if current_price <= 0:
                continue

            lot_size = int(details.get("lot_size") or 1)
            buy_value_needed = min(abs(signal.rebalance_amount), available_cash)

            if buy_value_needed < current_price:
                logger.debug("Insufficient cash to buy even 1 share of {}", target_symbol)
                continue

            buy_qty = int(
                (buy_value_needed / current_price).to_integral_value(rounding=ROUND_DOWN)
            )
            buy_qty = _round_to_lot_size(buy_qty, lot_size)

            if buy_qty <= 0:
                continue

            order = RebalanceOrder(
                tradingsymbol=target_symbol,
                exchange=details.get("exchange", "NSE"),
                instrument_token=details["instrument_token"],
                side=OrderSide.BUY,
                quantity=buy_qty,
                estimated_price=current_price,
                reason=OrderReason.SECTOR_DRIFT if signal.drift_type == DriftType.SECTOR else OrderReason.HOLDING_DRIFT,
                drift_signal=signal,
            )
            plan.add_order(order)
            plan.drift_signals_addressed += 1
            available_cash -= order.estimated_value

    logger.success(
        "Rebalance plan: {} orders ({} sells, {} buys), "
        "net cash impact: {}, {} drift signals addressed, {} tax warnings",
        len(plan.orders), len(plan.sell_orders), len(plan.buy_orders),
        plan.net_cash_impact, plan.drift_signals_addressed, plan.tax_warnings_count
    )
    return plan


def get_rebalance_summary(user_id: str = "default") -> dict[str, Any]:
    """JSON-friendly rebalance plan summary for the dashboard."""
    plan = generate_rebalance_plan(user_id)
    return {
        "total_orders": len(plan.orders),
        "sell_orders": len(plan.sell_orders),
        "buy_orders": len(plan.buy_orders),
        "total_sell_value": float(plan.total_sell_value),
        "total_buy_value": float(plan.total_buy_value),
        "net_cash_impact": float(plan.net_cash_impact),
        "drift_signals_addressed": plan.drift_signals_addressed,
        "tax_warnings": plan.tax_warnings_count,
        "orders": [
            {
                "symbol": o.tradingsymbol,
                "exchange": o.exchange,
                "side": o.side.value,
                "quantity": o.quantity,
                "price": float(o.estimated_price),
                "value": float(o.estimated_value),
                "reason": o.reason.value,
                "has_tax_warning": o.has_tax_warning,
                "tax_warnings": [
                    {"type": w.warning_type, "message": w.message, "severity": w.severity}
                    for w in o.tax_warnings
                ],
            }
            for o in plan.orders
        ],
    }


if __name__ == "__main__":
    """Test: python -m src.analytics.rebalancer"""
    import json
    print(json.dumps(get_rebalance_summary(), indent=2))
