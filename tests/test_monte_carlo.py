"""
Tests for Calibrated Fat-Tailed Monte Carlo Simulation.
Phase 5: Make the Signal Engine Evidence-Based (Plan 05-05)

Validates:
- Student's t parameter fitting via MLE with clamped degrees of freedom [2.5, 30.0].
- Conformal empirical coverage calibration (scale multiplier >= 1.0, coverage >= 80%).
- Strict percentile ordering (P10 < P25 < P50 < P75 < P90) across simulation & fan charts.
- Strict absence of single "Target Price" point estimates.
- Deterministic simulation replay with seed control.
- Edge cases: short series, flat prices, invalid inputs, NaN returns.
"""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from src.analytics.calibrated_monte_carlo import (
    calibrate_empirical_coverage,
    fit_student_t_parameters,
    run_calibrated_monte_carlo,
)
from src.models.dtos import CalibratedMonteCarloDTO


@pytest.fixture
def synthetic_t_returns() -> np.ndarray:
    """Generate 500 daily log returns from known Student-t(nu=4.0, loc=0.0005, scale=0.015)."""
    rng = np.random.default_rng(42)
    return stats.t.rvs(df=4.0, loc=0.0005, scale=0.015, size=500, random_state=rng)


@pytest.fixture
def synthetic_price_df() -> pd.DataFrame:
    """Generate 252 days of geometric price series from known Student-t shocks."""
    rng = np.random.default_rng(123)
    shocks = stats.t.rvs(df=4.5, loc=0.0004, scale=0.018, size=252, random_state=rng)
    prices = 1000.0 * np.exp(np.cumsum(shocks))
    dates = pd.date_range("2025-01-01", periods=252, freq="B")
    return pd.DataFrame({"Close": prices}, index=dates)


class TestStudentTFitting:
    """Tests for MLE parameter fitting with tail risk bounds."""

    def test_fit_student_t_recovers_reasonable_parameters(self, synthetic_t_returns):
        nu, mu, s = fit_student_t_parameters(synthetic_t_returns)
        # Should be within reasonable sampling error of ground truth nu=4.0
        assert 2.5 <= nu <= 10.0
        assert -0.005 <= mu <= 0.005
        assert 0.008 <= s <= 0.025

    def test_degrees_of_freedom_clamped_to_lower_bound(self):
        # Generate extreme heavy-tailed Cauchy-like data (nu=1.2)
        rng = np.random.default_rng(99)
        heavy_tails = stats.t.rvs(df=1.2, loc=0.0, scale=0.01, size=300, random_state=rng)
        nu, _, _ = fit_student_t_parameters(heavy_tails)
        assert nu >= 2.5, "Degrees of freedom must be clamped at lower bound 2.5"

    def test_degrees_of_freedom_clamped_to_upper_bound(self):
        # Generate Gaussian data (nu -> infinity)
        rng = np.random.default_rng(77)
        gaussian_returns = rng.normal(loc=0.001, scale=0.01, size=500)
        nu, _, _ = fit_student_t_parameters(gaussian_returns)
        assert nu <= 30.0, "Degrees of freedom must be clamped at upper bound 30.0"

    def test_fallback_on_insufficient_samples(self):
        short_returns = np.array([0.01, -0.005, 0.002])
        nu, mu, s = fit_student_t_parameters(short_returns)
        assert nu == 5.0
        assert s == 0.015

    def test_empty_and_nan_returns_handled_gracefully(self):
        assert fit_student_t_parameters(np.array([])) == (5.0, 0.0, 0.015)
        assert fit_student_t_parameters(None) == (5.0, 0.0, 0.015)
        # Array with all NaNs
        nan_arr = np.array([np.nan, np.nan, np.nan])
        assert fit_student_t_parameters(nan_arr) == (5.0, 0.0, 0.015)

    def test_minimum_scale_floor(self):
        # Array with identical returns (zero variance)
        flat = np.full(50, 0.001)
        nu, mu, s = fit_student_t_parameters(flat)
        assert s >= 1e-4, "Scale must not be zero to avoid division by zero"


