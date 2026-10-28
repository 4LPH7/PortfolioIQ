"""
Unit tests for Tax Guard, Tax-Loss Harvesting, and FY Exemption Tracking
(src/analytics/tax_guard.py).
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from src.analytics.tax_guard import (
    HoldingTaxProfile,
    TaxLot,
    check_sell_tax_impact,
    get_annual_tax_harvesting_summary,
    get_financial_year_bounds,
    get_tax_summary,
)


def test_financial_year_boundary_switch():
    """Verify Indian financial year boundaries (April 1 to March 31)."""
    # 1. Date in April (start of new FY)
    start, end, label = get_financial_year_bounds(date(2026, 4, 1))
    assert start == date(2026, 4, 1)
    assert end == date(2027, 3, 31)
    assert label == "FY 2026-27"

    # 2. Date in March (end of current FY)
    start_q4, end_q4, label_q4 = get_financial_year_bounds(date(2026, 3, 31))
    assert start_q4 == date(2025, 4, 1)
    assert end_q4 == date(2026, 3, 31)
    assert label_q4 == "FY 2025-26"

    # 3. Date in September
    start_mid, end_mid, label_mid = get_financial_year_bounds(date(2026, 9, 28))
    assert start_mid == date(2026, 4, 1)
    assert end_mid == date(2027, 3, 31)
    assert label_mid == "FY 2026-27"


def test_q4_gain_harvesting_within_exemption():
    """Verify Q4 tax-gain harvesting proposes LTCG gains up to remaining Rs 1.25L exemption."""
    q4_date = date(2026, 2, 15)  # February = Q4 of FY 2025-26

    # 1 LTCG lot with Rs 50,000 unrealized gain
    ltcg_lot = TaxLot(
        lot_id=201,
        holding_id=30,
        buy_date=date(2024, 1, 1),  # Held > 2 years -> LTCG
        buy_price=Decimal("1000.00"),
        quantity=100,
        remaining_quantity=100,
        ref_date=q4_date,
    )

    profile = HoldingTaxProfile(
        tradingsymbol="RELIANCE",
        exchange="NSE",
        instrument_token=738561,
        current_price=Decimal("1500.00"),  # Gain = Rs 500/share * 100 = Rs 50,000
        lots=[ltcg_lot],
    )

    with (
        patch("src.analytics.tax_guard.get_realized_ltcg_ytd", return_value=Decimal("25000.00")),
        patch("src.analytics.tax_guard.load_tax_lots", return_value={"RELIANCE": profile}),
    ):
        summary = get_annual_tax_harvesting_summary(user_id="default", ref_date=q4_date)

        assert summary.is_q4 is True
        assert summary.fy_year == "FY 2025-26"
        assert summary.ltcg_realized_ytd == Decimal("25000.00")
        assert summary.ltcg_exemption_remaining == Decimal("100000.00")  # 125,000 - 25,000

        # Should propose harvesting all 100 shares (Rs 50,000 gain fits comfortably within Rs 100,000)
        gain_ops = [o for o in summary.opportunities if o.action_type == "GAIN_HARVEST"]
        assert len(gain_ops) == 1
        assert gain_ops[0].tradingsymbol == "RELIANCE"
        assert gain_ops[0].quantity == 100
        assert gain_ops[0].unrealized_pnl == Decimal("50000.00")
        # Potential tax savings = 50,000 * 12.5% = 6,250.00
        assert gain_ops[0].potential_tax_savings == Decimal("6250.00")


def test_q4_gain_harvesting_exhausted_exemption():
    """Verify Q4 gain harvesting proposes nothing if the Rs 1.25L exemption is already exhausted."""
    q4_date = date(2026, 3, 1)

    ltcg_lot = TaxLot(
        lot_id=202,
        holding_id=30,
        buy_date=date(2024, 1, 1),
        buy_price=Decimal("1000.00"),
        quantity=50,
        remaining_quantity=50,
        ref_date=q4_date,
    )
    profile = HoldingTaxProfile(
        tradingsymbol="INFY",
        exchange="NSE",
        instrument_token=408065,
        current_price=Decimal("1500.00"),
        lots=[ltcg_lot],
    )

    with (
        patch("src.analytics.tax_guard.get_realized_ltcg_ytd", return_value=Decimal("130000.00")),
        patch("src.analytics.tax_guard.load_tax_lots", return_value={"INFY": profile}),
    ):
        summary = get_annual_tax_harvesting_summary(user_id="default", ref_date=q4_date)

        assert summary.ltcg_exemption_remaining == Decimal("0.00")
        gain_ops = [o for o in summary.opportunities if o.action_type == "GAIN_HARVEST"]
        assert len(gain_ops) == 0


def test_loss_harvesting_stcl_and_ltcl():
    """Verify unrealized loss lots are flagged with appropriate action_type and tax rate savings."""
    today = date(2026, 8, 1)

    stcl_lot = TaxLot(
        lot_id=301,
        holding_id=40,
        buy_date=today - date.resolution * 100,  # STCL
        buy_price=Decimal("2000.00"),
        quantity=20,
        remaining_quantity=20,
        ref_date=today,
    )
    profile = HoldingTaxProfile(
        tradingsymbol="TATASTEEL",
        exchange="NSE",
        instrument_token=895745,
        current_price=Decimal("1500.00"),  # Loss = -500 * 20 = -10,000
        lots=[stcl_lot],
    )

    with (
        patch("src.analytics.tax_guard.get_realized_ltcg_ytd", return_value=Decimal("0.00")),
        patch("src.analytics.tax_guard.load_tax_lots", return_value={"TATASTEEL": profile}),
    ):
        summary = get_annual_tax_harvesting_summary(user_id="default", ref_date=today)

        loss_ops = [o for o in summary.opportunities if o.action_type == "LOSS_HARVEST"]
        assert len(loss_ops) == 1
        assert loss_ops[0].tradingsymbol == "TATASTEEL"
        assert loss_ops[0].unrealized_pnl == Decimal("-10000.00")
        assert loss_ops[0].tax_type == "STCG"
        # 20% of Rs 10,000 = Rs 2,000
        assert loss_ops[0].potential_tax_savings == Decimal("2000.00")


def test_check_sell_tax_impact_and_get_tax_summary():
    """Verify existing warning generation and tax summary dashboard integration."""
    today = date.today()
    near_lot = TaxLot(
        lot_id=401,
        holding_id=50,
        buy_date=today - date.resolution * 355,  # 10 days to LTCG
        buy_price=Decimal("100.00"),
        quantity=10,
        remaining_quantity=10,
    )
    profile = HoldingTaxProfile(
        tradingsymbol="WIPRO",
        exchange="NSE",
        instrument_token=969473,
        current_price=Decimal("150.00"),
        lots=[near_lot],
    )
    profile.compute_aggregates()

    with patch("src.analytics.tax_guard.load_tax_lots", return_value={"WIPRO": profile}):
        warnings = check_sell_tax_impact("WIPRO", sell_quantity=5)
        assert any(w.warning_type == "NEAR_LTCG" for w in warnings)

        summary = get_tax_summary()
        assert summary["total_stcg_quantity"] == 10
        assert len(summary["near_ltcg_holdings"]) == 1
        assert summary["near_ltcg_holdings"][0]["symbol"] == "WIPRO"
