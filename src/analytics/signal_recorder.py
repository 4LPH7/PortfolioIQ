"""
PortfolioIQ — Quantitative Signal Recorder & Forward Return Maturation Engine
Phase 5: Make the Signal Engine Evidence-Based (Plan 05-07)

Responsibilities:
1. compute_holding_signal: Computes out-of-sample evaluated indicator weights, composite score,
   calibrated Student's t Monte Carlo cones, and evidence hurdle verification for a scrip.
2. record_eod_signals: Records daily EOD signal snapshots for all user holdings in signal_snapshots.
3. mature_forward_returns: Inspects pending snapshots, evaluates realized forward returns
   across 5, 20, and 60 trading day horizons against stock and benchmark, and updates DB.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

import pandas as pd
from loguru import logger

from src.analytics.calibrated_monte_carlo import run_calibrated_monte_carlo
from src.analytics.historical_cache import get_or_sync_historical_bars
from src.analytics.indicator_evaluator import (
    compute_all_factors,
    compute_forward_returns,
    evaluate_indicators,
)
from src.analytics.rebalancer import evaluate_evidence_hurdles
from src.analytics.walk_forward import run_walk_forward_backtest
from src.db.repository import (
    get_current_holdings,
    get_latest_backtest_run,
    get_pending_forward_return_snapshots,
    record_backtest_run,
    record_signal_snapshot,
    update_signal_forward_returns,
)
from src.models.dtos import (
    BacktestRunDTO,
    CreateSignalSnapshotDTO,
    HoldingSignalDTO,
    SignalSnapshotDTO,
)


def compute_holding_signal(
    symbol: str,
    avg_buy_price: float = 0.0,
    df: pd.DataFrame | None = None,
    bench_df: pd.DataFrame | None = None,
) -> HoldingSignalDTO:
    """
    Computes evidence-based quantitative signal evaluation for a single holding.

    Combines:
    - Independent Spearman rank IC evaluation with dynamic IR weighting.
    - Calibrated fat-tailed Student's t Monte Carlo simulation with P10..P90 dispersion cones.
    - 4-hurdle quantitative backtest classification (PROVEN_EDGE vs UNPROVEN_NOISE).
    """
    stock_df = df
    if stock_df is None:
        try:
            stock_df = get_or_sync_historical_bars(symbol)
        except Exception as exc:
            logger.warning("Failed to fetch historical bars for {}: {}", symbol, exc)
            stock_df = None

    if stock_df is None or len(stock_df) < 35 or "Close" not in stock_df.columns:
        logger.warning(
            "Insufficient historical data for {} (have {} bars, need >= 35)",
            symbol,
            len(stock_df) if stock_df is not None else 0,
        )
        return HoldingSignalDTO(
            symbol=symbol,
            tradingsymbol=symbol,
            current_price=0.0,
            composite_score=50.0,
            signal_label="HOLD",
            status="PENDING",
            evidence_badge="PENDING (Insufficient Bars)",
            avg_buy_price=round(avg_buy_price, 2),
            data_start="",
            data_end="",
            data_points=len(stock_df) if stock_df is not None else 0,
        )

    current_price = float(stock_df["Close"].iloc[-1])

    # 1. Independent indicator evaluation & dynamic IR weighting
    factors = compute_all_factors(stock_df)
    fwd_ret = compute_forward_returns(stock_df, horizon=20)
    current_factors = {k: float(v.iloc[-1]) for k, v in factors.items() if len(v) > 0}

    evaluations = evaluate_indicators(
        factor_slices=factors,
        forward_return_slices=fwd_ret,
        current_factors=current_factors,
    )

    # Calculate dynamic composite score
    valid_evals = [e for e in evaluations if not e.is_pruned and e.weight > 0]
    if valid_evals:
        comp_score = float(sum(e.score * e.weight for e in valid_evals))
    else:
        comp_score = 50.0
    comp_score = max(0.0, min(100.0, comp_score))

    # Determine signal label
    if comp_score >= 75.0:
        sig_label: Literal["STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"] = "STRONG_BUY"
    elif comp_score >= 60.0:
        sig_label = "BUY"
    elif comp_score <= 25.0:
        sig_label = "STRONG_SELL"
    elif comp_score <= 40.0:
        sig_label = "SELL"
    else:
        sig_label = "HOLD"

    # 2. Calibrated Student's t Monte Carlo simulation
    try:
        mc_dto = run_calibrated_monte_carlo(
            df=stock_df,
            horizon_days=30,
            simulations=1000,
            seed=42,
        )
    except Exception as exc:
        logger.warning("Monte Carlo simulation failed for {}: {}", symbol, exc)
        mc_dto = None

    # 3. Check latest backtest evidence hurdles
    bt = None
    try:
        bt = get_latest_backtest_run(symbol)
    except Exception as exc:
        logger.debug("No recorded backtest found for {}: {}", symbol, exc)

    if bt is not None:
        status_val, badge_val, _ = evaluate_evidence_hurdles(bt)
        status: Literal["PROVEN_EDGE", "UNPROVEN_NOISE", "PENDING"] = (
            "PROVEN_EDGE"
            if status_val == "PROVEN_EDGE"
            else "UNPROVEN_NOISE"
            if status_val == "UNPROVEN_NOISE"
            else "PENDING"
        )
        badge = badge_val
        backtest_summary: dict[str, Any] | None = {
            "run_id": str(bt.run_id),
            "status": status,
            "strategy_cagr": bt.strategy_cagr,
            "excess_cagr_vs_benchmark": bt.excess_cagr_vs_benchmark,
            "win_rate": bt.strategy_win_rate,
            "profit_factor": bt.strategy_profit_factor,
            "total_trades": bt.total_trades,
            "total_cost_drag_bps": bt.total_cost_drag_bps,
        }
    else:
        status = "PENDING"
        badge = "PENDING (No Backtest)"
        backtest_summary = None

    data_start = (
        stock_df.index[0].strftime("%Y-%m-%d")
        if isinstance(stock_df.index[0], (datetime, pd.Timestamp))
        else str(stock_df.index[0])
    )
    data_end = (
        stock_df.index[-1].strftime("%Y-%m-%d")
        if isinstance(stock_df.index[-1], (datetime, pd.Timestamp))
        else str(stock_df.index[-1])
    )

    return HoldingSignalDTO(
        symbol=symbol,
        tradingsymbol=symbol,
        current_price=round(current_price, 2),
        composite_score=round(comp_score, 1),
        signal_label=sig_label,
        status=status,
        evidence_badge=badge,
        indicators=evaluations,
        monte_carlo=mc_dto,
        backtest_summary=backtest_summary,
        avg_buy_price=round(avg_buy_price, 2),
        data_start=data_start,
        data_end=data_end,
        data_points=len(stock_df),
    )


def compute_portfolio_signals(user_id: str = "default") -> list[HoldingSignalDTO]:
    """Computes quantitative signals for all active portfolio holdings."""
    try:
        holdings = get_current_holdings(user_id)
    except Exception as exc:
        logger.warning("Failed to retrieve holdings from repository: {}", exc)
        holdings = []

    if not holdings:
        return []

    signals = []
    for h in holdings:
        avg_price = float(h.average_price) if h.average_price else 0.0
        sig = compute_holding_signal(h.tradingsymbol, avg_buy_price=avg_price)
        signals.append(sig)

    return signals


def run_and_record_backtest(
    symbol: str,
    train_window: int = 252,
    test_window: int = 63,
    stock_df: pd.DataFrame | None = None,
    bench_df: pd.DataFrame | None = None,
) -> BacktestRunDTO:
    """Executes a rolling walk-forward backtest and records the run in the database."""
    s_df = stock_df
    if s_df is None:
        s_df = get_or_sync_historical_bars(symbol)

    b_df = bench_df
    if b_df is None:
        b_df = get_or_sync_historical_bars("NIFTY 50 TRI")

    run_dto = run_walk_forward_backtest(
        stock_df=s_df,
        bench_df=b_df,
        train_window=train_window,
        test_window=test_window,
        tradingsymbol=symbol,
    )

    try:
        record_backtest_run(run_dto)
        logger.info("Recorded backtest run for {}: status={}", symbol, run_dto.status)
    except Exception as exc:
        logger.warning("Failed to persist backtest run for {}: {}", symbol, exc)

    return run_dto


def record_eod_signals(
    user_id: str = "default", snapshot_date: date | None = None
) -> list[SignalSnapshotDTO]:
    """
    Captures and persists end-of-day signal snapshots for all user holdings.
    Runs daily after market close.
    """
    snap_date = snapshot_date or date.today()
    try:
        holdings = get_current_holdings(user_id)
    except Exception as exc:
        logger.warning("Failed to fetch holdings for EOD signal recording: {}", exc)
        return []

    if not holdings:
        logger.info("No active holdings for user '{}'; skipping signal snapshot recording", user_id)
        return []

    bench_price: Decimal | None = None
    try:
        b_df = get_or_sync_historical_bars("NIFTY 50 TRI")
        if not b_df.empty and "Close" in b_df.columns:
            bench_price = Decimal(str(round(float(b_df["Close"].iloc[-1]), 2)))
    except Exception as exc:
        logger.debug("Failed to retrieve benchmark price: {}", exc)

    snapshots: list[SignalSnapshotDTO] = []
    for h in holdings:
        try:
            avg_p = float(h.average_price) if h.average_price else 0.0
            sig = compute_holding_signal(h.tradingsymbol, avg_buy_price=avg_p)

            indicators_dict = {ind.name: ind.model_dump(mode="json") for ind in sig.indicators}
            mc_dict = sig.monte_carlo.model_dump(mode="json") if sig.monte_carlo else {}

            cur_p = (
                Decimal(str(sig.current_price))
                if sig.current_price > 0
                else (h.last_price or h.average_price)
            )
            comp_score = Decimal(str(sig.composite_score))

            create_dto = CreateSignalSnapshotDTO(
                snapshot_date=snap_date,
                user_id=user_id,
                tradingsymbol=h.tradingsymbol,
                model_version="v1.0.0",
                current_price=cur_p,
                benchmark_price=bench_price,
                composite_score=comp_score,
                signal_label=sig.signal_label,
                status=sig.status,
                indicators=indicators_dict,
                monte_carlo=mc_dict,
            )

            saved = record_signal_snapshot(create_dto)
            snapshots.append(saved)
        except Exception as exc:
            logger.error("Failed to record EOD signal snapshot for {}: {}", h.tradingsymbol, exc)

    logger.info(
        "Successfully recorded {} EOD signal snapshots for user '{}'", len(snapshots), user_id
    )
    return snapshots


def mature_forward_returns(
    user_id: str = "default", as_of_date: date | None = None
) -> dict[str, int]:
    """
    Evaluates realized forward returns (5d, 20d, 60d) for pending signal snapshots.
    Updates database with realized stock return, benchmark return, and excess alpha.
    """
    matured = {"5d": 0, "20d": 0, "60d": 0}

    for horizon in (5, 20, 60):
        h_key = f"{horizon}d"
        try:
            pending = get_pending_forward_return_snapshots(horizon_days=horizon)
        except Exception as exc:
            logger.warning("Failed to query pending forward snapshots for {}: {}", h_key, exc)
            continue

        user_pending = [s for s in pending if s.user_id == user_id]
        if not user_pending:
            continue

        for snapshot in user_pending:
            try:
                stock_df = get_or_sync_historical_bars(snapshot.tradingsymbol)
                if stock_df.empty or len(stock_df) < horizon + 1:
                    continue

                # Slice starting at or after snapshot_date
                post_snap_stock = stock_df[stock_df.index >= pd.Timestamp(snapshot.snapshot_date)]
                if len(post_snap_stock) < horizon + 1:
                    continue

                base_price = float(snapshot.current_price)
                if base_price <= 0:
                    continue

                target_stock_bar = post_snap_stock.iloc[horizon]
                realized_stock_price = float(target_stock_bar["Close"])
                stock_return = (realized_stock_price - base_price) / base_price

                # Benchmark forward return
                bench_return = 0.0
                try:
                    bench_df = get_or_sync_historical_bars("NIFTY 50 TRI")
                    post_snap_bench = bench_df[
                        bench_df.index >= pd.Timestamp(snapshot.snapshot_date)
                    ]
                    if len(post_snap_bench) >= horizon + 1 and snapshot.benchmark_price:
                        base_bench = float(snapshot.benchmark_price)
                        realized_bench_price = float(post_snap_bench.iloc[horizon]["Close"])
                        if base_bench > 0:
                            bench_return = (realized_bench_price - base_bench) / base_bench
                except Exception as b_exc:
                    logger.debug(
                        "Benchmark return calculation failed for {}: {}", snapshot.id, b_exc
                    )

                excess_return = stock_return - bench_return

                update_signal_forward_returns(
                    snapshot_id=snapshot.id,
                    horizon=h_key,
                    stock_return=round(stock_return, 6),
                    bench_return=round(bench_return, 6),
                    excess_return=round(excess_return, 6),
                )
                matured[h_key] += 1
            except Exception as exc:
                logger.error(
                    "Failed maturing {} forward return for snapshot {}: {}",
                    h_key,
                    snapshot.id,
                    exc,
                )

    logger.info("Forward return maturation complete: {}", matured)
    return matured
