"""
PortfolioIQ — Indian Equity Delivery Transaction Cost Model
Accurately computes statutory regulatory fees, exchange transaction charges,
taxes (STT, Stamp Duty, GST), and depository participant (DP) charges for
cash equity delivery trades on NSE via Zerodha.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass
class IndianTradeCost:
    """Detailed breakdown of Indian equity delivery transaction costs."""

    side: str  # "BUY" | "SELL"
    trade_value: Decimal
    brokerage: Decimal = Decimal("0.00")
    stt: Decimal = Decimal("0.00")
    exchange_fee: Decimal = Decimal("0.00")
    sebi_fee: Decimal = Decimal("0.00")
    stamp_duty: Decimal = Decimal("0.00")
    gst: Decimal = Decimal("0.00")
    dp_charges: Decimal = Decimal("0.00")
    total_charges: Decimal = Decimal("0.00")
    net_settlement_amount: Decimal = Decimal("0.00")
    cost_drag_bps: Decimal = Decimal("0.00")


def compute_indian_delivery_charges(
    side: str,
    trade_value: Decimal,
    is_first_sell_of_scrip: bool = True,
) -> IndianTradeCost:
    """
    Compute full Indian statutory and broker delivery charges for NSE equities.

    Fee Schedule (Equity Delivery / CNC on NSE):
        - Brokerage: Rs 0.00 (Zero brokerage on delivery)
        - STT (Securities Transaction Tax): 0.1% on buy & sell (rounded to nearest rupee)
        - Exchange Turnover Fee: 0.00297% (NSE)
        - SEBI Turnover Charges: 0.0001% (Rs 10 per crore)
        - Stamp Duty: 0.015% on BUY only (rounded to nearest rupee)
        - GST: 18% on (Brokerage + Exchange Fee + SEBI Fee)
        - DP Charges: Rs 15.34 (Rs 13.00 + 18% GST) on SELL only per scrip per day

    Args:
        side: "BUY" or "SELL".
        trade_value: Total value of the execution (quantity * price).
        is_first_sell_of_scrip: If False, DP charge is waived (already debited today).

    Returns:
        IndianTradeCost with all itemized components and total charges.
    """
    trade_value = Decimal(str(trade_value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if trade_value <= 0:
        return IndianTradeCost(
            side=side.upper(),
            trade_value=Decimal("0.00"),
        )

    side_norm = side.upper()
    if side_norm not in ("BUY", "SELL"):
        raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")

    # 1. Brokerage: Rs 0 for delivery
    brokerage = Decimal("0.00")

    # 2. STT: 0.1% on both Buy and Sell delivery, rounded to nearest rupee
    stt = (trade_value * Decimal("0.001")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    stt = stt.quantize(Decimal("0.01"))

    # 3. Exchange turnover charges: 0.00297% (NSE)
    exchange_fee = (trade_value * Decimal("0.0000297")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    # 4. SEBI turnover charges: 0.0001% (Rs 10 / crore)
    sebi_fee = (trade_value * Decimal("0.000001")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # 5. Stamp Duty: 0.015% on BUY only, rounded to nearest rupee
    if side_norm == "BUY":
        stamp_duty = (trade_value * Decimal("0.00015")).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        stamp_duty = stamp_duty.quantize(Decimal("0.01"))
    else:
        stamp_duty = Decimal("0.00")

    # 6. GST: 18% on (Brokerage + Exchange Fee + SEBI Fee)
    taxable_services = brokerage + exchange_fee + sebi_fee
    gst = (taxable_services * Decimal("0.18")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # 7. DP Charges: Rs 15.34 (Rs 13.00 + 18% GST) on SELL only
    if side_norm == "SELL" and is_first_sell_of_scrip:
        dp_charges = Decimal("15.34")
    else:
        dp_charges = Decimal("0.00")

    total_charges = (
        brokerage + stt + exchange_fee + sebi_fee + stamp_duty + gst + dp_charges
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if side_norm == "BUY":
        net_settlement = trade_value + total_charges
    else:
        net_settlement = trade_value - total_charges

    cost_drag_bps = (
        ((total_charges / trade_value) * Decimal("10000")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if trade_value > 0
        else Decimal("0.00")
    )

    return IndianTradeCost(
        side=side_norm,
        trade_value=trade_value,
        brokerage=brokerage,
        stt=stt,
        exchange_fee=exchange_fee,
        sebi_fee=sebi_fee,
        stamp_duty=stamp_duty,
        gst=gst,
        dp_charges=dp_charges,
        total_charges=total_charges,
        net_settlement_amount=net_settlement,
        cost_drag_bps=cost_drag_bps,
    )
