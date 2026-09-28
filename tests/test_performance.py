"""
Tests for src/analytics/performance.py.
Verifies GIPS TWR unitization, XIRR numerical root-finding, Sharpe/Sortino ratios,
drawdowns, Beta/Alpha regression, and fee drag attribution.
"""

from datetime import date, timedelta
from decimal import Decimal

import numpy as np

from src.analytics.performance import (
    calculate_beta_alpha,
    calculate_drawdown_series,
    calculate_sharpe_sortino,
    calculate_unitized_twr,
    calculate_xirr,
    compute_portfolio_performance_summary,
)
from src.models.dtos import PortfolioCashFlowDTO, PortfolioDailySnapshotDTO


def test_twr_unitization_isolated_from_cash_flows():
    """Verify that external cash flows do not distort Time-Weighted Return (TWR)."""
    d0 = date(2026, 1, 1)
    d1 = date(2026, 1, 2)
    d2 = date(2026, 1, 3)

    # Day 0: Initial portfolio: 100,000 NAV, 1000 units, 100.00 unit NAV
    s0 = PortfolioDailySnapshotDTO(
        snapshot_date=d0,
        total_equity_value=Decimal("100000.00"),
        cash_balance=Decimal("0.00"),
        total_nav=Decimal("100000.00"),
        units=Decimal("1000.000000"),
        unit_nav=Decimal("100.0000"),
        daily_return_pct=Decimal("0.0000"),
    )

    # Day 1: Portfolio rises by 10% to 110,000. Unit NAV rises to 110.00.
    s1 = PortfolioDailySnapshotDTO(
        snapshot_date=d1,
        total_equity_value=Decimal("110000.00"),
        cash_balance=Decimal("0.00"),
        total_nav=Decimal("110000.00"),
        units=Decimal("1000.000000"),
        unit_nav=Decimal("110.0000"),
        daily_return_pct=Decimal("0.1000"),
    )

    # Day 2: Huge external deposit of 110,000 added. Total NAV doubles to 220,000.
    # At unit NAV 110.00, 1000 new units issued -> 2000 total units. Unit NAV stays 110.00.
    s2 = PortfolioDailySnapshotDTO(
        snapshot_date=d2,
        total_equity_value=Decimal("110000.00"),
        cash_balance=Decimal("110000.00"),
        total_nav=Decimal("220000.00"),
        units=Decimal("2000.000000"),
        unit_nav=Decimal("110.0000"),
        daily_return_pct=Decimal("0.0000"),
        net_external_flow=Decimal("110000.00"),
    )

    twr_res = calculate_unitized_twr([s0, s1, s2])
    # TWR must be exactly 10.0%, NOT 120% despite total NAV doubling!
    assert twr_res["twr_pct"] == 10.0
    assert twr_res["start_nav"] == 100.0
    assert twr_res["end_nav"] == 110.0


def test_xirr_numerical_solver_accuracy():
    """Verify XIRR calculation matches standard compound interest benchmark."""
    d0 = date(2025, 1, 1)
    d1 = date(2026, 1, 1)

    # 1-year deposit of 100,000 that grows to 112,000 -> 12.0% annual return
    flows = [(d0, -100000.0), (d1, 112000.0)]
    xirr = calculate_xirr(flows)
    assert xirr is not None
    assert abs(xirr - 12.0) < 0.2

    # Multi-period irregular cash flows
    d2 = date(2026, 7, 1)
    flows_multi = [
        (date(2025, 1, 1), -100000.0),
        (date(2025, 7, 1), -50000.0),
        (date(2026, 1, 1), 20000.0),
        (d2, 160000.0),
    ]
    xirr_multi = calculate_xirr(flows_multi)
    assert xirr_multi is not None
    assert xirr_multi > 0.0


def test_xirr_edge_cases():
    """Verify XIRR handles edge cases and invalid inputs gracefully."""
    # Fewer than 2 flows
    assert calculate_xirr([]) is None
    assert calculate_xirr([(date(2026, 1, 1), -100.0)]) is None

    # All negative (only deposits, no valuation/outflow)
    assert calculate_xirr([(date(2026, 1, 1), -100.0), (date(2026, 2, 1), -200.0)]) is None

    # All positive (only withdrawals, no cost basis)
    assert calculate_xirr([(date(2026, 1, 1), 100.0), (date(2026, 2, 1), 200.0)]) is None


