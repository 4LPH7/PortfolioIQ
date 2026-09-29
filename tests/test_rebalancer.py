"""
Tests for Rebalancer data structures, DTO compatibility, and evidence gating integration.
Phase 5: Make the Signal Engine Evidence-Based (Plan 05-06)
"""

from decimal import Decimal
from unittest.mock import patch

from src.analytics.rebalancer import (
    OrderReason,
    OrderSide,
    RebalanceOrder,
    RebalancePlan,
    generate_rebalance_orders,
    resolve_evidence_status,
)
from src.analytics.valuator import PortfolioValuation
from src.models.dtos import HoldingSignalDTO


class TestRebalancerStructures:
    """Verifies that RebalanceOrder and RebalancePlan properly expose evidence gating fields."""

    def test_rebalance_order_default_evidence_fields(self):
        order = RebalanceOrder(
            tradingsymbol="INFY",
            exchange="NSE",
            instrument_token=408065,
            side=OrderSide.BUY,
            quantity=10,
            estimated_price=Decimal("1500.00"),
        )
        assert order.evidence_status == "PENDING"
        assert order.evidence_badge == ""
        assert order.reason == OrderReason.SECTOR_DRIFT
        assert order.estimated_value == Decimal("15000.00")

    def test_rebalance_order_custom_evidence_fields(self):
        order = RebalanceOrder(
            tradingsymbol="TCS",
            exchange="NSE",
            instrument_token=2953217,
            side=OrderSide.BUY,
            quantity=5,
            estimated_price=Decimal("3500.00"),
            reason=OrderReason.TACTICAL_SIGNAL,
            evidence_status="PROVEN_EDGE",
            evidence_badge="PROVEN EDGE (Alpha: +12.5%)",
        )
        assert order.reason == OrderReason.TACTICAL_SIGNAL
        assert order.evidence_status == "PROVEN_EDGE"
        assert order.evidence_badge == "PROVEN EDGE (Alpha: +12.5%)"

    def test_rebalance_plan_default_noise_counter(self):
        plan = RebalancePlan()
        assert plan.orders_suppressed_unproven_noise == 0
        assert len(plan.orders) == 0

    def test_order_reason_enum_contains_tactical_signal(self):
        assert OrderReason.TACTICAL_SIGNAL == "TACTICAL_SIGNAL"
        assert "TACTICAL_SIGNAL" in [r.value for r in OrderReason]

    def test_resolve_evidence_status_from_signal_lookup(self):
        sig = HoldingSignalDTO(
            symbol="INFY",
            tradingsymbol="INFY",
            current_price=1500.0,
            composite_score=72.0,
            signal_label="BUY",
            status="PROVEN_EDGE",
            evidence_badge="PROVEN EDGE (IC: +0.08)",
        )
        status, badge = resolve_evidence_status("INFY", signal_lookup={"INFY": sig})
        assert status == "PROVEN_EDGE"
        assert badge == "PROVEN EDGE (IC: +0.08)"

    def test_resolve_evidence_status_fallback(self):
        status, badge = resolve_evidence_status("UNKNOWN")
        assert status == "PENDING"
        assert badge == "PENDING (No Backtest)"

    def test_generate_rebalance_orders_convenience_alias(self):
        val = PortfolioValuation(
            user_id="default",
            holdings=[],
            total_aum=Decimal("100000.00"),
            available_cash=Decimal("20000.00"),
            net_worth=Decimal("120000.00"),
        )
        with (
            patch("src.analytics.rebalancer.compute_portfolio_valuation", return_value=val),
            patch("src.analytics.rebalancer.detect_drift", return_value=[]),
        ):
            orders = generate_rebalance_orders()
            assert isinstance(orders, list)
            assert len(orders) == 0
