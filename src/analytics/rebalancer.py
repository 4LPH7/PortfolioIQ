"""
PortfolioIQ — Rebalancer
Generates corrective orders to close allocation drift with execution constraints
and realistic Indian transaction cost modeling.

Pipeline:
    1. Detect drift (via DriftDetector)
    2. Prioritise signals (highest absolute drift magnitude first)
    3. Compute sell orders for overweight holdings:
       - 20-day ADV volume guard (<= 1% ADV)
       - Minimum trade value filter (>= Rs 2,000, 100% liquidations exempt)
       - Daily turnover cap check (<= 15% AUM)
    4. Compute buy orders for underweight holdings:
       - Constrained by cash buffer (cash_balance + sells - max(2% AUM, Rs 5,000))
       - 20-day ADV volume guard
       - Minimum trade value filter
       - Daily turnover cap check
    5. Validate against tax guard (avoid STCG if near LTCG)
    6. Attach Indian equity delivery cost breakdown (STT, DP, GST, Stamp Duty, Fees)
    7. Output RebalanceOrder list / RebalancePlan manifest

This module NEVER places orders directly. It produces a validated order manifest
for the Gatekeeper to review and approve before routing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from loguru import logger

from src.analytics.cost_calculator import IndianTradeCost, compute_indian_delivery_charges
from src.analytics.drift_detector import DriftDirection, DriftSignal, DriftType, detect_drift
from src.analytics.tax_guard import TaxWarning, check_sell_tax_impact
from src.analytics.valuator import compute_portfolio_valuation
from src.config.settings import get_settings
from src.db.connection import execute_sql
from src.models.dtos import BacktestRunDTO, HoldingSignalDTO


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderReason(StrEnum):
    SECTOR_DRIFT = "SECTOR_DRIFT"
    CONCENTRATION_BREACH = "CONCENTRATION_BREACH"
    HOLDING_DRIFT = "HOLDING_DRIFT"
    CASH_REBALANCE = "CASH_REBALANCE"
    MANUAL = "MANUAL"
    TACTICAL_SIGNAL = "TACTICAL_SIGNAL"


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

    # Indian statutory charges & execution constraints
    cost: IndianTradeCost | None = None
    charges_total: Decimal = Decimal("0.00")
    is_full_liquidation: bool = False
    is_adv_capped: bool = False
    original_quantity: int | None = None
    adv_20: int | None = None

    # Evidence status & badge (Phase 5 D-06)
    evidence_status: str = "PENDING"
    evidence_badge: str = ""

    def __post_init__(self):
        self.estimated_value = (self.estimated_price * self.quantity).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.has_tax_warning = len(self.tax_warnings) > 0
        if self.cost is None and self.estimated_value > 0:
            self.cost = compute_indian_delivery_charges(
                side=self.side.value,
                trade_value=self.estimated_value,
                is_first_sell_of_scrip=True,
            )
        if self.cost is not None:
            self.charges_total = self.cost.total_charges


@dataclass
class RebalancePlan:
    """Complete rebalance plan with sell and buy orders and constraint metrics."""

    orders: list[RebalanceOrder] = field(default_factory=list)
    sell_orders: list[RebalanceOrder] = field(default_factory=list)
    buy_orders: list[RebalanceOrder] = field(default_factory=list)

    total_sell_value: Decimal = Decimal("0")
    total_buy_value: Decimal = Decimal("0")
    total_charges: Decimal = Decimal("0")
    net_cash_impact: Decimal = Decimal("0")  # positive = cash freed

    drift_signals_addressed: int = 0
    tax_warnings_count: int = 0
    max_orders_limit: int = 10

    # Execution constraint metrics
    total_turnover: Decimal = Decimal("0")
    turnover_cap: Decimal = Decimal("0")
    turnover_cap_reached: bool = False
    cash_buffer_retained: Decimal = Decimal("0")
    orders_suppressed_min_trade: int = 0
    orders_scaled_adv: int = 0
    orders_suppressed_unproven_noise: int = 0

    def add_order(self, order: RebalanceOrder):
        self.orders.append(order)
        if order.side == OrderSide.SELL:
            self.sell_orders.append(order)
            self.total_sell_value += order.estimated_value
        else:
            self.buy_orders.append(order)
            self.total_buy_value += order.estimated_value
        self.total_turnover = self.total_sell_value + self.total_buy_value
        self.total_charges += order.charges_total
        self.net_cash_impact = self.total_sell_value - self.total_buy_value
        if order.has_tax_warning:
            self.tax_warnings_count += 1
        if order.is_adv_capped:
            self.orders_scaled_adv += 1


def _get_max_rebalance_orders() -> int:
    """Get the max orders limit from config or settings."""
    try:
        rows = execute_sql("SELECT value FROM system_config WHERE key = 'max_rebalance_orders'")
        if rows:
            return int(rows[0]["value"])
    except Exception:
        pass
    return get_settings().max_rebalance_orders


def _get_holding_details(tradingsymbol: str, user_id: str = "default") -> dict | None:
    """Get holding details needed for order generation."""
    rows = execute_sql(
        """
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
    """,
        {"sym": tradingsymbol, "uid": user_id},
    )
    return rows[0] if rows else None


def _round_to_lot_size(quantity: int, lot_size: int = 1) -> int:
    """Round quantity down to the nearest lot size."""
    if lot_size <= 1:
        return max(quantity, 0)
    return (quantity // lot_size) * lot_size


def evaluate_evidence_hurdles(
    backtest: BacktestRunDTO | None,
) -> tuple[str, str, dict[str, bool]]:
    """
    Evaluates whether a backtest run satisfies all 4 quantitative evidence hurdles:
    1. Predictive Power: Mean out-of-sample IC > 0 and p < 0.05 on at least one unpruned indicator.
    2. Stock Timing Skill: Strategy net CAGR > Stock Buy-and-Hold CAGR.
    3. Market Excess Alpha: Strategy net CAGR > NIFTY 50 TRI CAGR.
    4. Trade Quality: Win Rate >= 50%, Profit Factor > 1.0, and Total Trades >= 5.

    Returns:
        (status, badge, checks_dict)
        where status is "PROVEN_EDGE" | "UNPROVEN_NOISE" | "PENDING".
    """
    if backtest is None:
        return (
            "PENDING",
            "PENDING (No Backtest)",
            {
                "predictive_power": False,
                "stock_timing": False,
                "market_alpha": False,
                "trade_quality": False,
            },
        )

    # Hurdle 1: Predictive Power (mean_ic > 0 and p < 0.05 on at least one unpruned indicator)
    valid_indicators = [
        ind
        for ind in (backtest.indicators or [])
        if not getattr(ind, "is_pruned", True)
        and getattr(ind, "mean_ic", 0.0) > 0
        and getattr(ind, "p_value", 1.0) < 0.05
    ]
    predictive_power = len(valid_indicators) > 0 or bool(
        backtest.hurdle_details.get("predictive_power", False)
    )

    # Hurdle 2: Stock Timing Skill (CAGR_strategy, net > CAGR_stock)
    strat_cagr = backtest.strategy_cagr
    stock_cagr = backtest.stock_cagr
    stock_timing = strat_cagr is not None and stock_cagr is not None and strat_cagr > stock_cagr

    # Hurdle 3: Market Excess Alpha (CAGR_strategy, net > CAGR_benchmark)
    bench_cagr = backtest.benchmark_cagr
    market_alpha = strat_cagr is not None and bench_cagr is not None and strat_cagr > bench_cagr

    # Hurdle 4: Trade Quality (Win Rate >= 50%, Profit Factor > 1.0, Trades >= 5)
    win_rate = backtest.strategy_win_rate
    pf = backtest.strategy_profit_factor
    trades = backtest.total_trades
    trade_quality = (
        win_rate is not None and win_rate >= 0.50 and pf is not None and pf > 1.0 and trades >= 5
    )

    checks = {
        "predictive_power": bool(predictive_power),
        "stock_timing": bool(stock_timing),
        "market_alpha": bool(market_alpha),
        "trade_quality": bool(trade_quality),
    }

    if all(checks.values()):
        status = "PROVEN_EDGE"
        excess_cagr = (
            backtest.excess_cagr_vs_benchmark
            or (strat_cagr - bench_cagr if strat_cagr and bench_cagr else 0.0)
        ) * 100.0
        badge = f"PROVEN EDGE (Alpha: {excess_cagr:+.1f}%, Trades: {trades})"
    else:
        status = "UNPROVEN_NOISE"
        failed = [k for k, v in checks.items() if not v]
        badge = f"UNPROVEN NOISE (Failed: {', '.join(failed)})"

    return status, badge, checks


def resolve_evidence_status(
    symbol: str,
    backtest_lookup: dict[str, BacktestRunDTO] | None = None,
    signal_lookup: dict[str, HoldingSignalDTO] | None = None,
) -> tuple[str, str]:
    """Resolves (evidence_status, evidence_badge) for a symbol using provided lookups."""
    if signal_lookup and symbol in signal_lookup:
        sig = signal_lookup[symbol]
        status = getattr(sig, "status", "PENDING")
        badge = getattr(sig, "evidence_badge", "")
        if not badge:
            badge = (
                "PROVEN EDGE"
                if status == "PROVEN_EDGE"
                else "UNPROVEN NOISE"
                if status == "UNPROVEN_NOISE"
                else "PENDING (No Backtest)"
            )
        return status, badge

    if backtest_lookup and symbol in backtest_lookup:
        status, badge, _ = evaluate_evidence_hurdles(backtest_lookup[symbol])
        return status, badge

    return "PENDING", "PENDING (No Backtest)"


def generate_rebalance_plan(
    user_id: str = "default",
    dry_run: bool = True,
    skip_tax_warnings: bool = False,
    min_trade_value: Decimal | None = None,
    cash_buffer_pct: Decimal | None = None,
    cash_buffer_floor: Decimal | None = None,
    turnover_cap_pct: Decimal | None = None,
    adv_limit_pct: Decimal | None = None,
    adv_lookup: dict[str, int] | None = None,
    backtest_lookup: dict[str, BacktestRunDTO] | None = None,
    signal_lookup: dict[str, HoldingSignalDTO] | None = None,
    tactical_signals: list[HoldingSignalDTO] | None = None,
    suppress_unproven_buys: bool = False,
) -> RebalancePlan:
    """
    Generate a complete rebalance plan incorporating Indian transaction cost modeling,
    minimum trade size gating, cash buffer preservation, daily turnover limits, ADV volume caps,
    and evidence hurdle gating for tactical signals.

    Args:
        user_id: Portfolio owner.
        dry_run: If True, simulate order plan.
        skip_tax_warnings: If True, bypass tax warnings.
        min_trade_value: Override minimum trade size threshold (default Rs 2,000).
        cash_buffer_pct: Override cash buffer % (default 0.02 = 2%).
        cash_buffer_floor: Override cash buffer floor (default Rs 5,000).
        turnover_cap_pct: Override daily turnover cap % of AUM (default 0.15 = 15%).
        adv_limit_pct: Override ADV cap fraction (default 0.01 = 1%).
        adv_lookup: Optional dict mapping tradingsymbol -> 20-day Average Daily Volume.
        backtest_lookup: Optional dict mapping tradingsymbol -> BacktestRunDTO for evidence hurdles.
        signal_lookup: Optional dict mapping tradingsymbol -> HoldingSignalDTO.
        tactical_signals: Optional list of tactical signal recommendations to gate and allocate.
        suppress_unproven_buys: If True, also suppress drift buy orders for unproven scrips.

    Returns:
        RebalancePlan containing sized, constrained RebalanceOrder items.
    """
    settings = get_settings()
    min_trade_val = (
        min_trade_value if min_trade_value is not None else settings.rebalancer_min_trade_value
    )
    cb_pct = cash_buffer_pct if cash_buffer_pct is not None else settings.rebalancer_cash_buffer_pct
    cb_floor = (
        cash_buffer_floor
        if cash_buffer_floor is not None
        else settings.rebalancer_cash_buffer_floor
    )
    to_cap_pct = (
        turnover_cap_pct if turnover_cap_pct is not None else settings.rebalancer_turnover_cap_pct
    )
    adv_pct = adv_limit_pct if adv_limit_pct is not None else settings.rebalancer_adv_limit_pct
    adv_map = adv_lookup or {}

    logger.info("Generating constrained rebalance plan for user='{}'...", user_id)

    valuation = compute_portfolio_valuation(user_id)
    aum = (
        valuation.net_worth
        if valuation.net_worth > 0
        else (valuation.total_aum + valuation.available_cash)
    )

    cash_buffer = max(cb_pct * aum, cb_floor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    turnover_cap = (to_cap_pct * aum).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    max_orders = _get_max_rebalance_orders()

    plan = RebalancePlan(
        max_orders_limit=max_orders,
        turnover_cap=turnover_cap,
        cash_buffer_retained=cash_buffer,
    )

    signals = detect_drift(user_id, valuation)
    actionable = [s for s in signals if s.is_actionable]
    if not actionable and not tactical_signals:
        logger.info("No actionable drift signals or tactical signals. Portfolio is balanced.")
        return plan
    if not actionable:
        logger.info("No actionable drift signals; evaluating tactical signals.")

    # Sort actionable signals by absolute drift magnitude descending (most severe breaches first)
    actionable.sort(key=lambda s: abs(s.drift_pct), reverse=True)
    logger.info(
        "Processing {} actionable drift signals (AUM={}, TurnoverCap={}, CashBuffer={})...",
        len(actionable),
        aum,
        turnover_cap,
        cash_buffer,
    )

    running_turnover = Decimal("0.00")
    seen_sell_symbols: set[str] = set()

    # ─── SELL ORDERS (overweight → sell to target) ────────────
    for signal in actionable:
        if len(plan.orders) >= max_orders:
            logger.warning("Reached max orders limit ({}). Stopping sells.", max_orders)
            break

        if running_turnover >= turnover_cap:
            plan.turnover_cap_reached = True
            logger.info("Daily turnover cap reached ({}). Stopping sell generation.", turnover_cap)
            break

        if signal.direction != DriftDirection.OVERWEIGHT:
            continue

        if signal.drift_type == DriftType.CASH:
            continue

        if signal.drift_type == DriftType.SECTOR:
            sector_holdings = [
                h for h in valuation.holdings if (h.sector or "Uncategorised") == signal.name
            ]
            sector_holdings.sort(key=lambda h: h.weight_pct, reverse=True)

            sell_value_needed = abs(signal.rebalance_amount)
            for sh in sector_holdings:
                if sell_value_needed <= 0 or len(plan.orders) >= max_orders:
                    break
                if running_turnover >= turnover_cap:
                    plan.turnover_cap_reached = True
                    break

                details = _get_holding_details(sh.tradingsymbol, user_id)
                if details is None:
                    continue

                current_price = Decimal(str(details["current_price"]))
                if current_price <= 0:
                    continue

                lot_size = int(details.get("lot_size") or 1)
                max_sellable = details["quantity"]  # Settled shares only
                if max_sellable <= 0:
                    continue

                raw_sell_qty = int(
                    (sell_value_needed / current_price).to_integral_value(rounding=ROUND_DOWN)
                )
                sell_qty = min(raw_sell_qty, max_sellable)

                # Check if this represents a 100% position exit
                is_full_liq = sell_qty >= max_sellable and details.get("t1_quantity", 0) == 0
                if is_full_liq:
                    sell_qty = max_sellable

                # 20-day ADV constraint
                adv_val = adv_map.get(sh.tradingsymbol)
                is_adv_capped = False
                orig_qty = sell_qty
                if adv_val is not None and adv_val > 0:
                    max_adv_qty = int(
                        (Decimal(str(adv_val)) * adv_pct).to_integral_value(rounding=ROUND_DOWN)
                    )
                    if sell_qty > max_adv_qty:
                        sell_qty = max_adv_qty
                        is_adv_capped = True

                sell_qty = _round_to_lot_size(sell_qty, lot_size)
                if is_full_liq and lot_size <= 1:
                    sell_qty = max_sellable

                # Turnover cap check
                rem_to = turnover_cap - running_turnover
                if sell_qty * current_price > rem_to:
                    capped_qty = int(
                        (rem_to / current_price).to_integral_value(rounding=ROUND_DOWN)
                    )
                    sell_qty = _round_to_lot_size(capped_qty, lot_size)

                # Minimum trade value check (exempt full liquidations)
                order_val = (sell_qty * current_price).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                if order_val < min_trade_val:
                    if not is_full_liq:
                        logger.debug(
                            "Suppressing sell order for {}: value {} < min_trade_val {}",
                            sh.tradingsymbol,
                            order_val,
                            min_trade_val,
                        )
                        plan.orders_suppressed_min_trade += 1
                        continue

                if sell_qty <= 0:
                    continue

                tax_warnings = []
                if not skip_tax_warnings:
                    tax_warnings = check_sell_tax_impact(sh.tradingsymbol, sell_qty, user_id)

                is_first_sell = sh.tradingsymbol not in seen_sell_symbols
                cost = compute_indian_delivery_charges(
                    side="SELL",
                    trade_value=order_val,
                    is_first_sell_of_scrip=is_first_sell,
                )
                seen_sell_symbols.add(sh.tradingsymbol)

                ev_status, ev_badge = resolve_evidence_status(
                    sh.tradingsymbol,
                    backtest_lookup=backtest_lookup,
                    signal_lookup=signal_lookup,
                )

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
                    cost=cost,
                    charges_total=cost.total_charges,
                    is_full_liquidation=is_full_liq,
                    is_adv_capped=is_adv_capped,
                    original_quantity=orig_qty if is_adv_capped else None,
                    adv_20=adv_val,
                    evidence_status=ev_status,
                    evidence_badge=ev_badge,
                )
                plan.add_order(order)
                plan.drift_signals_addressed += 1
                running_turnover += order.estimated_value
                sell_value_needed -= order.estimated_value
                if running_turnover >= turnover_cap:
                    plan.turnover_cap_reached = True

        elif signal.drift_type == DriftType.HOLDING:
            details = _get_holding_details(signal.name, user_id)
            if details is None:
                continue

            current_price = Decimal(str(details["current_price"]))
            if current_price <= 0:
                continue

            lot_size = int(details.get("lot_size") or 1)
            max_sellable = details["quantity"]
            if max_sellable <= 0:
                continue

            sell_value_needed = abs(signal.rebalance_amount)
            raw_sell_qty = int(
                (sell_value_needed / current_price).to_integral_value(rounding=ROUND_DOWN)
            )
            sell_qty = min(raw_sell_qty, max_sellable)

            is_full_liq = sell_qty >= max_sellable and details.get("t1_quantity", 0) == 0
            if is_full_liq:
                sell_qty = max_sellable

            adv_val = adv_map.get(signal.name)
            is_adv_capped = False
            orig_qty = sell_qty
            if adv_val is not None and adv_val > 0:
                max_adv_qty = int(
                    (Decimal(str(adv_val)) * adv_pct).to_integral_value(rounding=ROUND_DOWN)
                )
                if sell_qty > max_adv_qty:
                    sell_qty = max_adv_qty
                    is_adv_capped = True

            sell_qty = _round_to_lot_size(sell_qty, lot_size)
            if is_full_liq and lot_size <= 1:
                sell_qty = max_sellable

            rem_to = turnover_cap - running_turnover
            if sell_qty * current_price > rem_to:
                capped_qty = int((rem_to / current_price).to_integral_value(rounding=ROUND_DOWN))
                sell_qty = _round_to_lot_size(capped_qty, lot_size)

            order_val = (sell_qty * current_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if order_val < min_trade_val:
                if not is_full_liq:
                    logger.debug(
                        "Suppressing sell order for {}: value {} < min_trade_val {}",
                        signal.name,
                        order_val,
                        min_trade_val,
                    )
                    plan.orders_suppressed_min_trade += 1
                    continue

            if sell_qty <= 0:
                continue

            tax_warnings = []
            if not skip_tax_warnings:
                tax_warnings = check_sell_tax_impact(signal.name, sell_qty, user_id)

            reason = (
                OrderReason.CONCENTRATION_BREACH
                if signal.target_weight_pct
                == Decimal(
                    str(
                        execute_sql(
                            "SELECT value FROM system_config WHERE key = 'concentration_limit_pct'"
                        )[0]["value"]
                    )
                )
                else OrderReason.HOLDING_DRIFT
            )

            is_first_sell = signal.name not in seen_sell_symbols
            cost = compute_indian_delivery_charges(
                side="SELL",
                trade_value=order_val,
                is_first_sell_of_scrip=is_first_sell,
            )
            seen_sell_symbols.add(signal.name)

            ev_status, ev_badge = resolve_evidence_status(
                signal.name,
                backtest_lookup=backtest_lookup,
                signal_lookup=signal_lookup,
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
                cost=cost,
                charges_total=cost.total_charges,
                is_full_liquidation=is_full_liq,
                is_adv_capped=is_adv_capped,
                original_quantity=orig_qty if is_adv_capped else None,
                adv_20=adv_val,
                evidence_status=ev_status,
                evidence_badge=ev_badge,
            )
            plan.add_order(order)
            plan.drift_signals_addressed += 1
            running_turnover += order.estimated_value
            if running_turnover >= turnover_cap:
                plan.turnover_cap_reached = True

    # ─── BUY ORDERS (underweight → buy up to target, constrained by cash buffer) ─
    usable_cash = valuation.available_cash + plan.total_sell_value - cash_buffer
    usable_cash = max(Decimal("0.00"), usable_cash)

    for signal in actionable:
        if len(plan.orders) >= max_orders:
            break

        if running_turnover >= turnover_cap:
            plan.turnover_cap_reached = True
            logger.info("Turnover cap reached. Stopping buy generation.")
            break

        if signal.direction != DriftDirection.UNDERWEIGHT:
            continue

        if signal.drift_type == DriftType.CASH:
            continue

        if usable_cash <= 0:
            logger.info(
                "Usable cash exhausted while retaining cash buffer of {}. Stopping buys.",
                cash_buffer,
            )
            break

        target_symbol = None
        if signal.drift_type == DriftType.HOLDING:
            target_symbol = signal.name
        elif signal.drift_type == DriftType.SECTOR:
            sector_holdings = [
                h for h in valuation.holdings if (h.sector or "Uncategorised") == signal.name
            ]
            if sector_holdings:
                sector_holdings.sort(key=lambda h: h.weight_pct)
                target_symbol = sector_holdings[0].tradingsymbol

        if target_symbol is None:
            continue

        details = _get_holding_details(target_symbol, user_id)
        if details is None:
            continue

        current_price = Decimal(str(details["current_price"]))
        if current_price <= 0:
            continue

        lot_size = int(details.get("lot_size") or 1)
        buy_value_needed = min(abs(signal.rebalance_amount), usable_cash)

        rem_to = turnover_cap - running_turnover
        buy_value_needed = min(buy_value_needed, rem_to)

        if buy_value_needed < current_price:
            continue

        raw_buy_qty = int((buy_value_needed / current_price).to_integral_value(rounding=ROUND_DOWN))

        adv_val = adv_map.get(target_symbol)
        is_adv_capped = False
        orig_qty = raw_buy_qty
        if adv_val is not None and adv_val > 0:
            max_adv_qty = int(
                (Decimal(str(adv_val)) * adv_pct).to_integral_value(rounding=ROUND_DOWN)
            )
            if raw_buy_qty > max_adv_qty:
                raw_buy_qty = max_adv_qty
                is_adv_capped = True

        buy_qty = _round_to_lot_size(raw_buy_qty, lot_size)
        order_val = (buy_qty * current_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if order_val < min_trade_val:
            logger.debug(
                "Suppressing buy order for {}: value {} < min_trade_val {}",
                target_symbol,
                order_val,
                min_trade_val,
            )
            plan.orders_suppressed_min_trade += 1
            continue

        if buy_qty <= 0:
            continue

        ev_status, ev_badge = resolve_evidence_status(
            target_symbol,
            backtest_lookup=backtest_lookup,
            signal_lookup=signal_lookup,
        )

        if suppress_unproven_buys and ev_status == "UNPROVEN_NOISE":
            plan.orders_suppressed_unproven_noise += 1
            logger.warning(
                "[GATE] Suppressed order for {}: Unproven signal (Failed Hurdle).",
                target_symbol,
            )
            continue

        cost = compute_indian_delivery_charges(side="BUY", trade_value=order_val)

        order = RebalanceOrder(
            tradingsymbol=target_symbol,
            exchange=details.get("exchange", "NSE"),
            instrument_token=details["instrument_token"],
            side=OrderSide.BUY,
            quantity=buy_qty,
            estimated_price=current_price,
            reason=OrderReason.SECTOR_DRIFT
            if signal.drift_type == DriftType.SECTOR
            else OrderReason.HOLDING_DRIFT,
            drift_signal=signal,
            cost=cost,
            charges_total=cost.total_charges,
            is_full_liquidation=False,
            is_adv_capped=is_adv_capped,
            original_quantity=orig_qty if is_adv_capped else None,
            adv_20=adv_val,
            evidence_status=ev_status,
            evidence_badge=ev_badge,
        )
        plan.add_order(order)
        plan.drift_signals_addressed += 1
        usable_cash -= order.estimated_value
        running_turnover += order.estimated_value
        if running_turnover >= turnover_cap:
            plan.turnover_cap_reached = True

    # ─── TACTICAL SIGNAL ORDERS (Evidence-gated BUY signals) ───
    if tactical_signals:
        for sig in tactical_signals:
            if len(plan.orders) >= max_orders:
                break
            if running_turnover >= turnover_cap:
                plan.turnover_cap_reached = True
                break
            if usable_cash <= 0:
                break

            sym = getattr(sig, "tradingsymbol", None) or getattr(sig, "symbol", "")
            if not sym:
                continue

            ev_status, ev_badge = resolve_evidence_status(
                sym,
                backtest_lookup=backtest_lookup,
                signal_lookup=signal_lookup or {sym: sig},
            )

            # Fail-closed gate: strictly suppress UNPROVEN_NOISE
            if ev_status == "UNPROVEN_NOISE":
                plan.orders_suppressed_unproven_noise += 1
                logger.warning(
                    "[GATE] Suppressed order for {}: Unproven signal (Failed Hurdle).",
                    sym,
                )
                continue

            if ev_status != "PROVEN_EDGE":
                continue

            # Only BUY or STRONG_BUY (or composite_score >= 60.0)
            sig_label = getattr(sig, "signal_label", "HOLD")
            comp_score = getattr(sig, "composite_score", 50.0)
            if sig_label not in ("BUY", "STRONG_BUY") and comp_score < 60.0:
                continue

            details = _get_holding_details(sym, user_id)
            if details is None:
                continue

            current_price = Decimal(str(details["current_price"]))
            if current_price <= 0:
                continue

            lot_size = int(details.get("lot_size") or 1)
            rem_to = turnover_cap - running_turnover
            # Sizing tactical tilt: 5% of AUM or min_trade_val
            tactical_budget = min(
                (Decimal("0.05") * aum).quantize(Decimal("0.01")),
                usable_cash,
                rem_to,
            )
            tactical_budget = max(tactical_budget, min_trade_val)
            tactical_budget = min(tactical_budget, usable_cash, rem_to)

            if tactical_budget < current_price:
                continue

            raw_buy_qty = int(
                (tactical_budget / current_price).to_integral_value(rounding=ROUND_DOWN)
            )

            adv_val = adv_map.get(sym)
            is_adv_capped = False
            orig_qty = raw_buy_qty
            if adv_val is not None and adv_val > 0:
                max_adv_qty = int(
                    (Decimal(str(adv_val)) * adv_pct).to_integral_value(rounding=ROUND_DOWN)
                )
                if raw_buy_qty > max_adv_qty:
                    raw_buy_qty = max_adv_qty
                    is_adv_capped = True

            buy_qty = _round_to_lot_size(raw_buy_qty, lot_size)
            order_val = (buy_qty * current_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            if order_val < min_trade_val:
                plan.orders_suppressed_min_trade += 1
                continue

            if buy_qty <= 0:
                continue

            cost = compute_indian_delivery_charges(side="BUY", trade_value=order_val)
            order = RebalanceOrder(
                tradingsymbol=sym,
                exchange=details.get("exchange", "NSE"),
                instrument_token=details["instrument_token"],
                side=OrderSide.BUY,
                quantity=buy_qty,
                estimated_price=current_price,
                reason=OrderReason.TACTICAL_SIGNAL,
                cost=cost,
                charges_total=cost.total_charges,
                is_full_liquidation=False,
                is_adv_capped=is_adv_capped,
                original_quantity=orig_qty if is_adv_capped else None,
                adv_20=adv_val,
                evidence_status=ev_status,
                evidence_badge=ev_badge,
            )
            plan.add_order(order)
            usable_cash -= order.estimated_value
            running_turnover += order.estimated_value
            if running_turnover >= turnover_cap:
                plan.turnover_cap_reached = True

    logger.success(
        "Rebalance plan complete: {} orders ({} sells, {} buys), "
        "total turnover: {} / cap {}, charges: {}, min_trade suppressed: {}, adv scaled: {}, unproven suppressed: {}",
        len(plan.orders),
        len(plan.sell_orders),
        len(plan.buy_orders),
        plan.total_turnover,
        plan.turnover_cap,
        plan.total_charges,
        plan.orders_suppressed_min_trade,
        plan.orders_scaled_adv,
        plan.orders_suppressed_unproven_noise,
    )
    return plan


def generate_rebalance_orders(
    user_id: str = "default",
    dry_run: bool = True,
    **kwargs: Any,
) -> list[RebalanceOrder]:
    """Convenience alias returning only the list of RebalanceOrder objects."""
    plan = generate_rebalance_plan(user_id=user_id, dry_run=dry_run, **kwargs)
    return plan.orders


def get_rebalance_summary(user_id: str = "default", **kwargs: Any) -> dict[str, Any]:
    """JSON-friendly rebalance plan summary for the dashboard and API."""
    plan = generate_rebalance_plan(user_id=user_id, **kwargs)
    return {
        "total_orders": len(plan.orders),
        "sell_orders": len(plan.sell_orders),
        "buy_orders": len(plan.buy_orders),
        "total_sell_value": float(plan.total_sell_value),
        "total_buy_value": float(plan.total_buy_value),
        "total_charges": float(plan.total_charges),
        "total_turnover": float(plan.total_turnover),
        "turnover_cap": float(plan.turnover_cap),
        "turnover_cap_reached": plan.turnover_cap_reached,
        "cash_buffer_retained": float(plan.cash_buffer_retained),
        "orders_suppressed_min_trade": plan.orders_suppressed_min_trade,
        "orders_scaled_adv": plan.orders_scaled_adv,
        "orders_suppressed_unproven_noise": plan.orders_suppressed_unproven_noise,
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
                "charges": float(o.charges_total),
                "cost_breakdown": {
                    "brokerage": float(o.cost.brokerage),
                    "stt": float(o.cost.stt),
                    "exchange_fee": float(o.cost.exchange_fee),
                    "sebi_fee": float(o.cost.sebi_fee),
                    "stamp_duty": float(o.cost.stamp_duty),
                    "gst": float(o.cost.gst),
                    "dp_charges": float(o.cost.dp_charges),
                    "total_charges": float(o.cost.total_charges),
                }
                if o.cost
                else None,
                "is_full_liquidation": o.is_full_liquidation,
                "is_adv_capped": o.is_adv_capped,
                "original_quantity": o.original_quantity,
                "has_tax_warning": o.has_tax_warning,
                "evidence_status": o.evidence_status,
                "evidence_badge": o.evidence_badge,
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
