"""
Tests for Indian Equity Delivery Cost Calculator (src/analytics/cost_calculator.py).
Validates charges against Zerodha and NSE delivery brokerage schedules.
"""

from decimal import Decimal

import pytest

from src.analytics.cost_calculator import (
    IndianTradeCost,
    compute_indian_delivery_charges,
)


def test_buy_cost_calculation_matches_zerodha():
    """Verify statutory Indian charges on Buy delivery for Rs 100,000 order."""
    cost = compute_indian_delivery_charges(side="BUY", trade_value=Decimal("100000.00"))

    assert isinstance(cost, IndianTradeCost)
    assert cost.side == "BUY"
    assert cost.trade_value == Decimal("100000.00")
    assert cost.brokerage == Decimal("0.00")  # Free delivery
    assert cost.stt == Decimal("100.00")  # 0.1% rounded to nearest rupee
    assert cost.exchange_fee == Decimal("2.97")  # 0.00297%
    assert cost.sebi_fee == Decimal("0.10")  # 0.0001% (Rs 10/crore)
    assert cost.stamp_duty == Decimal("15.00")  # 0.015%
    assert cost.gst == Decimal("0.55")  # 18% of (2.97 + 0.10 = 3.07) -> 0.55
    assert cost.dp_charges == Decimal("0.00")  # No DP charges on buy
    assert cost.total_charges == Decimal("118.62")
    assert cost.net_settlement_amount == Decimal("100118.62")
    # Drag bps = 118.62 / 100000 * 10000 = 11.86 bps
    assert cost.cost_drag_bps == Decimal("11.86")


def test_sell_cost_calculation_matches_zerodha_first_sell():
    """Verify statutory charges and DP charge on Sell delivery for Rs 100,000 order."""
    cost = compute_indian_delivery_charges(
        side="SELL", trade_value=Decimal("100000.00"), is_first_sell_of_scrip=True
    )

    assert cost.side == "SELL"
    assert cost.trade_value == Decimal("100000.00")
    assert cost.brokerage == Decimal("0.00")
    assert cost.stt == Decimal("100.00")  # 0.1%
    assert cost.exchange_fee == Decimal("2.97")
    assert cost.sebi_fee == Decimal("0.10")
    assert cost.stamp_duty == Decimal("0.00")  # Stamp duty only on BUY
    assert cost.gst == Decimal("0.55")
    assert cost.dp_charges == Decimal("15.34")  # Rs 13 + 18% GST
    assert cost.total_charges == Decimal("118.96")
    assert cost.net_settlement_amount == Decimal("99881.04")
    assert cost.cost_drag_bps == Decimal("11.90")


def test_sell_cost_calculation_subsequent_sell_same_scrip():
    """Verify DP charges waived on subsequent sell of the same scrip on the same day."""
    cost = compute_indian_delivery_charges(
        side="SELL", trade_value=Decimal("100000.00"), is_first_sell_of_scrip=False
    )

    assert cost.dp_charges == Decimal("0.00")
    assert cost.total_charges == Decimal("103.62")
    assert cost.net_settlement_amount == Decimal("99896.38")


def test_zero_or_negative_trade_value():
    """Verify clean zero-charge object for non-positive trade values."""
    cost_zero = compute_indian_delivery_charges(side="BUY", trade_value=Decimal("0.00"))
    assert cost_zero.total_charges == Decimal("0.00")
    assert cost_zero.cost_drag_bps == Decimal("0.00")

    cost_neg = compute_indian_delivery_charges(side="SELL", trade_value=Decimal("-500.00"))
    assert cost_neg.total_charges == Decimal("0.00")


def test_invalid_side_raises_value_error():
    """Verify invalid side raises ValueError."""
    with pytest.raises(ValueError, match="side must be 'BUY' or 'SELL'"):
        compute_indian_delivery_charges(side="HOLD", trade_value=Decimal("1000.00"))
