"""
Risk statistics after QuantConnect Lean's PortfolioStatistics: PSR, VaR/CVaR, information ratio.
Each returns a value only when the estimate can be trusted, otherwise an explicit status.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from src.analytics.performance import (
    calculate_information_ratio,
    calculate_probabilistic_sharpe,
    calculate_var_cvar,
)

RF = 0.065
RF_DAILY = (1 + RF) ** (1 / 252) - 1


def _alternating(n, centre, swing):
    return centre + swing * np.array([1.0 if i % 2 == 0 else -1.0 for i in range(n)])


def test_var_and_cvar_on_a_hand_computed_grid():
    r = np.linspace(-0.05, 0.05, 101)  # step 0.001
    var95, cvar95 = calculate_var_cvar(r, 0.95)
    var99, cvar99 = calculate_var_cvar(r, 0.99)
    assert (var95.value, cvar95.value) == (4.5, 4.75)  # tail −5.0..−4.5%: mean −4.75%
    assert (var99.value, cvar99.value) == (4.9, 4.95)
    assert var95.status == "ok" and var95.n_obs == 101


def test_var_99_needs_a_hundred_observations():
    r = np.linspace(-0.05, 0.05, 60)
    assert calculate_var_cvar(r, 0.95)[0].status == "ok"
    var99, cvar99 = calculate_var_cvar(r, 0.99)
    assert var99.status == cvar99.status == "insufficient_data" and var99.value is None


def test_information_ratio_closed_form():
    bench = np.zeros(40)
    port = _alternating(40, 0.001, 0.002)  # active mean 0.1%, sd 0.2%·√(40/39)
    ir = calculate_information_ratio(port, bench)
    expected = 0.001 / (0.002 * np.sqrt(40 / 39)) * np.sqrt(252)
    assert ir.status == "ok" and ir.value == round(expected, 3)


def test_psr_is_one_half_when_returns_centre_on_the_risk_free_rate():
    psr = calculate_probabilistic_sharpe(_alternating(60, RF_DAILY, 0.01), rf_annual=RF)
    assert psr.status == "ok" and psr.value == 0.5


def test_psr_rises_with_the_edge():
    low = calculate_probabilistic_sharpe(_alternating(60, RF_DAILY + 0.0005, 0.01), RF).value
    high = calculate_probabilistic_sharpe(_alternating(60, RF_DAILY + 0.003, 0.01), RF).value
    assert 0.5 < low < high <= 1.0


def test_psr_reports_unstable_instead_of_clamping():
    r = _alternating(60, RF_DAILY + 0.003, 0.01)
    # Population moments can't make the variance term negative (kurtosis ≥ skew² + 1); sampling
    # error in a small sample can. Force that case.
    with patch("scipy.stats.kurtosis", return_value=-50.0):
        psr = calculate_probabilistic_sharpe(r, RF)
    assert psr.status == "unstable" and psr.value is None


@pytest.mark.parametrize(
    "returns, status",
    [
        (np.full(10, 0.001), "insufficient_data"),
        (np.append(np.linspace(-0.01, 0.01, 40), np.nan), "non_finite"),
        (np.full(40, 0.001), "zero_variance"),
    ],
)
def test_degenerate_inputs_return_a_status_not_a_number(returns, status):
    for metric in (
        calculate_probabilistic_sharpe(returns, RF),
        calculate_var_cvar(returns, 0.95)[0],
        calculate_information_ratio(returns, np.zeros(len(returns))),
    ):
        assert metric.status == status and metric.value is None


def test_summary_reports_the_benchmark_over_the_same_days():
    from datetime import date, timedelta
    from decimal import Decimal

    from src.analytics.performance import compute_portfolio_performance_summary
    from src.models.dtos import PortfolioDailySnapshotDTO

    start = date(2026, 1, 1)
    snaps = [
        PortfolioDailySnapshotDTO(
            snapshot_date=start + timedelta(days=i),
            user_id="default",
            total_nav=Decimal("100000"),
            total_equity_value=Decimal("100000"),
            cash_balance=Decimal("0"),
            units=Decimal("1000"),
            unit_nav=Decimal("100"),
            daily_return_pct=Decimal("0"),
            benchmark_daily_return_pct=Decimal("0.0100") if i else None,
        )
        for i in range(3)
    ]
    summary = compute_portfolio_performance_summary(snaps, [])
    assert summary.benchmark_twr_pct == round((1.01**2 - 1) * 100, 2)  # two 1% days: 2.01%
