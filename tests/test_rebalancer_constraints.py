"""
Tests for Rebalancer Execution Constraints & Indian Cost Model Integration
(src/analytics/rebalancer.py).

Validates:
- Minimum trade size filter (Rs 2,000 threshold, full liquidation exemption).
- Cash reserve buffer preservation (max(2% AUM, Rs 5,000)).
- Daily turnover cap (15% AUM) with absolute drift magnitude prioritization.
- 20-day ADV volume guard (<= 1% ADV).
- Summary serialization with Indian cost breakdown.
"""

from decimal import Decimal
from unittest.mock import patch

from src.analytics.drift_detector import DriftDirection, DriftSignal, DriftType
from src.analytics.rebalancer import (
    OrderSide,
    generate_rebalance_orders,
    generate_rebalance_plan,
    get_rebalance_summary,
)
from src.analytics.valuator import HoldingValuation, PortfolioValuation


def _mock_valuation(
    holdings: list[HoldingValuation] | None = None,
    available_cash: Decimal = Decimal("50000.00"),
    total_aum: Decimal = Decimal("500000.00"),
) -> PortfolioValuation:
    return PortfolioValuation(
        user_id="default",
        holdings=holdings or [],
        total_aum=total_aum,
        available_cash=available_cash,
        net_worth=total_aum + available_cash,
    )


def test_sub_min_trade_value_suppression():
    """Verify orders with value < Rs 2,000 are suppressed unless liquidating."""
    # Underweight signal requiring Rs 1,500 buy (< Rs 2,000)
    signal = DriftSignal(
        drift_type=DriftType.HOLDING,
        name="INFY",
        current_weight_pct=Decimal("5.0"),
        target_weight_pct=Decimal("6.0"),
        drift_pct=Decimal("-1.0"),
        threshold_pct=Decimal("0.5"),
        direction=DriftDirection.UNDERWEIGHT,
        severity="MEDIUM",
        rebalance_amount=Decimal("1500.00"),
    )

    val = _mock_valuation(available_cash=Decimal("50000.00"), total_aum=Decimal("500000.00"))

    with (
        patch("src.analytics.rebalancer.compute_portfolio_valuation", return_value=val),
        patch("src.analytics.rebalancer.detect_drift", return_value=[signal]),
        patch(
            "src.analytics.rebalancer._get_holding_details",
            return_value={
                "instrument_token": 408065,
                "exchange": "NSE",
                "quantity": 10,
                "t1_quantity": 0,
                "average_price": Decimal("1500.00"),
                "current_price": Decimal("1500.00"),
                "lot_size": 1,
            },
        ),
    ):
        plan = generate_rebalance_plan(min_trade_value=Decimal("2000.00"))
        assert len(plan.orders) == 0
        assert plan.orders_suppressed_min_trade == 1


def test_full_liquidation_exempt_from_min_trade_value():
    """Verify 100% position liquidations bypass the minimum trade size threshold."""
    # Holding has 2 shares @ Rs 500 = Rs 1,000 total (< Rs 2,000 min trade value)
    signal = DriftSignal(
        drift_type=DriftType.HOLDING,
        name="SMALLCAP",
        current_weight_pct=Decimal("1.0"),
        target_weight_pct=Decimal("0.0"),
        drift_pct=Decimal("1.0"),
        threshold_pct=Decimal("0.5"),
        direction=DriftDirection.OVERWEIGHT,
        severity="MEDIUM",
        rebalance_amount=Decimal("-1000.00"),
    )

    val = _mock_valuation(total_aum=Decimal("100000.00"))

    with (
        patch("src.analytics.rebalancer.compute_portfolio_valuation", return_value=val),
        patch("src.analytics.rebalancer.detect_drift", return_value=[signal]),
        patch(
            "src.analytics.rebalancer._get_holding_details",
            return_value={
                "instrument_token": 999999,
                "exchange": "NSE",
                "quantity": 2,
                "t1_quantity": 0,
                "average_price": Decimal("500.00"),
                "current_price": Decimal("500.00"),
                "lot_size": 1,
            },
        ),
        patch("src.analytics.rebalancer.check_sell_tax_impact", return_value=[]),
    ):
        plan = generate_rebalance_plan(min_trade_value=Decimal("2000.00"))
        assert len(plan.orders) == 1
        order = plan.orders[0]
        assert order.tradingsymbol == "SMALLCAP"
        assert order.side == OrderSide.SELL
        assert order.quantity == 2
        assert order.estimated_value == Decimal("1000.00")
        assert order.is_full_liquidation is True
        assert plan.orders_suppressed_min_trade == 0


