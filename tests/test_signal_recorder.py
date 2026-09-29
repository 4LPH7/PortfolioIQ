"""
Tests for Quantitative Signal Recorder & Forward Return Maturation Engine
(src/analytics/signal_recorder.py, src/scheduler/jobs.py).
Phase 5: Make the Signal Engine Evidence-Based (Plan 05-07)

Validates:
- Quantitative signal computation with dynamic weights and calibrated Monte Carlo.
- EOD snapshot recording in signal_snapshots.
- Forward return maturation (5d, 20d, 60d) and database updates.
- APScheduler jobs execution and market day gating.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.analytics.signal_recorder import (
    compute_holding_signal,
    compute_portfolio_signals,
    mature_forward_returns,
    record_eod_signals,
)
from src.models.dtos import (
    BacktestRunDTO,
    HoldingDTO,
    HoldingSignalDTO,
    IndicatorEvaluationDTO,
    SignalSnapshotDTO,
)
from src.scheduler.jobs import (
    create_scheduler,
    mature_forward_returns_job,
    record_daily_signals_job,
)


@pytest.fixture
def mock_ohlcv_df() -> pd.DataFrame:
    """Generate 100 days of synthetic price data."""
    dates = pd.date_range("2025-01-01", periods=100, freq="B")
    rng = np.random.default_rng(42)
    shocks = rng.normal(0.0005, 0.015, size=100)
    prices = 1000.0 * np.exp(np.cumsum(shocks))
    return pd.DataFrame(
        {
            "Open": prices * 0.99,
            "High": prices * 1.01,
            "Low": prices * 0.98,
            "Close": prices,
            "Volume": 100000,
        },
        index=dates,
    )


class TestComputeHoldingSignal:
    """Tests for individual holding signal evaluation."""

    def test_compute_holding_signal_success(self, mock_ohlcv_df):
        with (
            patch(
                "src.analytics.signal_recorder.get_or_sync_historical_bars",
                return_value=mock_ohlcv_df,
            ),
            patch("src.analytics.signal_recorder.get_latest_backtest_run", return_value=None),
        ):
            sig = compute_holding_signal("INFY", avg_buy_price=950.0)

            assert isinstance(sig, HoldingSignalDTO)
            assert sig.symbol == "INFY"
            assert sig.current_price > 0
            assert sig.avg_buy_price == 950.0
            assert 0.0 <= sig.composite_score <= 100.0
            assert sig.signal_label in ("STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL")
            assert sig.status == "PENDING"  # No backtest run yet
            assert "PENDING" in sig.evidence_badge

            # Calibrated Monte Carlo must be populated and have no point estimate
            assert sig.monte_carlo is not None
            assert sig.monte_carlo.p10 < sig.monte_carlo.p50 < sig.monte_carlo.p90
            mc_dump = sig.monte_carlo.model_dump()
            assert "target_price" not in mc_dump

            # Indicators must have evaluations
            assert len(sig.indicators) > 0

    def test_compute_holding_signal_insufficient_bars(self):
        short_df = pd.DataFrame({"Close": [100.0, 101.0]})
        with patch(
            "src.analytics.signal_recorder.get_or_sync_historical_bars", return_value=short_df
        ):
            sig = compute_holding_signal("TCS")
            assert sig.status == "PENDING"
            assert sig.data_points == 2
            assert sig.evidence_badge == "PENDING (Insufficient Bars)"

    def test_compute_holding_signal_with_proven_backtest(self, mock_ohlcv_df):
        proven_bt = BacktestRunDTO(
            run_id=str(uuid.uuid4()),
            tradingsymbol="RELIANCE",
            train_start_date=date(2024, 1, 1),
            train_end_date=date(2024, 12, 31),
            test_start_date=date(2025, 1, 1),
            test_end_date=date(2025, 3, 31),
            strategy_cagr=0.25,
            stock_cagr=0.15,
            benchmark_cagr=0.12,
            strategy_win_rate=0.55,
            strategy_profit_factor=1.8,
            total_trades=10,
            indicators=[
                IndicatorEvaluationDTO(
                    name="RSI", mean_ic=0.06, p_value=0.02, is_pruned=False, weight=1.0
                )
            ],
            status="PROVEN_EDGE",
            passed_hurdle=True,
        )

        with (
            patch(
                "src.analytics.signal_recorder.get_or_sync_historical_bars",
                return_value=mock_ohlcv_df,
            ),
            patch("src.analytics.signal_recorder.get_latest_backtest_run", return_value=proven_bt),
        ):
            sig = compute_holding_signal("RELIANCE")
            assert sig.status == "PROVEN_EDGE"
            assert "PROVEN EDGE" in sig.evidence_badge
            assert sig.backtest_summary is not None
            assert sig.backtest_summary["win_rate"] == 0.55


class TestPortfolioSignalComputation:
    """Tests for bulk portfolio signal evaluation."""

    def test_compute_portfolio_signals_empty(self):
        with patch("src.analytics.signal_recorder.get_current_holdings", return_value=[]):
            signals = compute_portfolio_signals()
            assert signals == []

    def test_compute_portfolio_signals_success(self, mock_ohlcv_df):
        h1 = HoldingDTO(
            instrument_token=408065,
            tradingsymbol="INFY",
            quantity=10,
            average_price=Decimal("1500.00"),
        )
        with (
            patch("src.analytics.signal_recorder.get_current_holdings", return_value=[h1]),
            patch(
                "src.analytics.signal_recorder.get_or_sync_historical_bars",
                return_value=mock_ohlcv_df,
            ),
            patch("src.analytics.signal_recorder.get_latest_backtest_run", return_value=None),
        ):
            signals = compute_portfolio_signals()
            assert len(signals) == 1
            assert signals[0].tradingsymbol == "INFY"


class TestEODSignalRecording:
    """Tests for record_eod_signals persistence."""

    def test_record_eod_signals_success(self, mock_ohlcv_df):
        h1 = HoldingDTO(
            instrument_token=408065,
            tradingsymbol="INFY",
            quantity=10,
            average_price=Decimal("1500.00"),
        )
        saved_snapshot = SignalSnapshotDTO(
            id=101,
            snapshot_date=date(2025, 6, 1),
            user_id="default",
            tradingsymbol="INFY",
            model_version="v1.0.0",
            current_price=Decimal("1520.00"),
            composite_score=Decimal("65.0"),
            signal_label="BUY",
            status="PENDING",
        )

        with (
            patch("src.analytics.signal_recorder.get_current_holdings", return_value=[h1]),
            patch(
                "src.analytics.signal_recorder.get_or_sync_historical_bars",
                return_value=mock_ohlcv_df,
            ),
            patch("src.analytics.signal_recorder.get_latest_backtest_run", return_value=None),
            patch(
                "src.analytics.signal_recorder.record_signal_snapshot",
                return_value=saved_snapshot,
            ),
        ):
            snapshots = record_eod_signals(snapshot_date=date(2025, 6, 1))
            assert len(snapshots) == 1
            assert snapshots[0].tradingsymbol == "INFY"
            assert snapshots[0].id == 101


class TestForwardReturnMaturation:
    """Tests for mature_forward_returns."""

    def test_mature_forward_returns_success(self, mock_ohlcv_df):
        pending_snap = SignalSnapshotDTO(
            id=55,
            snapshot_date=date(2025, 1, 2),
            user_id="default",
            tradingsymbol="INFY",
            model_version="v1.0.0",
            current_price=Decimal("1000.00"),
            benchmark_price=Decimal("20000.00"),
            composite_score=Decimal("70.0"),
            signal_label="BUY",
            status="PROVEN_EDGE",
        )

        with (
            patch(
                "src.analytics.signal_recorder.get_pending_forward_return_snapshots",
                side_effect=lambda horizon_days: [pending_snap] if horizon_days == 5 else [],
            ),
            patch(
                "src.analytics.signal_recorder.get_or_sync_historical_bars",
                return_value=mock_ohlcv_df,
            ),
            patch("src.analytics.signal_recorder.update_signal_forward_returns") as mock_update,
        ):
            matured = mature_forward_returns()
            assert matured["5d"] == 1
            assert matured["20d"] == 0
            assert matured["60d"] == 0
            mock_update.assert_called_once()
            call_kwargs = mock_update.call_args[1]
            assert call_kwargs["snapshot_id"] == 55
            assert call_kwargs["horizon"] == "5d"
            assert "stock_return" in call_kwargs


class TestSchedulerJobsIntegration:
    """Tests for scheduler job functions and trigger registration."""

    def test_record_daily_signals_job_non_market_day(self):
        with patch("src.ingestion.market_hours.is_market_day", return_value=False):
            res = record_daily_signals_job()
            assert res == []

    def test_mature_forward_returns_job_non_market_day(self):
        with patch("src.ingestion.market_hours.is_market_day", return_value=False):
            res = mature_forward_returns_job()
            assert res == {}

    def test_create_scheduler_contains_signal_jobs(self):
        sched = create_scheduler()
        job_ids = [j.id for j in sched.get_jobs()]
        assert "daily_signals" in job_ids
        assert "mature_forward_returns" in job_ids
