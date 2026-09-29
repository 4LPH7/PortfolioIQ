"""
Unit Tests for Quantitative Indicator Evaluator & Spearman IC Engine
Phase 5: Make the Signal Engine Evidence-Based
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.analytics.indicator_evaluator import (
    compute_all_factors,
    compute_bollinger_factor,
    compute_composite_score,
    compute_forward_returns,
    compute_macd_factor,
    compute_momentum_factor,
    compute_rsi_factor,
    compute_spearman_ic,
    evaluate_indicators,
)
from src.models.dtos import IndicatorEvaluationDTO

# ──────────────────────────────────────────────────────────
# Fixtures & Synthetic Data
# ──────────────────────────────────────────────────────────


def _create_sample_ohlcv(n_bars: int = 100, seed: int = 42) -> pd.DataFrame:
    """Generate realistic synthetic OHLCV data using geometric Brownian motion."""
    np.random.seed(seed)
    returns = np.random.normal(0.0005, 0.015, n_bars)
    prices = 100.0 * np.exp(np.cumsum(returns))
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="B")
    return pd.DataFrame({"Close": prices}, index=dates)


# ──────────────────────────────────────────────────────────
# Factor Generator Tests
# ──────────────────────────────────────────────────────────


def test_factor_scores_bounded_in_interval():
    """Verify all factor scores are strictly bounded within [-1.0, +1.0]."""
    df = _create_sample_ohlcv(120)

    rsi_factor = compute_rsi_factor(df)
    macd_factor = compute_macd_factor(df)
    bb_factor = compute_bollinger_factor(df)
    mom_factor = compute_momentum_factor(df)

    for factor_series, name in [
        (rsi_factor, "RSI"),
        (macd_factor, "MACD"),
        (bb_factor, "BOLLINGER"),
        (mom_factor, "MOMENTUM"),
    ]:
        valid = factor_series.dropna()
        assert not valid.empty, f"{name} series should not be empty"
        assert (valid >= -1.0).all(), f"{name} contains values < -1.0: {valid.min()}"
        assert (valid <= 1.0).all(), f"{name} contains values > +1.0: {valid.max()}"


def test_factor_generator_edge_cases():
    """Verify behavior on empty DataFrames, missing columns, and constant series."""
    # Empty DataFrame
    empty_df = pd.DataFrame()
    assert compute_rsi_factor(empty_df).empty
    assert compute_macd_factor(empty_df).empty
    assert compute_bollinger_factor(empty_df).empty
    assert compute_momentum_factor(empty_df).empty

    # Missing 'Close' column
    no_close_df = pd.DataFrame({"Open": [10.0, 20.0]})
    assert compute_rsi_factor(no_close_df).empty
    assert compute_macd_factor(no_close_df).empty
    assert compute_bollinger_factor(no_close_df).empty
    assert compute_momentum_factor(no_close_df).empty

    # Flat / constant prices
    flat_df = pd.DataFrame({"Close": [100.0] * 50})
    rsi_flat = compute_rsi_factor(flat_df)
    assert rsi_flat.iloc[-1] == 0.0

    macd_flat = compute_macd_factor(flat_df)
    assert macd_flat.iloc[-1] == 0.0

    bb_flat = compute_bollinger_factor(flat_df)
    assert bb_flat.iloc[-1] == 0.0

    mom_flat = compute_momentum_factor(flat_df)
    assert mom_flat.iloc[-1] == 0.0


def test_directional_factors_monotonic_trends():
    """Verify directional factor conventions under monotonic trends."""
    # Pure uptrend
    uptrend_df = pd.DataFrame({"Close": np.arange(100.0, 180.0, 1.0)})
    rsi_up = compute_rsi_factor(uptrend_df)
    # Overbought mean-reversion convention: overbought RSI (>70) maps to negative factor
    assert rsi_up.iloc[-1] <= -0.5
    # Linear regression momentum factor in pure uptrend should be positive (+1.0)
    mom_up = compute_momentum_factor(uptrend_df)
    assert mom_up.iloc[-1] == 1.0

    # Pure downtrend
    downtrend_df = pd.DataFrame({"Close": np.arange(180.0, 100.0, -1.0)})
    rsi_down = compute_rsi_factor(downtrend_df)
    # Oversold mean-reversion convention: oversold RSI (<30) maps to positive factor
    assert rsi_down.iloc[-1] >= 0.5
    # Linear regression momentum factor in pure downtrend should be negative (-1.0)
    mom_down = compute_momentum_factor(downtrend_df)
    assert mom_down.iloc[-1] == -1.0


def test_compute_all_factors_and_forward_returns():
    """Verify orchestrator dictionary output and forward returns calculation."""
    df = _create_sample_ohlcv(60)
    factors = compute_all_factors(df)

    assert set(factors.keys()) == {"RSI", "MACD", "BOLLINGER", "LR_MOMENTUM"}
    for _k, series in factors.items():
        assert len(series) == len(df)
        assert (series >= -1.0).all() and (series <= 1.0).all()

    # Forward returns
    fwd_ret = compute_forward_returns(df, horizon=5)
    assert len(fwd_ret) == len(df)
    # Strict zero lookahead: last 5 bars must be NaN
    assert fwd_ret.iloc[-5:].isna().all()
    # First valid return: (P_5 - P_0) / P_0
    expected_ret_0 = (df["Close"].iloc[5] - df["Close"].iloc[0]) / df["Close"].iloc[0]
    assert np.isclose(fwd_ret.iloc[0], expected_ret_0)


# ──────────────────────────────────────────────────────────
# Spearman IC Engine Tests
# ──────────────────────────────────────────────────────────


def test_spearman_ic_benchmarks():
    """Test Spearman IC calculation on known correlation benchmarks."""
    x = pd.Series(np.arange(1, 31, dtype=float))
    # Perfect positive correlation
    ic_pos, p_pos = compute_spearman_ic(x, x * 2.0 + 5.0)
    assert np.isclose(ic_pos, 1.0)
    assert p_pos < 1e-4

    # Perfect negative correlation
    ic_neg, p_neg = compute_spearman_ic(x, -x * 3.0)
    assert np.isclose(ic_neg, -1.0)
    assert p_neg < 1e-4

    # Constant / zero-variance series
    const_series = pd.Series(np.full(30, 42.0))
    ic_const, p_const = compute_spearman_ic(const_series, x)
    assert ic_const == 0.0
    assert p_const == 1.0


def test_spearman_ic_edge_cases():
    """Test Spearman IC on short samples, NaNs, and infinite values."""
    # Small sample (< 15 bars)
    short_x = pd.Series(range(10))
    short_y = pd.Series(range(10))
    ic_short, p_short = compute_spearman_ic(short_x, short_y)
    assert ic_short == 0.0
    assert p_short == 1.0

    # Series with NaNs and Infs
    x = pd.Series([np.nan, 1.0, 2.0, np.inf] + list(range(3, 25)))
    y = pd.Series([10.0, np.nan, 20.0, 30.0] + list(range(3, 25)))
    ic, p = compute_spearman_ic(x, y)
    # Valid paired length >= 15
    assert not np.isnan(ic)
    assert not np.isnan(p)

    # None inputs
    assert compute_spearman_ic(None, None) == (0.0, 1.0)


# ──────────────────────────────────────────────────────────
# Statistical Pruning & Dynamic Weighting Tests
# ──────────────────────────────────────────────────────────


def test_statistical_pruning_negative_ic():
    """Verify indicators with non-positive mean IC are pruned (weight = 0)."""
    np.random.seed(42)
    # Inverted signal: factor opposes forward returns
    factor_slice = pd.Series(np.arange(30, dtype=float))
    return_slice = pd.Series(-np.arange(30, dtype=float) + np.random.normal(0, 0.1, 30))

    evals = evaluate_indicators(
        factor_slices={"BAD_INDICATOR": factor_slice},
        forward_return_slices=return_slice,
    )

    assert len(evals) == 1
    ev = evals[0]
    assert ev.name == "BAD_INDICATOR"
    assert ev.is_pruned is True
    assert ev.weight == 0.0
    assert "Non-positive out-of-sample IC" in (ev.prune_reason or "")


def test_statistical_pruning_insignificant_p_value():
    """Verify indicators with p > 0.05 are pruned even if IC > 0."""
    np.random.seed(123)
    # Random uncorrelated noise yielding statistically insignificant IC
    factor_slice = pd.Series(np.random.normal(0, 1, 20))
    return_slice = pd.Series(np.random.normal(0, 1, 20))

    evals = evaluate_indicators(
        factor_slices={"NOISY_INDICATOR": factor_slice},
        forward_return_slices=return_slice,
    )

    assert len(evals) == 1
    ev = evals[0]
    assert ev.is_pruned is True
    assert ev.weight == 0.0
    assert ev.prune_reason is not None


def test_dynamic_weighting_proportional_to_information_ratio():
    """Verify valid unpruned indicators are weighted proportional to their IR and sum to 1.0."""
    np.random.seed(42)
    # Fold 1 & Fold 2
    f1_fwd = pd.Series(np.arange(25, dtype=float) + np.random.normal(0, 1.0, 25))
    f2_fwd = pd.Series(np.arange(25, dtype=float) + np.random.normal(0, 1.0, 25))

    # Strong predictor: higher correlation
    strong_f1 = pd.Series(np.arange(25, dtype=float) + np.random.normal(0, 0.2, 25))
    strong_f2 = pd.Series(np.arange(25, dtype=float) + np.random.normal(0, 0.2, 25))

    # Moderate predictor: moderate correlation
    mod_f1 = pd.Series(np.arange(25, dtype=float) + np.random.normal(0, 1.5, 25))
    mod_f2 = pd.Series(np.arange(25, dtype=float) + np.random.normal(0, 1.5, 25))

    # Noise predictor: completely random
    noise_f1 = pd.Series(np.random.normal(0, 1, 25))
    noise_f2 = pd.Series(np.random.normal(0, 1, 25))

    evals = evaluate_indicators(
        factor_slices={
            "STRONG": [strong_f1, strong_f2],
            "MODERATE": [mod_f1, mod_f2],
            "NOISE": [noise_f1, noise_f2],
        },
        forward_return_slices=[f1_fwd, f2_fwd],
    )

    eval_map = {e.name: e for e in evals}
    strong_ev = eval_map["STRONG"]
    mod_ev = eval_map["MODERATE"]
    noise_ev = eval_map["NOISE"]

    assert strong_ev.is_pruned is False
    assert mod_ev.is_pruned is False
    assert noise_ev.is_pruned is True
    assert noise_ev.weight == 0.0

    # Strong indicator should have higher weight than moderate indicator
    assert strong_ev.weight > mod_ev.weight
    # Active weights must sum to 1.0
    total_weight = sum(e.weight for e in evals)
    assert np.isclose(total_weight, 1.0)


def test_all_indicators_pruned_fallback():
    """Verify fallback behavior when every indicator fails the hurdle."""
    np.random.seed(999)
    # All noisy uncorrelated series
    fwd = pd.Series(np.random.normal(0, 1, 20))
    s1 = pd.Series(np.random.normal(0, 1, 20))
    s2 = pd.Series(np.random.normal(0, 1, 20))

    evals = evaluate_indicators(
        factor_slices={"IND1": s1, "IND2": s2},
        forward_return_slices=fwd,
    )

    for ev in evals:
        assert ev.is_pruned is True
        assert ev.weight == 0.0

    # Composite score fallback
    score, status = compute_composite_score(evals)
    assert score == 50.0
    assert status == "UNPROVEN_NOISE"


# ──────────────────────────────────────────────────────────
# Composite Score Engine Tests
# ──────────────────────────────────────────────────────────


def test_composite_score_directional_outcomes():
    """Verify composite score generates correct directional labels (BUY, SELL, HOLD)."""
    # Create mock evaluations
    ev_rsi = IndicatorEvaluationDTO(
        name="RSI",
        is_pruned=False,
        weight=0.60,
        mean_ic=0.08,
        p_value=0.01,
        score=50.0,
    )
    ev_macd = IndicatorEvaluationDTO(
        name="MACD",
        is_pruned=False,
        weight=0.40,
        mean_ic=0.05,
        p_value=0.02,
        score=50.0,
    )

    # Bullish scenario (factor scores +0.8)
    # Score for +0.8 is (0.8 + 1.0) * 50 = 90.0
    bullish_factors = {"RSI": 0.8, "MACD": 0.8}
    score_bull, label_bull = compute_composite_score([ev_rsi, ev_macd], bullish_factors)
    assert score_bull >= 60.0
    assert label_bull == "BUY"

    # Bearish scenario (factor scores -0.8)
    # Score for -0.8 is (-0.8 + 1.0) * 50 = 10.0
    bearish_factors = {"RSI": -0.8, "MACD": -0.8}
    score_bear, label_bear = compute_composite_score([ev_rsi, ev_macd], bearish_factors)
    assert score_bear <= 40.0
    assert label_bear == "SELL"

    # Neutral scenario (factor scores 0.0)
    neutral_factors = {"RSI": 0.0, "MACD": 0.0}
    score_neutral, label_neutral = compute_composite_score([ev_rsi, ev_macd], neutral_factors)
    assert np.isclose(score_neutral, 50.0)
    assert label_neutral == "HOLD"


def test_evaluate_indicators_with_insample_and_current_factors():
    """Verify in-sample IC calculation and current_factors score mapping."""
    np.random.seed(42)
    in_fwd = pd.Series(np.arange(30, dtype=float))
    out_fwd = pd.Series(np.arange(30, dtype=float))

    in_factor = pd.Series(np.arange(30, dtype=float))
    out_factor = pd.Series(np.arange(30, dtype=float))

    evals = evaluate_indicators(
        factor_slices={"IND_A": out_factor},
        forward_return_slices=out_fwd,
        in_sample_factor_slices={"IND_A": in_factor},
        in_sample_return_slices=in_fwd,
        current_factors={"IND_A": 0.6},
    )

    assert len(evals) == 1
    ev = evals[0]
    assert ev.in_sample_ic is not None
    assert np.isclose(ev.in_sample_ic, 1.0)
    assert ev.out_sample_ic is not None
    assert np.isclose(ev.out_sample_ic, 1.0)
    assert ev.is_pruned is False
    assert ev.weight == 1.0
    # Score for factor 0.6: (0.6 + 1.0) * 50 = 80.0
    assert np.isclose(ev.score, 80.0)


def test_composite_score_fallback_to_ev_score():
    """Verify composite score uses ev.score if current_factors is omitted."""
    ev = IndicatorEvaluationDTO(
        name="MOM",
        is_pruned=False,
        weight=1.0,
        score=75.0,
    )
    score, label = compute_composite_score([ev])
    assert score == 75.0
    assert label == "BUY"


def test_compute_forward_returns_series_input():
    """Verify compute_forward_returns works when passed pd.Series directly."""
    prices = pd.Series([100.0, 105.0, 110.0, 115.0, 120.0])
    fwd = compute_forward_returns(prices, horizon=2)
    # At index 0: (110 - 100) / 100 = 0.10
    assert np.isclose(fwd.iloc[0], 0.10)
    # Last 2 bars must be NaN
    assert fwd.iloc[-2:].isna().all()
