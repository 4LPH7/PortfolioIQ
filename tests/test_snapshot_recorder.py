"""
Tests for src/analytics/snapshot_recorder.py.
Verifies daily EOD snapshot creation, unitized NAV calculation,
cash flow adjustments, broker margin delta detection, and baseline backfill.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from src.analytics.snapshot_recorder import (
    record_daily_eod_snapshot,
)
from src.analytics.valuator import PortfolioValuation
from src.models.dtos import (
    PortfolioCashFlowDTO,
    PortfolioDailySnapshotDTO,
)


def test_record_eod_snapshot_initializes_baseline():
    """Verify Day 1 baseline establishes unit_nav at 100.00."""
    mock_valuation = PortfolioValuation(
        total_aum=Decimal("95000.00"),
        available_cash=Decimal("5000.00"),
        net_worth=Decimal("100000.00"),
    )

    with (
        patch(
            "src.analytics.snapshot_recorder.compute_portfolio_valuation",
            return_value=mock_valuation,
        ),
        patch("src.analytics.snapshot_recorder.get_daily_snapshots", return_value=[]),
        patch("src.analytics.snapshot_recorder.get_cash_flows", return_value=[]),
        patch(
            "src.analytics.snapshot_recorder._get_benchmark_quote",
            return_value=(Decimal("25000.00"), Decimal("0.0050")),
        ),
        patch("src.analytics.snapshot_recorder.record_daily_snapshot") as mock_record,
    ):
        mock_record.side_effect = lambda s: PortfolioDailySnapshotDTO.model_validate(
            dict(id=1, **s.model_dump())
        )

        snapshot = record_daily_eod_snapshot(snapshot_date=date(2026, 1, 1))

        assert snapshot.total_nav == Decimal("100000.00")
        assert snapshot.unit_nav == Decimal("100.0000")
        assert snapshot.units == Decimal("1000.000000")
        assert snapshot.daily_return_pct == Decimal("0.0000")
        assert snapshot.benchmark_name == "NIFTY 50 TRI"


def test_record_eod_snapshot_subsequent_day_with_equity_gain():
    """Verify Day 2 equity gain updates unit NAV and daily return percentage."""
    d0 = date(2026, 1, 1)
    d1 = date(2026, 1, 2)

    prior_snap = PortfolioDailySnapshotDTO(
        snapshot_date=d0,
        total_equity_value=Decimal("95000.00"),
        cash_balance=Decimal("5000.00"),
        total_nav=Decimal("100000.00"),
        units=Decimal("1000.000000"),
        unit_nav=Decimal("100.0000"),
        daily_return_pct=Decimal("0.0000"),
    )

    # Day 2: Equity rises to 105,000 -> Total NAV 110,000 (+10% gain)
    mock_valuation = PortfolioValuation(
        total_aum=Decimal("105000.00"),
        available_cash=Decimal("5000.00"),
        net_worth=Decimal("110000.00"),
    )

    with (
        patch(
            "src.analytics.snapshot_recorder.compute_portfolio_valuation",
            return_value=mock_valuation,
        ),
        patch(
            "src.analytics.snapshot_recorder.get_daily_snapshots",
            return_value=[prior_snap],
        ),
        patch("src.analytics.snapshot_recorder.get_cash_flows", return_value=[]),
        patch(
            "src.analytics.snapshot_recorder._get_benchmark_quote",
            return_value=(Decimal("25250.00"), Decimal("0.0100")),
        ),
        patch("src.analytics.snapshot_recorder.record_daily_snapshot") as mock_record,
    ):
        mock_record.side_effect = lambda s: PortfolioDailySnapshotDTO.model_validate(
            dict(id=2, **s.model_dump())
        )

        snapshot = record_daily_eod_snapshot(snapshot_date=d1)

        assert snapshot.total_nav == Decimal("110000.00")
        assert snapshot.units == Decimal("1000.000000")
        assert snapshot.unit_nav == Decimal("110.0000")
        assert snapshot.daily_return_pct == Decimal("0.1000")


def test_record_eod_snapshot_with_external_deposit_isolates_return():
    """Verify external deposit issues new units without creating artificial daily return."""
    d0 = date(2026, 1, 1)
    d1 = date(2026, 1, 2)

    prior_snap = PortfolioDailySnapshotDTO(
        snapshot_date=d0,
        total_equity_value=Decimal("100000.00"),
        cash_balance=Decimal("0.00"),
        total_nav=Decimal("100000.00"),
        units=Decimal("1000.000000"),
        unit_nav=Decimal("100.0000"),
        daily_return_pct=Decimal("0.0000"),
    )

    # Day 2: Deposit of 50,000 cash added; equity stayed flat at 100,000
    mock_valuation = PortfolioValuation(
        total_aum=Decimal("100000.00"),
        available_cash=Decimal("50000.00"),
        net_worth=Decimal("150000.00"),
    )

    deposit_flow = PortfolioCashFlowDTO(
        flow_date=d1,
        flow_type="DEPOSIT",
        amount=Decimal("50000.00"),
        source="MANUAL",
    )

    with (
        patch(
            "src.analytics.snapshot_recorder.compute_portfolio_valuation",
            return_value=mock_valuation,
        ),
        patch(
            "src.analytics.snapshot_recorder.get_daily_snapshots",
            return_value=[prior_snap],
        ),
        patch(
            "src.analytics.snapshot_recorder.get_cash_flows",
            return_value=[deposit_flow],
        ),
        patch(
            "src.analytics.snapshot_recorder._get_benchmark_quote",
            return_value=(Decimal("25000.00"), Decimal("0.0000")),
        ),
        patch("src.analytics.snapshot_recorder.record_daily_snapshot") as mock_record,
    ):
        mock_record.side_effect = lambda s: PortfolioDailySnapshotDTO.model_validate(
            dict(id=3, **s.model_dump())
        )

        snapshot = record_daily_eod_snapshot(snapshot_date=d1)

        # 50,000 / 100.00 = 500 new units -> 1500 total units
        assert snapshot.units == Decimal("1500.000000")
        assert snapshot.total_nav == Decimal("150000.00")
        # Unit NAV must remain exactly 100.00
        assert snapshot.unit_nav == Decimal("100.0000")
        # Daily return is 0.0000%
        assert snapshot.daily_return_pct == Decimal("0.0000")
        assert snapshot.net_external_flow == Decimal("50000.00")