def test_cash_buffer_preservation():
    """Verify buy orders respect cash buffer = max(2% AUM, Rs 5,000)."""
    # AUM = Rs 200,000. Buffer = max(0.02 * 200,000 = 4,000, 5,000) = Rs 5,000.
    # Total cash = Rs 8,000 -> Usable cash for buys = Rs 3,000.
    # Stock price is Rs 1,000. Signal wants Rs 10,000 buy.
    # Buy order should be capped at 3 shares (Rs 3,000), leaving Rs 5,000 cash buffer intact.
    signal = DriftSignal(
        drift_type=DriftType.HOLDING,
        name="TCS",
        current_weight_pct=Decimal("2.0"),
        target_weight_pct=Decimal("7.0"),
        drift_pct=Decimal("-5.0"),
        threshold_pct=Decimal("1.0"),
        direction=DriftDirection.UNDERWEIGHT,
        severity="HIGH",
        rebalance_amount=Decimal("10000.00"),
    )

    val = PortfolioValuation(
        user_id="default",
        holdings=[],
        total_aum=Decimal("192000.00"),
        available_cash=Decimal("8000.00"),
        net_worth=Decimal("200000.00"),
    )

    with (
        patch("src.analytics.rebalancer.compute_portfolio_valuation", return_value=val),
        patch("src.analytics.rebalancer.detect_drift", return_value=[signal]),
        patch(
            "src.analytics.rebalancer._get_holding_details",
            return_value={
                "instrument_token": 2953217,
                "exchange": "NSE",
                "quantity": 10,
                "t1_quantity": 0,
                "average_price": Decimal("1000.00"),
                "current_price": Decimal("1000.00"),
                "lot_size": 1,
            },
        ),
    ):
        plan = generate_rebalance_plan(
            cash_buffer_pct=Decimal("0.02"),
            cash_buffer_floor=Decimal("5000.00"),
            min_trade_value=Decimal("2000.00"),
        )
        assert len(plan.orders) == 1
        order = plan.orders[0]
        assert order.tradingsymbol == "TCS"
        assert order.side == OrderSide.BUY
        assert order.quantity == 3  # Exactly Rs 3,000
        assert order.estimated_value == Decimal("3000.00")
        assert plan.cash_buffer_retained == Decimal("5000.00")


def test_turnover_cap_and_severity_prioritization():
    """Verify daily turnover cap limits orders and prioritizes highest absolute drift."""
    # AUM = Rs 100,000. Turnover cap = 15% = Rs 15,000.
    # Signal 1: drift +5%, needs Rs 10,000 sell
    # Signal 2: drift +12%, needs Rs 10,000 sell (Most severe)
    # Signal 3: drift +8%, needs Rs 10,000 sell
    # Priority order should be: Signal 2 (Rs 10,000) -> Signal 3 (budget Rs 5,000) -> Signal 1 (0)
    s1 = DriftSignal(
        drift_type=DriftType.HOLDING,
        name="STOCK_A",
        current_weight_pct=Decimal("15.0"),
        target_weight_pct=Decimal("10.0"),
        drift_pct=Decimal("5.0"),
        threshold_pct=Decimal("1.0"),
        direction=DriftDirection.OVERWEIGHT,
        severity="MEDIUM",
        rebalance_amount=Decimal("-10000.00"),
    )
    s2 = DriftSignal(
        drift_type=DriftType.HOLDING,
        name="STOCK_B",
        current_weight_pct=Decimal("22.0"),
        target_weight_pct=Decimal("10.0"),
        drift_pct=Decimal("12.0"),
        threshold_pct=Decimal("1.0"),
        direction=DriftDirection.OVERWEIGHT,
        severity="HIGH",
        rebalance_amount=Decimal("-10000.00"),
    )
    s3 = DriftSignal(
        drift_type=DriftType.HOLDING,
        name="STOCK_C",
        current_weight_pct=Decimal("18.0"),
        target_weight_pct=Decimal("10.0"),
        drift_pct=Decimal("8.0"),
        threshold_pct=Decimal("1.0"),
        direction=DriftDirection.OVERWEIGHT,
        severity="HIGH",
        rebalance_amount=Decimal("-10000.00"),
    )

    val = PortfolioValuation(
        user_id="default",
        holdings=[],
        total_aum=Decimal("90000.00"),
        available_cash=Decimal("10000.00"),
        net_worth=Decimal("100000.00"),
    )

    def mock_holding_details(sym, uid):
        return {
            "instrument_token": 100,
            "exchange": "NSE",
            "quantity": 50,
            "t1_quantity": 0,
            "average_price": Decimal("1000.00"),
            "current_price": Decimal("1000.00"),
            "lot_size": 1,
        }

    with (
        patch("src.analytics.rebalancer.compute_portfolio_valuation", return_value=val),
        patch("src.analytics.rebalancer.detect_drift", return_value=[s1, s2, s3]),
        patch("src.analytics.rebalancer._get_holding_details", side_effect=mock_holding_details),
        patch("src.analytics.rebalancer.check_sell_tax_impact", return_value=[]),
    ):
        plan = generate_rebalance_plan(
            turnover_cap_pct=Decimal("0.15"),
            min_trade_value=Decimal("2000.00"),
        )

        assert plan.turnover_cap == Decimal("15000.00")
        assert plan.turnover_cap_reached is True
        assert len(plan.orders) == 2
        # Order 1: STOCK_B (10 shares @ Rs 1,000 = Rs 10,000)
        assert plan.orders[0].tradingsymbol == "STOCK_B"
        assert plan.orders[0].quantity == 10
        # Order 2: STOCK_C (budgeted down to 5 shares = Rs 5,000)
        assert plan.orders[1].tradingsymbol == "STOCK_C"
        assert plan.orders[1].quantity == 5
        assert plan.total_turnover == Decimal("15000.00")


