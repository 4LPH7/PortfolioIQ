"""
PortfolioIQ — Quantitative Performance & Risk Attribution Engine
Computes Time-Weighted Return (TWR), Money-Weighted Return (XIRR),
Sharpe Ratio, Sortino Ratio, Drawdown series, Beta, and Jensen's Alpha.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import numpy as np
from scipy import optimize

from src.models.dtos import PerformanceMetricsDTO, PortfolioCashFlowDTO, PortfolioDailySnapshotDTO


@dataclass
class DrawdownMetrics:
    max_drawdown_pct: float
    current_drawdown_pct: float
    high_water_mark: float
    peak_date: date | None
    trough_date: date | None
    recovery_date: date | None
    is_recovered: bool


def calculate_unitized_twr(snapshots: list[PortfolioDailySnapshotDTO]) -> dict[str, Any]:
    """
    Computes Time-Weighted Return (TWR) and annualized CAGR from daily unit NAV progression.
    Under GIPS standards, external cash flows do not distort TWR.
    """
    if not snapshots:
        return {
            "twr_pct": 0.0,
            "cagr_pct": None,
            "history_days": 0,
            "start_nav": 100.0,
            "end_nav": 100.0,
        }

    start_nav = float(snapshots[0].unit_nav)
    end_nav = float(snapshots[-1].unit_nav)

    if start_nav <= 0:
        return {"twr_pct": 0.0, "cagr_pct": None, "history_days": 0}

    twr = (end_nav / start_nav) - 1.0
    history_days = (snapshots[-1].snapshot_date - snapshots[0].snapshot_date).days

    cagr: float | None = None
    if history_days >= 30:
        # Annualized geometric return
        years = history_days / 365.25
        if years > 0 and (1.0 + twr) > 0:
            cagr = float((1.0 + twr) ** (1.0 / years) - 1.0) * 100.0

    return {
        "twr_pct": round(twr * 100.0, 2),
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "history_days": history_days,
        "start_nav": round(start_nav, 4),
        "end_nav": round(end_nav, 4),
    }


def calculate_xirr(cash_flows: list[tuple[date, float]], guess: float = 0.10) -> float | None:
    """
    Compute Money-Weighted Return (XIRR) using Newton-Raphson with Brentq fallback.
    cash_flows format: [(date_0, -amount), (date_1, amount), ...]
    Deposits are negative outflows, withdrawals and terminal portfolio value are positive inflows.
    """
    if len(cash_flows) < 2:
        return None

    dates, amounts = zip(*cash_flows, strict=False)
    d0 = dates[0]
    years = np.array([(d - d0).days / 365.25 for d in dates], dtype=np.float64)
    amounts_arr = np.array(amounts, dtype=np.float64)

    # Must have at least one positive and one negative cash flow
    if np.all(amounts_arr <= 0) or np.all(amounts_arr >= 0):
        return None

    def npv(r: float) -> float:
        if r <= -1.0:
            return float("inf")
        return float(np.sum(amounts_arr / ((1.0 + r) ** years)))

    def npv_prime(r: float) -> float:
        if r <= -1.0:
            return float("-inf")
        return float(np.sum(-years * amounts_arr / ((1.0 + r) ** (years + 1.0))))

    # 1. Primary: Newton-Raphson for rapid quadratic convergence
    try:
        r_opt = optimize.newton(npv, guess, fprime=npv_prime, maxiter=100, tol=1e-6)
        if -0.999 < r_opt < 50.0:
            return round(float(r_opt) * 100.0, 2)
    except Exception:
        pass

    # 2. Secondary fallback: Brent's bounded search on [-0.999, 10.0]
    try:
        r_opt = optimize.brentq(npv, -0.999, 10.0, maxiter=100)
        return round(float(r_opt) * 100.0, 2)
    except Exception:
        pass

    # 3. Pure-Python bisection fallback
    low, high = -0.999, 10.0
    val_low = npv(low)
    for _ in range(100):
        mid = (low + high) / 2.0
        val_mid = npv(mid)
        if abs(val_mid) < 1e-5:
            return round(float(mid) * 100.0, 2)
        if val_low * val_mid < 0:
            high = mid
        else:
            low = mid
            val_low = val_mid

    return None


def calculate_sharpe_sortino(
    daily_returns: np.ndarray,
    rf_annual: float = 0.065,
    min_warmup_days: int = 30,
) -> tuple[float | None, float | None]:
    """
    Computes annualized Sharpe Ratio and Sortino Ratio using Indian market conventions.
    Returns (None, None) if sample size < min_warmup_days (30-day warmup gate).
    """
    n = len(daily_returns)
    if n < min_warmup_days:
        return None, None

    rf_daily = (1.0 + rf_annual) ** (1.0 / 252.0) - 1.0
    excess_returns = daily_returns - rf_daily
    mean_excess = np.mean(excess_returns)

    # Sharpe Ratio: Total portfolio return standard deviation
    std_dev = np.std(daily_returns, ddof=1)
    sharpe = float((mean_excess / std_dev) * np.sqrt(252)) if std_dev > 1e-8 else None

    # Sortino Ratio: Downside semi-deviation below Rf
    downside_deviations = np.minimum(0.0, excess_returns)
    downside_variance = np.mean(downside_deviations**2)
    downside_dev = np.sqrt(downside_variance)
    sortino = float((mean_excess / downside_dev) * np.sqrt(252)) if downside_dev > 1e-8 else None

    return (
        round(sharpe, 2) if sharpe is not None else None,
        round(sortino, 2) if sortino is not None else None,
    )


def calculate_drawdown_series(dates: list[date], unit_navs: np.ndarray) -> DrawdownMetrics:
    """
    Computes maximum drawdown percentage, current drawdown percentage,
    high-water mark (HWM), and peak/trough/recovery dates.
    """
    if len(unit_navs) == 0:
        return DrawdownMetrics(
            max_drawdown_pct=0.0,
            current_drawdown_pct=0.0,
            high_water_mark=100.0,
            peak_date=None,
            trough_date=None,
            recovery_date=None,
            is_recovered=True,
        )

    hwm_series = np.maximum.accumulate(unit_navs)
    drawdowns = (unit_navs - hwm_series) / hwm_series

    trough_idx = int(np.argmin(drawdowns))
    max_dd = float(drawdowns[trough_idx])

    # Find the peak date preceding the maximum drawdown trough
    peak_idx = int(np.argmax(unit_navs[: trough_idx + 1]))

    # Find recovery date after trough (if recovered)
    recovery_idx = None
    peak_val = unit_navs[peak_idx]
    for i in range(trough_idx + 1, len(unit_navs)):
        if unit_navs[i] >= peak_val:
            recovery_idx = i
            break

    return DrawdownMetrics(
        max_drawdown_pct=round(max_dd * 100.0, 2),
        current_drawdown_pct=round(float(drawdowns[-1]) * 100.0, 2),
        high_water_mark=round(float(hwm_series[-1]), 4),
        peak_date=dates[peak_idx] if dates else None,
        trough_date=dates[trough_idx] if dates else None,
        recovery_date=dates[recovery_idx] if recovery_idx is not None else None,
        is_recovered=(recovery_idx is not None or trough_idx == len(unit_navs) - 1 and max_dd == 0),
    )


def calculate_beta_alpha(
    portfolio_returns: np.ndarray,
    benchmark_returns: np.ndarray,
    rf_annual: float = 0.065,
    min_warmup_days: int = 30,
) -> dict[str, float | None]:
    """
    Computes Beta, Jensen's Alpha, R-Squared, and Tracking Error relative to benchmark.
    Returns None for ratios if sample size < min_warmup_days.
    """
    if len(portfolio_returns) < min_warmup_days or len(benchmark_returns) < min_warmup_days:
        return {
            "beta": None,
            "alpha_annual_pct": None,
            "r_squared": None,
            "tracking_error_pct": None,
        }

    rf_daily = (1.0 + rf_annual) ** (1.0 / 252.0) - 1.0
    cov_matrix = np.cov(portfolio_returns, benchmark_returns)
    cov_pb = cov_matrix[0, 1]
    var_b = cov_matrix[1, 1]

    if var_b < 1e-9:
        return {
            "beta": None,
            "alpha_annual_pct": None,
            "r_squared": None,
            "tracking_error_pct": None,
        }

    beta = cov_pb / var_b
    mean_p = np.mean(portfolio_returns)
    mean_b = np.mean(benchmark_returns)

    # Jensen's Alpha (annualized excess return over CAPM expected return)
    alpha_daily = mean_p - (rf_daily + beta * (mean_b - rf_daily))
    alpha_annual = (1.0 + alpha_daily) ** 252 - 1.0

    # Correlation & R²
    std_p = np.std(portfolio_returns, ddof=1)
    std_b = np.std(benchmark_returns, ddof=1)
    corr = cov_pb / (std_p * std_b) if (std_p * std_b) > 1e-9 else 0.0
    r_squared = corr**2

    # Tracking Error
    excess_vs_bm = portfolio_returns - benchmark_returns
    tracking_error = np.std(excess_vs_bm, ddof=1) * np.sqrt(252)

    return {
        "beta": round(float(beta), 3),
        "alpha_annual_pct": round(float(alpha_annual * 100.0), 2),
        "r_squared": round(float(r_squared), 4),
        "tracking_error_pct": round(float(tracking_error * 100.0), 2),
    }


def compute_portfolio_performance_summary(
    snapshots: list[PortfolioDailySnapshotDTO],
    cash_flows: list[PortfolioCashFlowDTO],
    rf_annual: float = 0.065,
    user_id: str = "default",
) -> PerformanceMetricsDTO:
    """
    Integrates all portfolio analytics into a consolidated PerformanceMetricsDTO:
    TWR, CAGR, XIRR, Sharpe, Sortino, Drawdown, Beta/Alpha, and fee drag attribution.
    """
    if not snapshots:
        return PerformanceMetricsDTO(
            user_id=user_id,
            twr_pct=0.0,
            cagr_pct=None,
            xirr_pct=None,
            sharpe_ratio=None,
            sortino_ratio=None,
            max_drawdown_pct=0.0,
            current_drawdown_pct=0.0,
            high_water_mark=100.0,
            beta=None,
            alpha_annual_pct=None,
            r_squared=None,
            tracking_error_pct=None,
            history_days=0,
            is_warmup_period=True,
            gross_return_pct=0.0,
            stt_drag_bps=0.0,
            fee_drag_bps=0.0,
            tax_drag_bps=0.0,
            net_realized_return_pct=0.0,
        )

    # 1. TWR and CAGR
    twr_res = calculate_unitized_twr(snapshots)
    history_days = twr_res["history_days"]
    is_warmup = history_days < 30

    # 2. XIRR Cash Flows Construction
    # Deposits = negative outflows; Withdrawals = positive inflows; Terminal NAV = positive inflow
    xirr_flows: list[tuple[date, float]] = []
    for flow in cash_flows:
        amount = float(flow.amount)
        if flow.flow_type in ("DEPOSIT", "INTEREST"):
            xirr_flows.append((flow.flow_date, -amount))
        elif flow.flow_type in ("WITHDRAWAL", "DIVIDEND"):
            xirr_flows.append((flow.flow_date, amount))

    # Add terminal portfolio value on last snapshot date
    terminal_nav = float(snapshots[-1].total_nav)
    last_date = snapshots[-1].snapshot_date
    if terminal_nav > 0:
        xirr_flows.append((last_date, terminal_nav))

    xirr_pct = calculate_xirr(xirr_flows) if len(xirr_flows) >= 2 else None

    # 3. Daily returns arrays for risk metrics
    dates = [s.snapshot_date for s in snapshots]
    unit_navs = np.array([float(s.unit_nav) for s in snapshots], dtype=np.float64)

    daily_returns_list = [
        float(s.daily_return_pct) for s in snapshots[1:] if s.daily_return_pct is not None
    ]
    daily_returns = np.array(daily_returns_list, dtype=np.float64)

    bm_returns_list = [
        float(s.benchmark_daily_return_pct)
        for s in snapshots[1:]
        if s.benchmark_daily_return_pct is not None
    ]
    bm_returns = np.array(bm_returns_list, dtype=np.float64)

    # 4. Sharpe & Sortino
    sharpe, sortino = calculate_sharpe_sortino(
        daily_returns, rf_annual=rf_annual, min_warmup_days=30
    )

    # 5. Drawdown & HWM
    dd_metrics = calculate_drawdown_series(dates, unit_navs)

    # 6. Beta & Alpha
    min_len = min(len(daily_returns), len(bm_returns))
    if min_len >= 30:
        beta_alpha = calculate_beta_alpha(
            daily_returns[:min_len], bm_returns[:min_len], rf_annual=rf_annual, min_warmup_days=30
        )
    else:
        beta_alpha = {
            "beta": None,
            "alpha_annual_pct": None,
            "r_squared": None,
            "tracking_error_pct": None,
        }

    # 7. Drag attribution
    total_stt_bps = sum(float(s.stt_drag_bps or Decimal("0.0")) for s in snapshots)
    total_fee_bps = sum(float(s.fee_drag_bps or Decimal("0.0")) for s in snapshots)
    total_tax_bps = sum(float(s.tax_drag_bps or Decimal("0.0")) for s in snapshots)
    total_drag_bps = total_stt_bps + total_fee_bps + total_tax_bps

    gross_twr = twr_res["twr_pct"]
    net_twr = round(gross_twr - (total_drag_bps / 100.0), 2)

    return PerformanceMetricsDTO(
        user_id=user_id,
        twr_pct=twr_res["twr_pct"],
        cagr_pct=twr_res["cagr_pct"],
        xirr_pct=xirr_pct,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown_pct=dd_metrics.max_drawdown_pct,
        current_drawdown_pct=dd_metrics.current_drawdown_pct,
        high_water_mark=dd_metrics.high_water_mark,
        beta=beta_alpha["beta"],
        alpha_annual_pct=beta_alpha["alpha_annual_pct"],
        r_squared=beta_alpha["r_squared"],
        tracking_error_pct=beta_alpha["tracking_error_pct"],
        history_days=history_days,
        is_warmup_period=is_warmup,
        gross_return_pct=gross_twr,
        stt_drag_bps=round(total_stt_bps, 2),
        fee_drag_bps=round(total_fee_bps, 2),
        tax_drag_bps=round(total_tax_bps, 2),
        net_realized_return_pct=net_twr,
    )
