"""
PortfolioIQ — Rolling Walk-Forward Backtester & Dual-Baseline Attribution
Phase 5: Make the Signal Engine Evidence-Based

Implements rolling 252-train / 63-test walk-forward validation with strict
zero-lookahead bias. Simulates out-of-sample trading net of Indian delivery
transaction charges (STT, DP charges, GST), benchmarks against dual baselines
(Stock Buy-and-Hold and NIFTY 50 TRI Buy-and-Hold), and evaluates evidence hurdles.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from src.analytics.cost_calculator import compute_indian_delivery_charges
from src.analytics.indicator_evaluator import (
    compute_all_factors,
    compute_composite_score,
    compute_forward_returns,
    evaluate_indicators,
)
from src.models.dtos import BacktestRunDTO, IndicatorEvaluationDTO


def _to_date(val: Any) -> date:
    """Helper to cleanly extract datetime.date from various timestamp types."""
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if hasattr(val, "date"):
        return val.date()
    return pd.to_datetime(val).date()


def generate_walk_forward_folds(
    total_bars: int,
    train_window: int = 252,
    test_window: int = 63,
    step: int = 63,
) -> list[tuple[slice, slice]]:
    """
    Generates rolling train and test slice windows with strict temporal causality.

    Parameters:
        total_bars: Total number of sequential daily price observations.
        train_window: Number of trading bars in each calibration window (default: 252, ~1 year).
        test_window: Number of trading bars in each out-of-sample test window (default: 63, ~1 quarter).
        step: Step forward size between folds (default: 63 for contiguous non-overlapping test slices).

    Returns:
        List of (train_slice, test_slice) tuples.

    Raises:
        ValueError: If total_bars is insufficient (< train_window + test_window).
    """
    min_required = train_window + test_window
    if total_bars < min_required:
        raise ValueError(
            f"Insufficient bars for walk-forward validation: {total_bars} < {min_required} "
            f"(requires at least train_window={train_window} + test_window={test_window})"
        )

    folds: list[tuple[slice, slice]] = []
    start = 0
    while start + train_window + test_window <= total_bars:
        train_slice = slice(start, start + train_window)
        test_slice = slice(start + train_window, start + train_window + test_window)
        folds.append((train_slice, test_slice))
        start += step

    return folds


def run_walk_forward_backtest(
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame | None = None,
    tradingsymbol: str = "DEFAULT",
    model_version: str = "v1.0.0",
    train_window: int = 252,
    test_window: int = 63,
    step: int = 63,
    forward_horizon: int = 20,
    initial_capital: float = 100000.0,
    risk_free_rate: float = 0.065,
) -> BacktestRunDTO:
    """
    Executes rolling walk-forward backtest simulation with Indian transaction costs
    and dual baseline attribution.

    Parameters:
        df: Historical OHLCV DataFrame for the target equity (must include 'Close').
        benchmark_df: Historical OHLCV DataFrame for the benchmark (e.g. NIFTY 50 TRI).
                      If None, falls back to using df prices as flat reference.
        tradingsymbol: Ticker symbol being evaluated.
        model_version: Version identifier of the signal model.
        train_window: In-sample training window size in bars (default: 252).
        test_window: Out-of-sample test window size in bars (default: 63).
        step: Step size between folds (default: 63).
        forward_horizon: Forward return horizon for indicator calibration (default: 20).
        initial_capital: Starting cash balance in INR (default: 100,000.0).
        risk_free_rate: Annual Indian risk-free rate (default: 6.5%, 0.065).

    Returns:
        BacktestRunDTO containing out-of-sample strategy metrics, dual baseline comparisons,
        and evidence hurdle classification.
    """
    if df.empty or "Close" not in df.columns:
        raise ValueError("Stock DataFrame is empty or missing 'Close' column")

    # Clean and align stock dates
    stock_df = df.copy()
    if not isinstance(stock_df.index, pd.DatetimeIndex):
        stock_df.index = pd.to_datetime(stock_df.index)

    # Benchmark handling and alignment
    if benchmark_df is not None and not benchmark_df.empty and "Close" in benchmark_df.columns:
        bench_df = benchmark_df.copy()
        if not isinstance(bench_df.index, pd.DatetimeIndex):
            bench_df.index = pd.to_datetime(bench_df.index)

        # Inner join to align dates
        common_idx = stock_df.index.intersection(bench_df.index)
        if len(common_idx) == 0:
            logger.warning(
                "No overlapping dates between stock and benchmark; aligning on stock index"
            )
            bench_df = pd.DataFrame({"Close": stock_df["Close"].values}, index=stock_df.index)
        else:
            stock_df = stock_df.loc[common_idx]
            bench_df = bench_df.loc[common_idx]
    else:
        # Default benchmark if unavailable
        bench_df = pd.DataFrame({"Close": stock_df["Close"].values}, index=stock_df.index)

    total_bars = len(stock_df)
    folds = generate_walk_forward_folds(
        total_bars=total_bars,
        train_window=train_window,
        test_window=test_window,
        step=step,
    )

    # Precalculate continuous factor scores and forward returns across full history
    all_factors = compute_all_factors(stock_df)
    fwd_returns = compute_forward_returns(stock_df, horizon=forward_horizon)

    # Simulation state
    cash = float(initial_capital)
    position_shares = 0
    entry_price = 0.0
    entry_cost = Decimal("0.00")
    total_cost_drag_inr = Decimal("0.00")
    closed_trades: list[dict[str, Any]] = []
    daily_nav_history: list[tuple[Any, float]] = []

    latest_calibrated_evals: list[IndicatorEvaluationDTO] = []

    # Iterate sequentially through walk-forward folds
    for _fold_idx, (train_slice, test_slice) in enumerate(folds):
        # In-sample calibration: strict causal isolation
        train_factors = {name: s.iloc[train_slice] for name, s in all_factors.items()}
        train_fwd = fwd_returns.iloc[train_slice].copy()
        # Guarantee zero lookahead: truncate the trailing forward_horizon bars from calibration
        if len(train_fwd) > forward_horizon:
            train_fwd.iloc[-forward_horizon:] = np.nan

        calibrated_evals = evaluate_indicators(
            factor_slices=train_factors,
            forward_return_slices=train_fwd,
        )
        latest_calibrated_evals = calibrated_evals

        # Out-of-sample execution on test slice
        for bar_idx in range(test_slice.start, test_slice.stop):
            bar_date = stock_df.index[bar_idx]
            cur_price = float(stock_df["Close"].iloc[bar_idx])
            cur_factors = {name: float(all_factors[name].iloc[bar_idx]) for name in all_factors}

            composite_score, _ = compute_composite_score(
                evaluations=calibrated_evals,
                current_factors=cur_factors,
            )

            # Signal execution rules: BUY >= 60.0, SELL <= 40.0
            if composite_score >= 60.0 and position_shares == 0 and cur_price > 0:
                # Buy maximum integer delivery shares (leaving ~0.25% buffer for transaction fees)
                target_shares = int(cash / (cur_price * 1.0025))
                if target_shares > 0:
                    trade_val = Decimal(str(round(target_shares * cur_price, 2)))
                    buy_costs = compute_indian_delivery_charges(
                        side="BUY",
                        trade_value=trade_val,
                    )
                    required_outlay = float(trade_val + buy_costs.total_charges)
                    if cash >= required_outlay:
                        position_shares = target_shares
                        entry_price = cur_price
                        entry_cost = buy_costs.total_charges
                        cash -= required_outlay
                        total_cost_drag_inr += buy_costs.total_charges

            elif composite_score <= 40.0 and position_shares > 0 and cur_price > 0:
                # Liquidate delivery position
                sell_val = Decimal(str(round(position_shares * cur_price, 2)))
                sell_costs = compute_indian_delivery_charges(
                    side="SELL",
                    trade_value=sell_val,
                    is_first_sell_of_scrip=True,
                )
                gross_pnl = float(sell_val - Decimal(str(round(position_shares * entry_price, 2))))
                net_pnl = float(
                    sell_val
                    - sell_costs.total_charges
                    - Decimal(str(round(position_shares * entry_price, 2)))
                    - entry_cost
                )
                cash += float(sell_val - sell_costs.total_charges)
                total_cost_drag_inr += sell_costs.total_charges

                closed_trades.append(
                    {
                        "entry_price": entry_price,
                        "exit_price": cur_price,
                        "shares": position_shares,
                        "gross_pnl": gross_pnl,
                        "net_pnl": net_pnl,
                        "charges": float(entry_cost + sell_costs.total_charges),
                    }
                )
                position_shares = 0
                entry_price = 0.0
                entry_cost = Decimal("0.00")

            # Mark-to-market daily portfolio NAV
            nav = cash + (position_shares * cur_price)
            daily_nav_history.append((bar_date, nav))

    # Mark to market and close any open position at the final test bar
    if position_shares > 0 and daily_nav_history:
        final_price = float(stock_df["Close"].iloc[folds[-1][1].stop - 1])
        final_val = Decimal(str(round(position_shares * final_price, 2)))
        final_costs = compute_indian_delivery_charges(
            side="SELL",
            trade_value=final_val,
            is_first_sell_of_scrip=True,
        )
        gross_pnl = float(final_val - Decimal(str(round(position_shares * entry_price, 2))))
        net_pnl = float(
            final_val
            - final_costs.total_charges
            - Decimal(str(round(position_shares * entry_price, 2)))
            - entry_cost
        )
        total_cost_drag_inr += final_costs.total_charges
        cash += float(final_val - final_costs.total_charges)

        closed_trades.append(
            {
                "entry_price": entry_price,
                "exit_price": final_price,
                "shares": position_shares,
                "gross_pnl": gross_pnl,
                "net_pnl": net_pnl,
                "charges": float(entry_cost + final_costs.total_charges),
            }
        )
        position_shares = 0
        # Update final daily NAV
        daily_nav_history[-1] = (daily_nav_history[-1][0], cash)

    # ──────────────────────────────────────────────────────────
    # Performance & Risk Metric Formulations
    # ──────────────────────────────────────────────────────────

    test_dates = [d for d, _ in daily_nav_history]
    equity_series = pd.Series(
        [nav for _, nav in daily_nav_history],
        index=pd.to_datetime(test_dates),
    )
    n_days = len(equity_series)
    years = max(n_days / 252.0, 1.0 / 252.0)
    daily_rf = (1.0 + risk_free_rate) ** (1.0 / 252.0) - 1.0

    # Strategy Metrics
    strat_total_return = (equity_series.iloc[-1] - initial_capital) / initial_capital
    if strat_total_return > -1.0 and n_days > 0:
        strategy_cagr = (1.0 + strat_total_return) ** (252.0 / n_days) - 1.0
    else:
        strategy_cagr = -1.0

    strat_daily_ret = equity_series.pct_change().dropna()
    strat_excess = strat_daily_ret - daily_rf
    strat_std = float(strat_excess.std())
    strategy_sharpe = (
        float(np.sqrt(252.0) * strat_excess.mean() / strat_std) if strat_std > 1e-6 else 0.0
    )

    strat_downside = strat_excess[strat_excess < 0]
    strat_downside_dev = (
        float(np.sqrt(np.mean(strat_downside**2))) if len(strat_downside) > 0 else 0.0
    )
    strategy_sortino = (
        float(np.sqrt(252.0) * strat_excess.mean() / strat_downside_dev)
        if strat_downside_dev > 1e-6
        else 0.0
    )

    strat_peak = equity_series.cummax()
    strat_drawdown = (equity_series - strat_peak) / strat_peak
    strategy_max_dd = float(abs(strat_drawdown.min())) if not strat_drawdown.empty else 0.0

    # Closed trades metrics
    total_trades = len(closed_trades)
    winning_trades = sum(1 for t in closed_trades if t["net_pnl"] > 0)
    strategy_win_rate = (winning_trades / total_trades) if total_trades > 0 else None

    gross_gains = sum(t["net_pnl"] for t in closed_trades if t["net_pnl"] > 0)
    gross_losses = sum(abs(t["net_pnl"]) for t in closed_trades if t["net_pnl"] < 0)
    if gross_losses > 0:
        strategy_profit_factor = gross_gains / gross_losses
    elif gross_gains > 0:
        strategy_profit_factor = 10.0
    else:
        strategy_profit_factor = 1.0 if total_trades == 0 else 0.0

    total_cost_drag_bps = float(
        (total_cost_drag_inr / Decimal(str(initial_capital)))
        * Decimal("10000.0")
        / Decimal(str(max(years, 0.25)))
    )

    # ──────────────────────────────────────────────────────────
    # Dual Baseline Attribution
    # ──────────────────────────────────────────────────────────

    # Baseline 1: Stock Buy-and-Hold
    stock_test_prices = stock_df["Close"].loc[equity_series.index].astype(float)
    stock_total_return = (
        (stock_test_prices.iloc[-1] - stock_test_prices.iloc[0]) / stock_test_prices.iloc[0]
        if len(stock_test_prices) > 0 and stock_test_prices.iloc[0] > 0
        else 0.0
    )
    if stock_total_return > -1.0 and n_days > 0:
        stock_cagr = (1.0 + stock_total_return) ** (252.0 / n_days) - 1.0
    else:
        stock_cagr = -1.0

    stock_daily_ret = stock_test_prices.pct_change().dropna()
    stock_excess = stock_daily_ret - daily_rf
    stock_std = float(stock_excess.std())
    stock_sharpe = (
        float(np.sqrt(252.0) * stock_excess.mean() / stock_std) if stock_std > 1e-6 else 0.0
    )
    stock_peak = stock_test_prices.cummax()
    stock_dd = (stock_test_prices - stock_peak) / stock_peak
    stock_max_dd = float(abs(stock_dd.min())) if not stock_dd.empty else 0.0

    # Baseline 2: Market NIFTY 50 TRI Buy-and-Hold
    bench_test_prices = bench_df["Close"].loc[equity_series.index].astype(float)
    bench_total_return = (
        (bench_test_prices.iloc[-1] - bench_test_prices.iloc[0]) / bench_test_prices.iloc[0]
        if len(bench_test_prices) > 0 and bench_test_prices.iloc[0] > 0
        else 0.0
    )
    if bench_total_return > -1.0 and n_days > 0:
        benchmark_cagr = (1.0 + bench_total_return) ** (252.0 / n_days) - 1.0
    else:
        benchmark_cagr = -1.0

    bench_daily_ret = bench_test_prices.pct_change().dropna()
    bench_excess = bench_daily_ret - daily_rf
    bench_std = float(bench_excess.std())
    benchmark_sharpe = (
        float(np.sqrt(252.0) * bench_excess.mean() / bench_std) if bench_std > 1e-6 else 0.0
    )
    bench_peak = bench_test_prices.cummax()
    bench_dd = (bench_test_prices - bench_peak) / bench_peak
    benchmark_max_dd = float(abs(bench_dd.min())) if not bench_dd.empty else 0.0

    excess_cagr_vs_stock = strategy_cagr - stock_cagr
    excess_cagr_vs_benchmark = strategy_cagr - benchmark_cagr

    # ──────────────────────────────────────────────────────────
    # Evidence Hurdle Classification (D-05, D-06)
    # ──────────────────────────────────────────────────────────

    beats_stock = strategy_cagr > stock_cagr and strategy_sharpe > stock_sharpe
    beats_benchmark = strategy_cagr > benchmark_cagr
    has_trades = total_trades > 0
    adequate_win_rate = (strategy_win_rate is not None) and (strategy_win_rate >= 0.40)

    passed_hurdle = bool(beats_stock and beats_benchmark and has_trades and adequate_win_rate)
    status = "PROVEN_EDGE" if passed_hurdle else "UNPROVEN_NOISE"

    hurdle_details = {
        "beats_stock": beats_stock,
        "beats_benchmark": beats_benchmark,
        "has_trades": has_trades,
        "adequate_win_rate": adequate_win_rate,
        "excess_cagr_stock_pct": round(excess_cagr_vs_stock * 100.0, 2),
        "excess_cagr_benchmark_pct": round(excess_cagr_vs_benchmark * 100.0, 2),
    }

    return BacktestRunDTO(
        run_id=str(uuid.uuid4()),
        tradingsymbol=tradingsymbol,
        model_version=model_version,
        train_start_date=_to_date(stock_df.index[folds[0][0].start]),
        train_end_date=_to_date(stock_df.index[folds[0][0].stop - 1]),
        test_start_date=_to_date(stock_df.index[folds[0][1].start]),
        test_end_date=_to_date(stock_df.index[folds[-1][1].stop - 1]),
        train_window_days=train_window,
        test_window_days=test_window,
        total_folds=len(folds),
        strategy_cagr=round(strategy_cagr, 4),
        strategy_sharpe=round(strategy_sharpe, 4),
        strategy_sortino=round(strategy_sortino, 4),
        strategy_max_drawdown=round(strategy_max_dd, 4),
        strategy_win_rate=round(strategy_win_rate, 4) if strategy_win_rate is not None else None,
        strategy_profit_factor=round(strategy_profit_factor, 4),
        total_trades=total_trades,
        stock_cagr=round(stock_cagr, 4),
        stock_sharpe=round(stock_sharpe, 4),
        stock_max_drawdown=round(stock_max_dd, 4),
        benchmark_cagr=round(benchmark_cagr, 4),
        benchmark_sharpe=round(benchmark_sharpe, 4),
        benchmark_max_drawdown=round(benchmark_max_dd, 4),
        excess_cagr_vs_stock=round(excess_cagr_vs_stock, 4),
        excess_cagr_vs_benchmark=round(excess_cagr_vs_benchmark, 4),
        total_cost_drag_bps=round(total_cost_drag_bps, 2),
        status=status,
        passed_hurdle=passed_hurdle,
        hurdle_details=hurdle_details,
        indicators=latest_calibrated_evals,
    )