def test_adv_volume_guard():
    """Verify order quantity is capped at 1.0% of 20-day ADV."""
    signal = DriftSignal(
        drift_type=DriftType.HOLDING,
        name="LIQUID_STOCK",
        current_weight_pct=Decimal("20.0"),
        target_weight_pct=Decimal("10.0"),
        drift_pct=Decimal("10.0"),
        threshold_pct=Decimal("1.0"),
        direction=DriftDirection.OVERWEIGHT,
        severity="HIGH",
        rebalance_amount=Decimal("-50000.00"),
    )

    val = _mock_valuation(total_aum=Decimal("500000.00"))

    with (
        patch("src.analytics.rebalancer.compute_portfolio_valuation", return_value=val),
        patch("src.analytics.rebalancer.detect_drift", return_value=[signal]),
        patch(
            "src.analytics.rebalancer._get_holding_details",
            return_value={
                "instrument_token": 12345,
                "exchange": "NSE",
                "quantity": 1000,
                "t1_quantity": 0,
                "average_price": Decimal("500.00"),
                "current_price": Decimal("500.00"),
                "lot_size": 1,
            },
        ),
        patch("src.analytics.rebalancer.check_sell_tax_impact", return_value=[]),
    ):
        # Desired sell is Rs 50,000 / Rs 500 = 100 shares.
        # But ADV is 5,000 shares -> 1% ADV cap is 50 shares.
        plan = generate_rebalance_plan(
            adv_limit_pct=Decimal("0.01"),
            adv_lookup={"LIQUID_STOCK": 5000},
        )

        assert len(plan.orders) == 1
        order = plan.orders[0]
        assert order.tradingsymbol == "LIQUID_STOCK"
        assert order.quantity == 50  # Scaled from 100 to 50
        assert order.is_adv_capped is True
        assert order.original_quantity == 100
        assert plan.orders_scaled_adv == 1


def test_order_and_summary_cost_breakdown():
    """Verify Indian charges are attached to orders and included in plan summary."""
    signal = DriftSignal(
        drift_type=DriftType.HOLDING,
        name="RELIANCE",
        current_weight_pct=Decimal("20.0"),
        target_weight_pct=Decimal("10.0"),
        drift_pct=Decimal("10.0"),
        threshold_pct=Decimal("1.0"),
        direction=DriftDirection.OVERWEIGHT,
        severity="HIGH",
        rebalance_amount=Decimal("-100000.00"),
    )

    val = _mock_valuation(total_aum=Decimal("1000000.00"))

    with (
        patch("src.analytics.rebalancer.compute_portfolio_valuation", return_value=val),
        patch("src.analytics.rebalancer.detect_drift", return_value=[signal]),
        patch(
            "src.analytics.rebalancer._get_holding_details",
            return_value={
                "instrument_token": 738561,
                "exchange": "NSE",
                "quantity": 100,
                "t1_quantity": 0,
                "average_price": Decimal("2500.00"),
                "current_price": Decimal("2500.00"),
                "lot_size": 1,
            },
        ),
        patch("src.analytics.rebalancer.check_sell_tax_impact", return_value=[]),
    ):
        # Sells 40 shares @ 2500 = Rs 100,000
        plan = generate_rebalance_plan()
        assert len(plan.orders) == 1
        order = plan.orders[0]
        assert order.cost is not None
        assert order.cost.stt == Decimal("100.00")
        assert order.cost.dp_charges == Decimal("15.34")
        assert order.charges_total == Decimal("118.96")
        assert plan.total_charges == Decimal("118.96")

        # Test convenience function generate_rebalance_orders
        orders = generate_rebalance_orders()
        assert len(orders) == 1

        # Test summary JSON output
        summary = get_rebalance_summary()
        assert summary["total_orders"] == 1
        assert summary["total_charges"] == 118.96
        assert "cost_breakdown" in summary["orders"][0]
        assert summary["orders"][0]["cost_breakdown"]["dp_charges"] == 15.34
