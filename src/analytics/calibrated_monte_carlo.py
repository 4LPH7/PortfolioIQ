"""
PortfolioIQ — Calibrated Fat-Tailed Monte Carlo Simulation
Phase 5: Make the Signal Engine Evidence-Based

Replaces Gaussian geometric Brownian motion with a 3-parameter Student's t distribution
fitted via maximum likelihood. Applies empirical coverage calibration (conformal scaling)
to ensure 80% confidence cones match historical realized frequencies. Strictly bans point
estimates ("Target Price") in favor of probabilistic dispersion percentiles (P10..P90).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from loguru import logger
from scipy import stats

from src.models.dtos import CalibratedMonteCarloDTO


def fit_student_t_parameters(log_returns: np.ndarray) -> tuple[float, float, float]:
    """
    Fits degrees of freedom (nu), location (mu), and scale (s) to daily log returns
    using maximum likelihood estimation.

    Parameters:
        log_returns: 1D array of historical daily log returns.

    Returns:
        (degrees_of_freedom, location, scale)

    Safeguards:
        - Drops non-finite values (NaN, Inf).
        - Requires at least 15 valid observations; falls back to default (5.0, 0.0, 0.015).
        - Clamps degrees of freedom to [2.5, 30.0] for tail risk realism and numerical stability.
        - Enforces minimum scale of 1e-4 to prevent zero division on flat price series.
    """
    if log_returns is None or len(log_returns) == 0:
        return 5.0, 0.0, 0.015

    valid = np.asarray(log_returns, dtype=float)
    valid = valid[np.isfinite(valid)]

    if len(valid) < 15:
        logger.warning(
            "Insufficient return observations ({}) for Student-t fit; using default priors",
            len(valid),
        )
        return 5.0, float(np.mean(valid)) if len(valid) > 0 else 0.0, 0.015

    try:
        df_fit, loc_fit, scale_fit = stats.t.fit(valid)
        df_clamped = float(np.clip(df_fit, 2.5, 30.0))
        scale_safe = max(float(scale_fit), 1e-4)
        return df_clamped, float(loc_fit), scale_safe
    except Exception as exc:
        logger.warning("Student's t MLE fitting failed: {}; using robust fallback", exc)
        sample_std = float(np.std(valid)) if len(valid) > 1 else 0.015
        return 5.0, float(np.mean(valid)), max(sample_std, 1e-4)


def calibrate_empirical_coverage(
    log_returns: np.ndarray,
    df: float,
    loc: float,
    scale: float,
    target_coverage: float = 0.80,
) -> tuple[float, float, float]:
    """
    Calibrates prediction cone scale multiplier against historical empirical coverage.

    For a target coverage 1 - alpha (default: 80% central interval [P10, P90]):
    1. Computes theoretical upper quantile: q_theo = t_nu^-1((1 + target) / 2).
    2. Computes empirical absolute standardized residuals: e_t = |r_t - loc| / scale.
    3. Extracts empirical target percentile: q_emp = Percentile_{100 * target}(e_t).
    4. Calculates scale multiplier: c = max(1.0, q_emp / q_theo).
    5. Returns (calibrated_scale, scale_multiplier, empirical_coverage_pct).
    """
    valid = np.asarray(log_returns, dtype=float)
    valid = valid[np.isfinite(valid)]

    if len(valid) < 15 or scale <= 0:
        return scale, 1.0, target_coverage * 100.0

    # Upper tail probability for central interval (e.g. 0.80 target -> 0.90 cumulative)
    p_upper = 1.0 - (1.0 - target_coverage) / 2.0
    theo_q = float(stats.t.ppf(p_upper, df=df))
    if theo_q <= 0:
        theo_q = 1.0

    std_residuals = np.abs((valid - loc) / scale)
    emp_q = float(np.percentile(std_residuals, target_coverage * 100.0))

    scale_multiplier = max(1.0, emp_q / theo_q)
    calibrated_scale = scale * scale_multiplier

    realized_coverage = float(np.mean(std_residuals <= theo_q * scale_multiplier) * 100.0)

    return calibrated_scale, scale_multiplier, realized_coverage


def run_calibrated_monte_carlo(
    df: pd.DataFrame,
    horizon_days: int = 30,
    simulations: int = 1000,
    seed: int = 42,
    as_of_date: datetime | None = None,
) -> CalibratedMonteCarloDTO:
    """
    Runs calibrated fat-tailed Student's t Monte Carlo simulation on daily closing prices.

    Guarantees:
    - Replaces Gaussian random walks with Student's t innovations.
    - Calibrates empirical coverage to verify that tail risk is fully captured.
    - Strictly returns dispersion percentiles (P10, P25, P50, P75, P90) and fan charts.
    - Never emits a single misleading "Target Price" point forecast.

    Parameters:
        df: DataFrame containing at least 'Close' column.
        horizon_days: Simulation horizon in trading days (default: 30).
        simulations: Number of Monte Carlo path trajectories (default: 1000).
        seed: Random seed for deterministic simulation replay (default: 42).
        as_of_date: Optional base date for future fan chart dates (defaults to today).

    Returns:
        CalibratedMonteCarloDTO with percentiles and fan chart series.
    """
    if df.empty or "Close" not in df.columns:
        raise ValueError("DataFrame is empty or missing 'Close' column")

    close = df["Close"].dropna().astype(float)
    if len(close) < 2:
        raise ValueError("Insufficient closing price observations (need at least 2)")

    current_price = float(close.iloc[-1])
    if current_price <= 0:
        raise ValueError(f"Current price must be positive, got {current_price}")

    # Compute daily log returns
    log_returns = np.log(close / close.shift(1)).dropna().values

    # Fit Student's t parameters
    df_fit, loc_fit, scale_fit = fit_student_t_parameters(log_returns)

    # Calibrate scale for empirical 80% coverage
    calibrated_scale, scale_multiplier, empirical_coverage_80 = calibrate_empirical_coverage(
        log_returns=log_returns,
        df=df_fit,
        loc=loc_fit,
        scale=scale_fit,
        target_coverage=0.80,
    )

    # Generate fat-tailed random shocks across all paths: shape (simulations, horizon_days)
    shocks = stats.t.rvs(
        df=df_fit,
        loc=loc_fit,
        scale=calibrated_scale,
        size=(simulations, horizon_days),
        random_state=seed,
    )

    # Cumulative asset prices along simulated trajectories
    cum_returns = np.cumsum(shocks, axis=1)
    paths = current_price * np.exp(cum_returns)

    # Final price distribution at end of horizon
    final_prices = paths[:, -1]
    p10 = float(np.percentile(final_prices, 10))
    p25 = float(np.percentile(final_prices, 25))
    p50 = float(np.percentile(final_prices, 50))
    p75 = float(np.percentile(final_prices, 75))
    p90 = float(np.percentile(final_prices, 90))

    prob_profit = float(np.mean(final_prices > current_price) * 100.0)

    # Fan chart daily trajectories
    base_date = as_of_date or datetime.now()
    fan_dates = [
        (base_date + timedelta(days=i + 1)).strftime("%Y-%m-%d") for i in range(horizon_days)
    ]
    fan_p10 = [round(float(np.percentile(paths[:, i], 10)), 2) for i in range(horizon_days)]
    fan_p50 = [round(float(np.percentile(paths[:, i], 50)), 2) for i in range(horizon_days)]
    fan_p90 = [round(float(np.percentile(paths[:, i], 90)), 2) for i in range(horizon_days)]

    return CalibratedMonteCarloDTO(
        horizon_days=horizon_days,
        current_price=round(current_price, 2),
        degrees_of_freedom=round(df_fit, 2),
        scale_multiplier=round(scale_multiplier, 3),
        empirical_coverage_80=round(empirical_coverage_80, 1),
        p10=round(p10, 2),
        p25=round(p25, 2),
        p50=round(p50, 2),
        p75=round(p75, 2),
        p90=round(p90, 2),
        prob_profit=round(prob_profit, 1),
        fan_dates=fan_dates,
        fan_p10=fan_p10,
        fan_p50=fan_p50,
        fan_p90=fan_p90,
    )