def test_sharpe_and_sortino_calculation_annualized():
    """Verify Sharpe and Sortino ratios computed with Indian sqrt(252) scaling."""
    # Series with positive mean return (+0.24% daily) and negative days (-0.3% daily):
    daily_returns = np.array(
        [0.0050 if i % 3 != 0 else -0.0030 for i in range(60)], dtype=np.float64
    )

    sharpe, sortino = calculate_sharpe_sortino(daily_returns, rf_annual=0.065, min_warmup_days=30)
    assert sharpe is not None
    assert sortino is not None
    # Positive drift above Rf should yield positive Sharpe & Sortino
    assert sharpe > 0.0
    assert sortino > 0.0


def test_warmup_period_gate():
    """Verify that fewer than 30 trading days suppress annualized volatility ratios."""
    daily_returns = np.array([0.01, -0.005, 0.008, 0.002, -0.001])  # 5 days
    sharpe, sortino = calculate_sharpe_sortino(daily_returns, rf_annual=0.065, min_warmup_days=30)
    assert sharpe is None
    assert sortino is None

    bm_returns = np.array([0.008, -0.003, 0.005, 0.001, -0.002])
    beta_alpha = calculate_beta_alpha(
        daily_returns, bm_returns, rf_annual=0.065, min_warmup_days=30
    )
    assert beta_alpha["beta"] is None
    assert beta_alpha["alpha_annual_pct"] is None


def test_max_drawdown_and_hwm_tracking():
    """Verify peak-to-trough drawdown calculation and recovery date identification."""
    dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(5)]
    # NAV: 100 -> 120 (peak) -> 90 (-25% DD from 120) -> 100 -> 125 (recovered)
    navs = np.array([100.0, 120.0, 90.0, 100.0, 125.0], dtype=np.float64)

    metrics = calculate_drawdown_series(dates, navs)
    assert metrics.max_drawdown_pct == -25.0
    assert metrics.high_water_mark == 125.0
    assert metrics.current_drawdown_pct == 0.0
    assert metrics.peak_date == dates[1]  # 120.0 peak
    assert metrics.trough_date == dates[2]  # 90.0 trough
    assert metrics.recovery_date == dates[4]  # 125.0 recovery
    assert metrics.is_recovered is True


def test_beta_and_jensens_alpha_vs_nifty():
    """Verify Beta and Alpha calculation against benchmark."""
    # 40 days of benchmark returns
    np.random.seed(123)
    bm_returns = np.random.normal(loc=0.0005, scale=0.01, size=40)
    # Portfolio has beta of ~1.5 with slight positive alpha
    p_returns = 1.5 * bm_returns + 0.0002

    res = calculate_beta_alpha(p_returns, bm_returns, rf_annual=0.065, min_warmup_days=30)
    assert res["beta"] is not None
    assert abs(res["beta"] - 1.5) < 0.1
    assert res["r_squared"] is not None
    assert res["r_squared"] > 0.90


def test_compute_portfolio_performance_summary_drag_attribution():
    """Verify full performance summary aggregation and basis-point drag attribution."""
    snapshots = []
    base_date = date(2026, 1, 1)
    for i in range(35):
        d = base_date + timedelta(days=i)
        # Unit NAV gradually climbs from 100 to 105
        unit_nav = Decimal(str(100.0 + i * (5.0 / 34)))
        daily_return = Decimal("0.0020") if i % 2 == 0 else Decimal("0.0010")
        snapshots.append(
            PortfolioDailySnapshotDTO(
                snapshot_date=d,
                total_equity_value=Decimal("100000.00"),
                cash_balance=Decimal("5000.00"),
                total_nav=Decimal("105000.00"),
                units=Decimal("1000.000000"),
                unit_nav=unit_nav,
                daily_return_pct=daily_return,
                benchmark_name="NIFTY 50 TRI",
                benchmark_value=Decimal("25000.00"),
                benchmark_daily_return_pct=Decimal("0.0010"),
                stt_drag_bps=Decimal("1.00"),  # 1 bps per day
                fee_drag_bps=Decimal("0.50"),  # 0.5 bps per day
                tax_drag_bps=Decimal("0.00"),
            )
        )

    cash_flows = [
        PortfolioCashFlowDTO(
            flow_date=base_date,
            flow_type="DEPOSIT",
            amount=Decimal("100000.00"),
        )
    ]

    summary = compute_portfolio_performance_summary(snapshots, cash_flows, rf_annual=0.065)
    assert summary.is_warmup_period is False
    assert summary.history_days == 34
    assert summary.twr_pct == 5.0
    assert summary.sharpe_ratio is not None
    assert summary.stt_drag_bps == 35.0
    assert summary.fee_drag_bps == 17.5
    # Total drag = 52.5 bps -> 0.525%
    # Net TWR = 5.0 - 0.53 = 4.47%
    assert summary.net_realized_return_pct < summary.gross_return_pct
    assert abs(summary.gross_return_pct - summary.net_realized_return_pct - 0.53) < 0.05
