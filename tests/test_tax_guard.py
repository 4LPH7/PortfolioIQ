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
    select_tax_optimized_lots,
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


def test_select_tax_optimized_lots_hierarchy():
    """
    Verify tax lot selection priority:
    STCL (highest loss first) -> LTCL -> LTCG -> STCG -> Near-LTCG (deferred).
    """
    today = date(2026, 6, 1)

    # Lot 1: STCL, bought 100 days ago @ Rs 1200 (current price Rs 1000 -> -16.67% loss)
    lot_stcl_1 = TaxLot(
        lot_id=1,
        holding_id=10,
        buy_date=today - date.resolution * 100,
        buy_price=Decimal("1200.00"),
        quantity=10,
        remaining_quantity=10,
        ref_date=today,
    )
    # Lot 2: STCL, bought 50 days ago @ Rs 1500 (current price Rs 1000 -> -33.33% loss)
    # Higher loss % should be sold FIRST!
    lot_stcl_2 = TaxLot(
        lot_id=2,
        holding_id=10,
        buy_date=today - date.resolution * 50,
        buy_price=Decimal("1500.00"),
        quantity=10,
        remaining_quantity=10,
        ref_date=today,
    )
    # Lot 3: LTCG in profit, bought 400 days ago @ Rs 800 (current price Rs 1000 -> +25% gain)
    lot_ltcg = TaxLot(
        lot_id=3,
        holding_id=10,
        buy_date=today - date.resolution * 400,
        buy_price=Decimal("800.00"),
        quantity=10,
        remaining_quantity=10,
        ref_date=today,
    )
    # Lot 4: STCG in profit, bought 150 days ago @ Rs 900 (current price Rs 1000 -> +11.11% gain)
    lot_stcg = TaxLot(
        lot_id=4,
        holding_id=10,
        buy_date=today - date.resolution * 150,
        buy_price=Decimal("900.00"),
        quantity=10,
        remaining_quantity=10,
        ref_date=today,
    )
    # Lot 5: Near-LTCG in profit, bought 350 days ago (15 days to LTCG cutoff) @ Rs 850
    lot_near_ltcg = TaxLot(
        lot_id=5,
        holding_id=10,
        buy_date=today - date.resolution * 350,
        buy_price=Decimal("850.00"),
        quantity=10,
        remaining_quantity=10,
        ref_date=today,
    )

    all_lots = [lot_near_ltcg, lot_stcg, lot_ltcg, lot_stcl_1, lot_stcl_2]

    # Target sell: 25 shares out of 50 total
    allocations = select_tax_optimized_lots(
        holding_id=10,
        target_quantity=25,
        current_price=Decimal("1000.00"),
        lots=all_lots,
        ref_date=today,
        is_full_liquidation=False,
    )

    assert len(allocations) == 3
    # 1. Lot 2 (-33.33% loss) sold first (10 shares)
    assert allocations[0].lot_id == 2
    assert allocations[0].tax_tier == "STCL"
    assert allocations[0].allocated_quantity == 10
    assert allocations[0].estimated_tax == Decimal("0.00")

    # 2. Lot 1 (-16.67% loss) sold second (10 shares)
    assert allocations[1].lot_id == 1
    assert allocations[1].tax_tier == "STCL"
    assert allocations[1].allocated_quantity == 10

    # 3. Lot 3 (LTCG gain) sold third for the remaining 5 shares
    assert allocations[2].lot_id == 3
    assert allocations[2].tax_tier == "LTCG"
    assert allocations[2].allocated_quantity == 5
    # Gain = (1000 - 800) * 5 = 1000. Tax @ 12.5% = 125.00
    assert allocations[2].estimated_tax == Decimal("125.00")

    # Verify Lot 5 (Near-LTCG) was NOT touched (strictly deferred)
    allocated_lot_ids = [a.lot_id for a in allocations]
    assert 5 not in allocated_lot_ids


def test_near_ltcg_deferral_and_full_liquidation():
    """Verify Near-LTCG lots (aged 335-365 days) are deferred unless liquidating."""
    today = date(2026, 6, 1)

    # Lot aged 350 days (15 days left to LTCG conversion)
    lot_near = TaxLot(
        lot_id=101,
        holding_id=20,
        buy_date=today - date.resolution * 350,
        buy_price=Decimal("1000.00"),
        quantity=10,
        remaining_quantity=10,
        ref_date=today,
    )

    assert lot_near.is_near_ltcg is True
    assert lot_near.days_to_ltcg == 15

    # 1. Partial rebalance sell: should defer lot and return empty
    alloc_partial = select_tax_optimized_lots(
        holding_id=20,
        target_quantity=5,
        current_price=Decimal("1200.00"),
        lots=[lot_near],
        ref_date=today,
        is_full_liquidation=False,
    )
    assert len(alloc_partial) == 0

    # 2. Full liquidation: permitted to sell Near-LTCG lot
    alloc_full = select_tax_optimized_lots(
        holding_id=20,
        target_quantity=10,
        current_price=Decimal("1200.00"),
        lots=[lot_near],
        ref_date=today,
        is_full_liquidation=True,
    )
    assert len(alloc_full) == 1
    assert alloc_full[0].lot_id == 101
    assert alloc_full[0].tax_tier == "NEAR_LTCG"
    assert alloc_full[0].allocated_quantity == 10


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
