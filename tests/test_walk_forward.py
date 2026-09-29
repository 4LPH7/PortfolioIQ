"""
Unit Tests for Rolling Walk-Forward Backtester & Dual-Baseline Attribution
Phase 5: Make the Signal Engine Evidence-Based
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.analytics.walk_forward import (
    generate_walk_forward_folds,
    run_walk_forward_backtest,
)
from src.models.dtos import BacktestRunDTO

# ──────────────────────────────────────────────────────────
# Synthetic Data Helpers
# ──────────────────────────────────────────────────────────


def _create_synthetic_series(
    n_bars: int = 400, drift: float = 0.0005, vol: float = 0.015, seed: int = 42
) -> pd.DataFrame:
    """Generate reproducible daily OHLCV price series."""
    np.random.seed(seed)
    returns = np.random.normal(drift, vol, n_bars)
    prices = 100.0 * np.exp(np.cumsum(returns))
    dates = pd.date_range("2023-01-01", periods=n_bars, freq="B")
    return pd.DataFrame({"Close": prices}, index=dates)


# ──────────────────────────────────────────────────────────
# Fold Generator Tests
# ──────────────────────────────────────────────────────────


def test_generate_walk_forward_folds_success():
    """Verify fold generation creates contiguous, non-overlapping test windows."""
    total_bars = 441  # 252 + 3 * 63
    folds = generate_walk_forward_folds(
        total_bars=total_bars,
        train_window=252,
        test_window=63,
        step=63,
    )

    assert len(folds) == 3
    # Check fold indices
    prev_test_stop = None
    for _i, (train_slice, test_slice) in enumerate(folds):
        assert train_slice.stop - train_slice.start == 252
        assert test_slice.stop - test_slice.start == 63
        assert train_slice.stop == test_slice.start

        if prev_test_stop is not None:
            # Strictly contiguous and non-overlapping test slices
            assert test_slice.start == prev_test_stop
        prev_test_stop = test_slice.stop


def test_generate_walk_forward_folds_insufficient_bars():
    """Verify ValueError is raised if bars < train_window + test_window."""
    with pytest.raises(ValueError, match="Insufficient bars for walk-forward validation"):
        generate_walk_forward_folds(total_bars=200, train_window=252, test_window=63)


def test_zero_lookahead_temporal_isolation():
    """Verify train slice strictly precedes test slice in every fold."""
    folds = generate_walk_forward_folds(total_bars=350, train_window=252, test_window=63, step=30)
    for train_slice, test_slice in folds:
        assert train_slice.start < train_slice.stop
        assert train_slice.stop <= test_slice.start
        assert test_slice.start < test_slice.stop


# ──────────────────────────────────────────────────────────
# Strategy Simulation & Dual Baseline Tests
# ──────────────────────────────────────────────────────────


def test_run_walk_forward_backtest_basic():
    """Verify walk-forward simulation produces valid BacktestRunDTO with finite metrics."""
    stock_df = _create_synthetic_series(n_bars=350, seed=101)
    bench_df = _create_synthetic_series(n_bars=350, seed=202)

    result = run_walk_forward_backtest(
        df=stock_df,
        benchmark_df=bench_df,
        tradingsymbol="INFY",
        train_window=252,
        test_window=63,
        step=63,
        initial_capital=100000.0,
    )

    assert isinstance(result, BacktestRunDTO)
    assert result.tradingsymbol == "INFY"
    assert result.total_folds >= 1
    assert isinstance(result.train_start_date, date)
    assert isinstance(result.test_end_date, date)

    # Strategy metrics should be finite floats
    assert isinstance(result.strategy_cagr, float)
    assert isinstance(result.strategy_sharpe, float)
    assert isinstance(result.strategy_max_drawdown, float)
    assert result.strategy_max_drawdown >= 0.0

    # Baseline metrics
    assert isinstance(result.stock_cagr, float)
    assert isinstance(result.benchmark_cagr, float)
    assert np.isclose(
        result.excess_cagr_vs_stock, round(result.strategy_cagr - result.stock_cagr, 4)
    )
    assert np.isclose(
        result.excess_cagr_vs_benchmark, round(result.strategy_cagr - result.benchmark_cagr, 4)
    )

    # Status classification
    assert result.status in ("PROVEN_EDGE", "UNPROVEN_NOISE")
    assert isinstance(result.passed_hurdle, bool)
    assert "beats_stock" in result.hurdle_details
    assert "beats_benchmark" in result.hurdle_details


def test_transaction_cost_deduction_in_simulation():
    """Verify that trades incur Indian statutory and broker delivery charges."""
    # Create trending scrip that triggers BUY and SELL signals
    stock_df = _create_synthetic_series(n_bars=350, drift=0.002, vol=0.02, seed=42)

    result = run_walk_forward_backtest(
        df=stock_df,
        tradingsymbol="RELIANCE",
        train_window=252,
        test_window=63,
        step=63,
    )

    # If trades occurred, cost drag bps must be recorded
    if result.total_trades > 0:
        assert result.total_cost_drag_bps >= 0.0
        if result.strategy_win_rate is not None:
            assert 0.0 <= result.strategy_win_rate <= 1.0


def test_walk_forward_default_benchmark_when_omitted():
    """Verify simulation operates properly when benchmark_df is None."""
    stock_df = _create_synthetic_series(n_bars=320, seed=77)

    result = run_walk_forward_backtest(
        df=stock_df,
        benchmark_df=None,
        tradingsymbol="TCS",
        train_window=252,
        test_window=63,
    )

    assert result.benchmark_cagr is not None
    assert result.benchmark_sharpe is not None


def test_walk_forward_invalid_inputs_raise():
    """Verify input validation on empty DataFrames or missing columns."""
    empty_df = pd.DataFrame()
    with pytest.raises(ValueError, match="empty or missing 'Close' column"):
        run_walk_forward_backtest(empty_df, tradingsymbol="INVALID")

    no_close_df = pd.DataFrame({"High": [100.0] * 320})
    with pytest.raises(ValueError, match="empty or missing 'Close' column"):
        run_walk_forward_backtest(no_close_df, tradingsymbol="INVALID")


def test_walk_forward_all_indicators_pruned_handling():
    """Verify backtest executes safely when all indicators fail hurdle and stays in cash."""
    # Pure flat series gives 0 IC and pruned indicators
    flat_prices = pd.DataFrame(
        {"Close": [100.0] * 350},
        index=pd.date_range("2023-01-01", periods=350, freq="B"),
    )

    result = run_walk_forward_backtest(
        df=flat_prices,
        tradingsymbol="FLAT",
        train_window=252,
        test_window=63,
    )

    # In flat prices, zero trades should be triggered
    assert result.total_trades == 0
    assert result.strategy_win_rate is None
    assert result.status == "UNPROVEN_NOISE"
    assert result.passed_hurdle is False


def test_walk_forward_non_datetime_index():
    """Verify walk-forward backtest accepts string dates or integer indices."""
    dates = [f"2023-01-{i + 1:02d}" for i in range(25)] * 14  # 350 dates
    df = pd.DataFrame({"Close": np.linspace(100.0, 150.0, 350)}, index=dates)

    result = run_walk_forward_backtest(
        df=df,
        tradingsymbol="STR_INDEX",
        train_window=252,
        test_window=63,
    )

    assert result.tradingsymbol == "STR_INDEX"
    assert isinstance(result.train_start_date, date)


def test_walk_forward_disjoint_benchmark_fallback():
    """Verify handling when benchmark dates do not overlap with stock dates."""
    stock_df = _create_synthetic_series(n_bars=350, seed=1)
    # Benchmark in 2010
    bench_df = pd.DataFrame(
        {"Close": [15000.0] * 350},
        index=pd.date_range("2010-01-01", periods=350, freq="B"),
    )

    result = run_walk_forward_backtest(
        df=stock_df,
        benchmark_df=bench_df,
        tradingsymbol="DISJOINT",
        train_window=252,
        test_window=63,
    )

    assert result.benchmark_cagr is not None
    assert result.status in ("PROVEN_EDGE", "UNPROVEN_NOISE")