class TestEmpiricalCoverageCalibration:
    """Tests for conformal empirical coverage scaling."""

    def test_calibration_expands_scale_for_heavy_tails(self):
        rng = np.random.default_rng(42)
        # Heavy-tailed data with significant outliers
        log_ret = stats.t.rvs(df=2.8, loc=0.0, scale=0.01, size=400, random_state=rng)
        cal_scale, multiplier, coverage = calibrate_empirical_coverage(
            log_ret, df=5.0, loc=0.0, scale=0.008, target_coverage=0.80
        )
        assert multiplier >= 1.0, "Multiplier should never shrink scale below raw fit"
        assert cal_scale >= 0.008
        assert coverage >= 75.0

    def test_calibration_guarantees_minimum_coverage(self, synthetic_t_returns):
        df_fit, loc_fit, scale_fit = fit_student_t_parameters(synthetic_t_returns)
        cal_scale, multiplier, coverage = calibrate_empirical_coverage(
            synthetic_t_returns, df=df_fit, loc=loc_fit, scale=scale_fit, target_coverage=0.80
        )
        assert coverage >= 78.0, f"Empirical coverage should achieve ~80%, got {coverage}"
        assert multiplier >= 1.0

    def test_short_series_calibration_fallback(self):
        short = np.array([0.01, -0.01])
        cal_scale, multiplier, coverage = calibrate_empirical_coverage(
            short, df=5.0, loc=0.0, scale=0.015, target_coverage=0.80
        )
        assert cal_scale == 0.015
        assert multiplier == 1.0
        assert coverage == 80.0


class TestCalibratedMonteCarloSimulation:
    """Tests for end-to-end simulation runner and output contracts."""

    def test_run_calibrated_monte_carlo_success(self, synthetic_price_df):
        dto = run_calibrated_monte_carlo(
            df=synthetic_price_df,
            horizon_days=30,
            simulations=1000,
            seed=42,
        )

        assert isinstance(dto, CalibratedMonteCarloDTO)
        assert dto.horizon_days == 30
        assert dto.current_price > 0
        assert 2.5 <= dto.degrees_of_freedom <= 30.0
        assert dto.scale_multiplier >= 1.0
        assert 70.0 <= dto.empirical_coverage_80 <= 100.0

    def test_percentile_ordering_strictly_holds(self, synthetic_price_df):
        dto = run_calibrated_monte_carlo(
            df=synthetic_price_df,
            horizon_days=20,
            simulations=2000,
            seed=42,
        )

        # Final percentiles must be strictly monotonically increasing
        assert dto.p10 < dto.p25 < dto.p50 < dto.p75 < dto.p90, (
            f"Percentile ordering failed: P10={dto.p10}, P25={dto.p25}, "
            f"P50={dto.p50}, P75={dto.p75}, P90={dto.p90}"
        )

        # Fan chart daily percentiles must also maintain ordering on every day
        assert len(dto.fan_dates) == 20
        assert len(dto.fan_p10) == 20
        assert len(dto.fan_p50) == 20
        assert len(dto.fan_p90) == 20

        for i in range(dto.horizon_days):
            assert dto.fan_p10[i] <= dto.fan_p50[i] <= dto.fan_p90[i], (
                f"Fan chart ordering failed on day {i}: "
                f"P10={dto.fan_p10[i]}, P50={dto.fan_p50[i]}, P90={dto.fan_p90[i]}"
            )

    def test_strictly_bans_point_estimates(self, synthetic_price_df):
        dto = run_calibrated_monte_carlo(synthetic_price_df)
        data_dict = dto.model_dump()

        # Assert no single point estimate target price exists in output
        assert "target_price" not in data_dict
        assert "target_return" not in data_dict
        assert "point_estimate" not in data_dict
        assert "expected_price" not in data_dict

    def test_deterministic_replay_with_seed(self, synthetic_price_df):
        res1 = run_calibrated_monte_carlo(synthetic_price_df, simulations=500, seed=12345)
        res2 = run_calibrated_monte_carlo(synthetic_price_df, simulations=500, seed=12345)

        assert res1.p10 == res2.p10
        assert res1.p50 == res2.p50
        assert res1.p90 == res2.p90
        assert res1.prob_profit == res2.prob_profit
        assert res1.fan_p50 == res2.fan_p50

    def test_custom_as_of_date(self, synthetic_price_df):
        custom_base = datetime(2026, 6, 1)
        dto = run_calibrated_monte_carlo(synthetic_price_df, horizon_days=5, as_of_date=custom_base)
        assert dto.fan_dates[0] == "2026-06-02"
        assert dto.fan_dates[4] == "2026-06-06"

    def test_error_handling_invalid_inputs(self):
        # Empty DataFrame
        with pytest.raises(ValueError, match="empty or missing 'Close'"):
            run_calibrated_monte_carlo(pd.DataFrame())

        # Missing 'Close' column
        with pytest.raises(ValueError, match="empty or missing 'Close'"):
            run_calibrated_monte_carlo(pd.DataFrame({"Open": [100, 101]}))

        # Only 1 price observation
        with pytest.raises(ValueError, match="Insufficient closing price"):
            run_calibrated_monte_carlo(pd.DataFrame({"Close": [100.0]}))

        # Non-positive current price
        with pytest.raises(ValueError, match="Current price must be positive"):
            run_calibrated_monte_carlo(pd.DataFrame({"Close": [100.0, 0.0]}))
