"""
Tests for Rebalancer Evidence Hurdle Gate & Fail-Closed Order Suppression
(src/analytics/rebalancer.py).
Phase 5: Make the Signal Engine Evidence-Based (Plan 05-06)

Validates:
- 4 Quantitative evidence hurdles: predictive power, stock timing, market alpha, trade quality.
- Classification into PROVEN_EDGE vs UNPROVEN_NOISE vs PENDING.
- Fail-closed suppression of tactical trade proposals for UNPROVEN_NOISE signals.
- Preservation of strategic drift orders with evidence annotation badges.
- Accurate tracking of orders_suppressed_unproven_noise counter.
- Full serialization of evidence metadata in get_rebalance_summary.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from src.analytics.drift_detector import DriftDirection, DriftSignal, DriftType
from src.analytics.rebalancer import (
    OrderReason,
    OrderSide,
    evaluate_evidence_hurdles,
    generate_rebalance_plan,
    get_rebalance_summary,
)
from src.analytics.valuator import HoldingValuation, PortfolioValuation
from src.models.dtos import BacktestRunDTO, HoldingSignalDTO, IndicatorEvaluationDTO


def _mock_valuation(
    holdings: list[HoldingValuation] | None = None,
    available_cash: Decimal = Decimal("100000.00"),
    total_aum: Decimal = Decimal("500000.00"),
) -> PortfolioValuation:
    return PortfolioValuation(
        user_id="default",
        holdings=holdings or [],
        total_aum=total_aum,
        available_cash=available_cash,
        net_worth=total_aum + available_cash,
    )


def _make_proven_backtest(
    symbol: str = "TCS",
    strategy_cagr: float = 0.28,
    stock_cagr: float = 0.15,
    benchmark_cagr: float = 0.12,
    win_rate: float = 0.60,
    profit_factor: float = 2.1,
    total_trades: int = 12,
) -> BacktestRunDTO:
    """Creates a backtest run that passes all 4 quantitative evidence hurdles."""
    ind = IndicatorEvaluationDTO(
        name="RSI",
        mean_ic=0.082,
        p_value=0.012,
        is_pruned=False,
        weight=1.0,
    )
    return BacktestRunDTO(
        run_id=str(uuid.uuid4()),
        tradingsymbol=symbol,
        train_start_date=date(2024, 1, 1),
        train_end_date=date(2024, 12, 31),
        test_start_date=date(2025, 1, 1),
        test_end_date=date(2025, 3, 31),
        strategy_cagr=strategy_cagr,
        stock_cagr=stock_cagr,
        benchmark_cagr=benchmark_cagr,
        excess_cagr_vs_stock=strategy_cagr - stock_cagr,
        excess_cagr_vs_benchmark=strategy_cagr - benchmark_cagr,
        strategy_win_rate=win_rate,
        strategy_profit_factor=profit_factor,
        total_trades=total_trades,
        indicators=[ind],
        status="PROVEN_EDGE",
        passed_hurdle=True,
    )


class TestEvidenceHurdleClassifier:
    """Validates the 4 quantitative evidence hurdles."""

    def test_evaluate_evidence_hurdles_none_backtest(self):
        status, badge, checks = evaluate_evidence_hurdles(None)
        assert status == "PENDING"
        assert badge == "PENDING (No Backtest)"
        assert not any(checks.values())

    def test_all_hurdles_passed_yields_proven_edge(self):
        bt = _make_proven_backtest()
        status, badge, checks = evaluate_evidence_hurdles(bt)

        assert status == "PROVEN_EDGE"
        assert "PROVEN EDGE" in badge
        assert "Alpha: +16.0%" in badge
        assert checks["predictive_power"] is True
        assert checks["stock_timing"] is True
        assert checks["market_alpha"] is True
        assert checks["trade_quality"] is True

    def test_hurdle_1_predictive_power_failure(self):
        # All indicators are pruned due to non-positive IC
        bt = _make_proven_backtest()
        bt.indicators = [
            IndicatorEvaluationDTO(
                name="MACD", mean_ic=-0.02, p_value=0.20, is_pruned=True, weight=0.0
            )
        ]
        status, badge, checks = evaluate_evidence_hurdles(bt)
        assert status == "UNPROVEN_NOISE"
        assert checks["predictive_power"] is False
        assert "predictive_power" in badge

    def test_hurdle_2_stock_timing_failure(self):
        # Strategy underperforms holding the stock directly
        bt = _make_proven_backtest(strategy_cagr=0.10, stock_cagr=0.20)
        status, badge, checks = evaluate_evidence_hurdles(bt)
        assert status == "UNPROVEN_NOISE"
        assert checks["stock_timing"] is False
        assert "stock_timing" in badge

    def test_hurdle_3_market_excess_alpha_failure(self):
        # Strategy underperforms NIFTY 50 TRI benchmark
        bt = _make_proven_backtest(strategy_cagr=0.10, stock_cagr=0.05, benchmark_cagr=0.15)
        status, badge, checks = evaluate_evidence_hurdles(bt)
        assert status == "UNPROVEN_NOISE"
        assert checks["market_alpha"] is False
        assert "market_alpha" in badge

    def test_hurdle_4_trade_quality_failure(self):
        # Win rate below 50%
        bt_low_win = _make_proven_backtest(win_rate=0.40)
        status, _, checks = evaluate_evidence_hurdles(bt_low_win)
        assert status == "UNPROVEN_NOISE"
        assert checks["trade_quality"] is False

        # Profit factor <= 1.0
        bt_low_pf = _make_proven_backtest(profit_factor=0.95)
        status, _, checks = evaluate_evidence_hurdles(bt_low_pf)
        assert status == "UNPROVEN_NOISE"
        assert checks["trade_quality"] is False

        # Fewer than 5 trades
        bt_few_trades = _make_proven_backtest(total_trades=3)
        status, _, checks = evaluate_evidence_hurdles(bt_few_trades)
        assert status == "UNPROVEN_NOISE"
        assert checks["trade_quality"] is False


class TestRebalancerEvidenceGate:
    """Validates rebalancer fail-closed order suppression on unproven noise signals."""

    def test_tactical_order_suppressed_for_unproven_noise(self):
        """Verify tactical buy proposal is suppressed and counted when signal is UNPROVEN_NOISE."""
        unproven_sig = HoldingSignalDTO(
            symbol="INFY",
            tradingsymbol="INFY",
            current_price=1500.0,
            composite_score=75.0,
            signal_label="BUY",
            status="UNPROVEN_NOISE",
            evidence_badge="UNPROVEN NOISE (Failed: predictive_power)",
        )

        val = _mock_valuation()
        with (
            patch("src.analytics.rebalancer.compute_portfolio_valuation", return_value=val),
            patch("src.analytics.rebalancer.detect_drift", return_value=[]),
            patch(
                "src.analytics.rebalancer._get_holding_details",
                return_value={
                    "instrument_token": 408065,
                    "exchange": "NSE",
                    "quantity": 20,
                    "current_price": Decimal("1500.00"),
                    "lot_size": 1,
                },
            ),
        ):
            plan = generate_rebalance_plan(tactical_signals=[unproven_sig])
            assert len(plan.orders) == 0
            assert plan.orders_suppressed_unproven_noise == 1

    def test_tactical_order_approved_for_proven_edge(self):
        """Verify tactical buy proposal is approved when signal has PROVEN_EDGE."""
        proven_sig = HoldingSignalDTO(
            symbol="TCS",
            tradingsymbol="TCS",
            current_price=3500.0,
            composite_score=78.0,
            signal_label="BUY",
            status="PROVEN_EDGE",
            evidence_badge="PROVEN EDGE (Alpha: +16.0%, Trades: 12)",
        )

        val = _mock_valuation(available_cash=Decimal("100000.00"), total_aum=Decimal("500000.00"))
        with (
            patch("src.analytics.rebalancer.compute_portfolio_valuation", return_value=val),
            patch("src.analytics.rebalancer.detect_drift", return_value=[]),
            patch(
                "src.analytics.rebalancer._get_holding_details",
                return_value={
                    "instrument_token": 2953217,
                    "exchange": "NSE",
                    "quantity": 10,
                    "current_price": Decimal("3500.00"),
                    "lot_size": 1,
                },
            ),
        ):
            plan = generate_rebalance_plan(tactical_signals=[proven_sig])
            assert len(plan.orders) == 1
            assert plan.orders_suppressed_unproven_noise == 0

            order = plan.orders[0]
            assert order.tradingsymbol == "TCS"
            assert order.side == OrderSide.BUY
            assert order.reason == OrderReason.TACTICAL_SIGNAL
            assert order.evidence_status == "PROVEN_EDGE"
            assert "PROVEN EDGE" in order.evidence_badge

    def test_strategic_drift_orders_annotated_with_evidence_badges(self):
        """Verify core portfolio drift rebalance orders are preserved and annotated."""
        drift_signal = DriftSignal(
            drift_type=DriftType.HOLDING,
            name="RELIANCE",
            current_weight_pct=Decimal("15.0"),
            target_weight_pct=Decimal("10.0"),
            drift_pct=Decimal("5.0"),
            threshold_pct=Decimal("1.0"),
            direction=DriftDirection.OVERWEIGHT,
            severity="HIGH",
            rebalance_amount=Decimal("-25000.00"),
        )

        val = _mock_valuation(total_aum=Decimal("500000.00"))
        bt = _make_proven_backtest(symbol="RELIANCE")

        with (
            patch("src.analytics.rebalancer.compute_portfolio_valuation", return_value=val),
            patch("src.analytics.rebalancer.detect_drift", return_value=[drift_signal]),
            patch(
                "src.analytics.rebalancer._get_holding_details",
                return_value={
                    "instrument_token": 738561,
                    "exchange": "NSE",
                    "quantity": 50,
                    "t1_quantity": 0,
                    "average_price": Decimal("2500.00"),
                    "current_price": Decimal("2500.00"),
                    "lot_size": 1,
                },
            ),
        ):
            plan = generate_rebalance_plan(backtest_lookup={"RELIANCE": bt})
            assert len(plan.orders) == 1
            order = plan.orders[0]
            assert order.tradingsymbol == "RELIANCE"
            assert order.reason == OrderReason.HOLDING_DRIFT
            assert order.evidence_status == "PROVEN_EDGE"
            assert "PROVEN EDGE" in order.evidence_badge

    def test_drift_buy_suppressed_when_flag_enabled(self):
        """Verify drift buy orders can also be suppressed when suppress_unproven_buys=True."""
        underweight_drift = DriftSignal(
            drift_type=DriftType.HOLDING,
            name="WIPRO",
            current_weight_pct=Decimal("2.0"),
            target_weight_pct=Decimal("5.0"),
            drift_pct=Decimal("-3.0"),
            threshold_pct=Decimal("1.0"),
            direction=DriftDirection.UNDERWEIGHT,
            severity="HIGH",
            rebalance_amount=Decimal("15000.00"),
        )

        unproven_bt = _make_proven_backtest(symbol="WIPRO", win_rate=0.30)  # fails trade quality
        val = _mock_valuation(available_cash=Decimal("50000.00"), total_aum=Decimal("500000.00"))

        with (
            patch("src.analytics.rebalancer.compute_portfolio_valuation", return_value=val),
            patch("src.analytics.rebalancer.detect_drift", return_value=[underweight_drift]),
            patch(
                "src.analytics.rebalancer._get_holding_details",
                return_value={
                    "instrument_token": 969473,
                    "exchange": "NSE",
                    "quantity": 10,
                    "current_price": Decimal("500.00"),
                    "lot_size": 1,
                },
            ),
        ):
            plan = generate_rebalance_plan(
                backtest_lookup={"WIPRO": unproven_bt},
                suppress_unproven_buys=True,
            )
            assert len(plan.orders) == 0
            assert plan.orders_suppressed_unproven_noise == 1

    def test_summary_serialization_includes_evidence_fields(self):
        """Verify get_rebalance_summary includes orders_suppressed_unproven_noise and order badges."""
        val = _mock_valuation()
        unproven_sig = HoldingSignalDTO(
            symbol="HDFC",
            tradingsymbol="HDFC",
            current_price=1600.0,
            composite_score=70.0,
            signal_label="BUY",
            status="UNPROVEN_NOISE",
        )

        with (
            patch("src.analytics.rebalancer.compute_portfolio_valuation", return_value=val),
            patch("src.analytics.rebalancer.detect_drift", return_value=[]),
        ):
            summary = get_rebalance_summary(tactical_signals=[unproven_sig])
            assert "orders_suppressed_unproven_noise" in summary
            assert summary["orders_suppressed_unproven_noise"] == 1
