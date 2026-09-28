"""
Tests for src/execution/
Comprehensive test suite for gatekeeper, order_router, and all pre-flight validators:
margin, slippage, concentration, and duplicate checks.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.analytics.rebalancer import OrderReason, OrderSide, RebalanceOrder, RebalancePlan
from src.execution.gatekeeper import (
    GatekeeperResult,
    ValidationReport,
    ValidationResult,
    _log_validation,
    get_gatekeeper_summary,
    validate_order,
    validate_plan,
)
from src.execution.order_router import _log_order_to_audit_trail, execute_plan, place_order
from src.execution.validators.concentration_check import validate_concentration
from src.execution.validators.duplicate_check import validate_no_duplicate
from src.execution.validators.margin_check import validate_margin
from src.execution.validators.slippage_check import validate_slippage


@pytest.fixture
def sample_buy_order() -> RebalanceOrder:
    return RebalanceOrder(
        tradingsymbol="INFY",
        exchange="NSE",
        instrument_token=408065,
        side=OrderSide.BUY,
        quantity=10,
        estimated_price=Decimal("1500.00"),
        reason=OrderReason.SECTOR_DRIFT,
    )


@pytest.fixture
def sample_sell_order() -> RebalanceOrder:
    return RebalanceOrder(
        tradingsymbol="RELIANCE",
        exchange="NSE",
        instrument_token=738561,
        side=OrderSide.SELL,
        quantity=5,
        estimated_price=Decimal("2500.00"),
        reason=OrderReason.CONCENTRATION_BREACH,
    )


# =====================================================================
# 1. Validator Tests
# =====================================================================


class TestMarginValidator:
    """Tests for src/execution/validators/margin_check.py."""

    @patch("src.execution.validators.margin_check.execute_sql")
    def test_no_margin_data_fails(self, mock_sql, sample_buy_order) -> None:
        mock_sql.return_value = []
        passed, msg = validate_margin(sample_buy_order)
        assert passed is False
        assert "No margin data available" in msg

    @patch("src.execution.validators.margin_check.execute_sql")
    def test_insufficient_margin_fails(self, mock_sql, sample_buy_order) -> None:
        mock_sql.return_value = [{"available_cash": "5000.00"}]
        # Required is 10 * 1500 = 15000
        passed, msg = validate_margin(sample_buy_order)
        assert passed is False
        assert "Insufficient margin" in msg
        assert "Shortfall" in msg

    @patch("src.execution.validators.margin_check.execute_sql")
    def test_sufficient_margin_passes(self, mock_sql, sample_buy_order) -> None:
        mock_sql.return_value = [{"available_cash": "20000.00"}]
        passed, msg = validate_margin(sample_buy_order)
        assert passed is True
        assert "Margin OK" in msg


class TestSlippageValidator:
    """Tests for src/execution/validators/slippage_check.py."""

    @patch("src.execution.validators.slippage_check.execute_sql")
    def test_slippage_rejects_inactive_instrument(self, mock_sql, sample_buy_order) -> None:
        # Mock instrument_master returning inactive
        mock_sql.return_value = [{"is_active": False}]
        passed, msg = validate_slippage(sample_buy_order)
        assert passed is False
        assert "inactive or delisted" in msg

    @patch("src.execution.validators.slippage_check.is_market_open", return_value=False)
    @patch("src.execution.validators.slippage_check.execute_sql")
    def test_slippage_missing_price_market_closed_passes(
        self, mock_sql, mock_open, sample_buy_order
    ) -> None:
        mock_sql.side_effect = [
            [{"is_active": True}],  # instrument_master
            [{"key": "slippage_bound_pct", "value": "2.0"}],  # config
            [],  # live_prices
        ]
        passed, msg = validate_slippage(sample_buy_order)
        assert passed is True
        assert "No live price outside market hours" in msg

    @patch("src.execution.validators.slippage_check._refresh_on_demand_price", return_value=None)
    @patch("src.execution.validators.slippage_check.is_market_open", return_value=True)
    @patch("src.execution.validators.slippage_check.execute_sql")
    def test_slippage_missing_price_market_open_broker_fails_closed(
        self, mock_sql, mock_open, mock_refresh, sample_buy_order
    ) -> None:
        mock_sql.side_effect = [
            [{"is_active": True}],  # instrument_master
            [{"key": "slippage_bound_pct", "value": "2.0"}],  # config
            [],  # live_prices
        ]
        passed, msg = validate_slippage(sample_buy_order)
        assert passed is False
        assert "on-demand broker refresh failed" in msg
        mock_refresh.assert_called_once_with(sample_buy_order)

    @patch("src.execution.validators.slippage_check.now_ist")
    @patch("src.execution.validators.slippage_check._refresh_on_demand_price")
    @patch("src.execution.validators.slippage_check.is_market_open", return_value=True)
    @patch("src.execution.validators.slippage_check.execute_sql")
    def test_stale_live_price_passes(
        self, mock_sql, mock_open, mock_refresh, mock_now, sample_buy_order
    ) -> None:
        from datetime import datetime

        from src.ingestion.market_hours import IST

        mock_now.return_value = IST.localize(datetime(2026, 9, 28, 10, 15, 0))

        mock_sql.side_effect = [
            [{"is_active": True}],  # instrument_master
            [
                {"key": "slippage_bound_pct", "value": "2.0"},
                {"key": "price_staleness_threshold_sec", "value": "60"},
            ],  # config
            [
                {
                    "last_price": "1600.00",
                    "is_stale": False,
                    "last_updated": datetime(2026, 9, 28, 10, 10, 0),
                }
            ],  # live_prices (stale)
        ]
        mock_refresh.return_value = Decimal("1515.00")  # Fresh price, 1% slippage

        passed, msg = validate_slippage(sample_buy_order)
        assert passed is True
        assert "Slippage OK" in msg
        mock_refresh.assert_called_once_with(sample_buy_order)

    @patch("src.execution.validators.slippage_check.now_ist")
    @patch("src.execution.validators.slippage_check._refresh_on_demand_price", return_value=None)
    @patch("src.execution.validators.slippage_check.is_market_open", return_value=True)
    @patch("src.execution.validators.slippage_check.execute_sql")
    def test_stale_live_price_fails_closed_when_refresh_fails(
        self, mock_sql, mock_open, mock_refresh, mock_now, sample_buy_order
    ) -> None:
        from datetime import datetime

        from src.ingestion.market_hours import IST

        mock_now.return_value = IST.localize(datetime(2026, 9, 28, 10, 15, 0))

        mock_sql.side_effect = [
            [{"is_active": True}],  # instrument_master
            [
                {"key": "slippage_bound_pct", "value": "2.0"},
                {"key": "price_staleness_threshold_sec", "value": "60"},
            ],  # config
            [
                {
                    "last_price": "1600.00",
                    "is_stale": False,
                    "last_updated": datetime(2026, 9, 28, 10, 10, 0),
                }
            ],  # live_prices (stale)
        ]

        passed, msg = validate_slippage(sample_buy_order)
        assert passed is False
        assert "on-demand broker refresh failed" in msg

    @patch("src.execution.validators.slippage_check.now_ist")
    @patch("src.execution.validators.slippage_check.is_market_open", return_value=True)
    @patch("src.execution.validators.slippage_check.execute_sql")
    def test_zero_or_negative_price_fails(
        self, mock_sql, mock_open, mock_now, sample_buy_order
    ) -> None:
        from datetime import datetime

        from src.ingestion.market_hours import IST

        mock_now.return_value = IST.localize(datetime(2026, 9, 28, 10, 15, 0))
        mock_sql.side_effect = [
            [{"is_active": True}],
            [{"key": "slippage_bound_pct", "value": "2.0"}],
            [
                {
                    "last_price": "0.00",
                    "is_stale": False,
                    "last_updated": datetime(2026, 9, 28, 10, 14, 50),
                }
            ],  # fresh
        ]
        passed, msg = validate_slippage(sample_buy_order)
        assert passed is False
        assert "zero or unavailable" in msg

    @patch("src.execution.validators.slippage_check.now_ist")
    @patch("src.execution.validators.slippage_check.is_market_open", return_value=True)
    @patch("src.execution.validators.slippage_check.execute_sql")
    def test_slippage_within_bound_passes(
        self, mock_sql, mock_open, mock_now, sample_buy_order
    ) -> None:
        from datetime import datetime

        from src.ingestion.market_hours import IST

        mock_now.return_value = IST.localize(datetime(2026, 9, 28, 10, 15, 0))
        mock_sql.side_effect = [
            [{"is_active": True}],
            [{"key": "slippage_bound_pct", "value": "2.0"}],
            [
                {
                    "last_price": "1515.00",
                    "is_stale": False,
                    "last_updated": datetime(2026, 9, 28, 10, 14, 50),
                }
            ],  # fresh
        ]
        passed, msg = validate_slippage(sample_buy_order)
        assert passed is True
        assert "Slippage OK" in msg

    @patch("src.execution.validators.slippage_check.now_ist")
    @patch("src.execution.validators.slippage_check.is_market_open", return_value=True)
    @patch("src.execution.validators.slippage_check.execute_sql")
    def test_slippage_exceeds_bound_fails(
        self, mock_sql, mock_open, mock_now, sample_buy_order
    ) -> None:
        from datetime import datetime

        from src.ingestion.market_hours import IST

        mock_now.return_value = IST.localize(datetime(2026, 9, 28, 10, 15, 0))
        mock_sql.side_effect = [
            [{"is_active": True}],
            [{"key": "slippage_bound_pct", "value": "2.0"}],
            [
                {
                    "last_price": "1575.00",
                    "is_stale": False,
                    "last_updated": datetime(2026, 9, 28, 10, 14, 50),
                }
            ],  # fresh
        ]
        passed, msg = validate_slippage(sample_buy_order)
        assert passed is False
        assert "Slippage too high" in msg


class TestConcentrationValidator:
    """Tests for src/execution/validators/concentration_check.py."""

    @patch("src.execution.validators.concentration_check.execute_sql")
    def test_zero_aum_skips_check(self, mock_sql, sample_buy_order) -> None:
        mock_sql.side_effect = [
            [{"value": "15.0"}],  # limit
            [{"total_aum": 0}],  # AUM is zero
        ]
        passed, msg = validate_concentration(sample_buy_order)
        assert passed is True
        assert "AUM is zero" in msg

    @patch("src.execution.validators.concentration_check.execute_sql")
    def test_concentration_within_limit_passes(self, mock_sql, sample_buy_order) -> None:
        # Total AUM 1,000,000, current holding 50,000, order is 15,000 => new 65,000 / 1,015,000 = ~6.4%
        mock_sql.side_effect = [
            [],  # default 15.0%
            [{"total_aum": "1000000.00"}],
            [{"holding_value": "50000.00"}],
        ]
        passed, msg = validate_concentration(sample_buy_order)
        assert passed is True
        assert "Concentration OK" in msg

    @patch("src.execution.validators.concentration_check.execute_sql")
    def test_concentration_breach_fails(self, mock_sql, sample_buy_order) -> None:
        # Total AUM 50,000, current holding 10,000, order is 15,000 => new 25,000 / 65,000 = 38.4% (> 15%)
        mock_sql.side_effect = [
            [{"value": "15.0"}],
            [{"total_aum": "50000.00"}],
            [{"holding_value": "10000.00"}],
        ]
        passed, msg = validate_concentration(sample_buy_order)
        assert passed is False
        assert "Concentration breach" in msg


class TestDuplicateValidator:
    """Tests for src/execution/validators/duplicate_check.py."""

    @patch("src.execution.validators.duplicate_check.execute_sql")
    def test_no_duplicate_passes(self, mock_sql, sample_buy_order) -> None:
        mock_sql.side_effect = [
            [{"value": "300"}],  # duplicate_window_sec
            [],  # no dup rows
        ]
        passed, msg = validate_no_duplicate(sample_buy_order)
        assert passed is True
        assert "No duplicate orders" in msg

    @patch("src.execution.validators.duplicate_check.execute_sql")
    def test_duplicate_order_detected_fails(self, mock_sql, sample_buy_order) -> None:
        mock_sql.side_effect = [
            [],  # default 300
            [{"id": 42, "placed_at": "2026-09-27 10:00:00", "status": "PLACED"}],
        ]
        passed, msg = validate_no_duplicate(sample_buy_order)
        assert passed is False
        assert "Duplicate order detected" in msg
        assert "Order #42" in msg


# =====================================================================
# 2. Gatekeeper Pipeline Tests
# =====================================================================


class TestGatekeeperPipeline:
    """Tests for src/execution/gatekeeper.py order validation pipeline."""

    @patch("src.execution.gatekeeper.record_validation_check")
    @patch("src.execution.gatekeeper.record_order_attempt")
    def test_unknown_validation_check_name_raises(
        self, mock_record_attempt, mock_record_check, sample_buy_order
    ) -> None:
        mock_record_attempt.return_value = MagicMock(id=101)
        report = ValidationReport(
            order=sample_buy_order,
            result=ValidationResult.APPROVED,
            checks_passed=["UNKNOWN_CHECK_NAME"],
        )
        with pytest.raises(ValueError, match="Unknown validation check"):
            _log_validation(report)

    @patch("src.execution.gatekeeper._log_validation")
    @patch("src.execution.gatekeeper.is_market_open", return_value=False)
    def test_market_closed_rejects_order(
        self, mock_market_open, mock_log_val, sample_buy_order
    ) -> None:
        report = validate_order(sample_buy_order)
        assert report.result == ValidationResult.REJECTED
        assert "MARKET_CLOSED" in report.checks_failed
        assert "Market is currently closed" in (report.failure_reason or "")
        mock_log_val.assert_called_once_with(report)

    @patch("src.execution.gatekeeper._log_validation")
    @patch("src.execution.gatekeeper.validate_margin", return_value=(False, "Margin shortfall"))
    @patch("src.execution.gatekeeper.is_market_open", return_value=True)
    def test_margin_failure_rejects_buy_order(
        self, mock_market_open, mock_margin, mock_log_val, sample_buy_order
    ) -> None:
        report = validate_order(sample_buy_order)
        assert report.result == ValidationResult.REJECTED
        assert "MARGIN" in report.checks_failed
        assert report.failure_reason == "Margin shortfall"

    @patch("src.execution.gatekeeper._log_validation")
    @patch("src.execution.gatekeeper.validate_slippage", return_value=(False, "Slippage 4.5%"))
    @patch("src.execution.gatekeeper.validate_margin", return_value=(True, "OK"))
    @patch("src.execution.gatekeeper.is_market_open", return_value=True)
    def test_slippage_failure_rejects_order(
        self, mock_market_open, mock_margin, mock_slippage, mock_log_val, sample_buy_order
    ) -> None:
        report = validate_order(sample_buy_order)
        assert report.result == ValidationResult.REJECTED
        assert "SLIPPAGE" in report.checks_failed

    @patch("src.execution.gatekeeper._log_validation")
    @patch(
        "src.execution.gatekeeper.validate_concentration",
        return_value=(False, "Concentration breach"),
    )
    @patch("src.execution.gatekeeper.validate_slippage", return_value=(True, "OK"))
    @patch("src.execution.gatekeeper.validate_margin", return_value=(True, "OK"))
    @patch("src.execution.gatekeeper.is_market_open", return_value=True)
    def test_concentration_failure_rejects_buy_order(
        self,
        mock_market_open,
        mock_margin,
        mock_slippage,
        mock_conc,
        mock_log_val,
        sample_buy_order,
    ) -> None:
        report = validate_order(sample_buy_order)
        assert report.result == ValidationResult.REJECTED
        assert "CONCENTRATION" in report.checks_failed

    @patch("src.execution.gatekeeper._log_validation")
    @patch(
        "src.execution.gatekeeper.validate_no_duplicate", return_value=(False, "Duplicate order")
    )
    @patch("src.execution.gatekeeper.validate_concentration", return_value=(True, "OK"))
    @patch("src.execution.gatekeeper.validate_slippage", return_value=(True, "OK"))
    @patch("src.execution.gatekeeper.validate_margin", return_value=(True, "OK"))
    @patch("src.execution.gatekeeper.is_market_open", return_value=True)
    def test_duplicate_failure_rejects_order(
        self,
        mock_market_open,
        mock_margin,
        mock_slippage,
        mock_conc,
        mock_dup,
        mock_log_val,
        sample_buy_order,
    ) -> None:
        report = validate_order(sample_buy_order)
        assert report.result == ValidationResult.REJECTED
        assert "DUPLICATE" in report.checks_failed

    @patch("src.execution.gatekeeper._log_validation")
    @patch("src.execution.gatekeeper.validate_no_duplicate", return_value=(True, "OK"))
    @patch("src.execution.gatekeeper.validate_slippage", return_value=(True, "OK"))
    @patch("src.execution.gatekeeper.is_market_open", return_value=True)
    def test_sell_order_skips_margin_and_concentration(
        self,
        mock_market_open,
        mock_slippage,
        mock_dup,
        mock_log_val,
        sample_sell_order,
    ) -> None:
        report = validate_order(sample_sell_order, dry_run=True)
        assert report.result == ValidationResult.SKIPPED_DRY_RUN
        assert "MARGIN" in report.checks_passed
        assert "CONCENTRATION" in report.checks_passed

    @patch("src.execution.gatekeeper._log_validation")
    @patch("src.execution.gatekeeper.is_dry_run_enabled", return_value=False)
    @patch("src.execution.gatekeeper.validate_no_duplicate", return_value=(True, "OK"))
    @patch("src.execution.gatekeeper.validate_concentration", return_value=(True, "OK"))
    @patch("src.execution.gatekeeper.validate_slippage", return_value=(True, "OK"))
    @patch("src.execution.gatekeeper.validate_margin", return_value=(True, "OK"))
    @patch("src.execution.gatekeeper.is_market_open", return_value=True)
    def test_approved_live_order(
        self,
        mock_market_open,
        mock_margin,
        mock_slippage,
        mock_conc,
        mock_dup,
        mock_is_dry_run,
        mock_log_val,
        sample_buy_order,
    ) -> None:
        report = validate_order(sample_buy_order, dry_run=False)
        assert report.result == ValidationResult.APPROVED
        assert report.is_dry_run is False
        assert sample_buy_order.is_approved is True

    @patch("src.execution.gatekeeper.validate_order")
    def test_validate_plan_aggregation(
        self, mock_validate_order, sample_buy_order, sample_sell_order
    ) -> None:
        rep1 = ValidationReport(order=sample_buy_order, result=ValidationResult.APPROVED)
        rep2 = ValidationReport(order=sample_sell_order, result=ValidationResult.REJECTED)
        rep3 = ValidationReport(order=sample_buy_order, result=ValidationResult.SKIPPED_DRY_RUN)
        mock_validate_order.side_effect = [rep1, rep2, rep3]

        plan = RebalancePlan(orders=[sample_buy_order, sample_sell_order, sample_buy_order])
        result = validate_plan(plan)

        assert result.approved_count == 1
        assert result.rejected_count == 1
        assert result.dry_run_count == 1
        assert len(result.reports) == 3

    @patch("src.execution.gatekeeper.validate_plan")
    def test_get_gatekeeper_summary(self, mock_validate_plan, sample_buy_order) -> None:
        rep = ValidationReport(
            order=sample_buy_order,
            result=ValidationResult.APPROVED,
            checks_passed=["MARKET_HOURS"],
            checks_failed=[],
        )
        mock_validate_plan.return_value = GatekeeperResult(
            reports=[rep], approved_count=1, rejected_count=0, dry_run_count=0, is_dry_run=False
        )
        summary = get_gatekeeper_summary(RebalancePlan(orders=[sample_buy_order]))

        assert summary["is_dry_run"] is False
        assert summary["approved"] == 1
        assert len(summary["reports"]) == 1
        assert summary["reports"][0]["symbol"] == "INFY"


# =====================================================================
# 3. Order Router Tests
# =====================================================================


class TestOrderRouter:
    """Tests for src/execution/order_router.py broker routing and audit logging."""

    @patch("src.execution.order_router.record_broker_execution")
    def test_log_order_to_audit_trail(self, mock_record, sample_buy_order) -> None:
        _log_order_to_audit_trail(
            order=sample_buy_order,
            internal_order_id="int-12345",
            kite_order_id="kite-99999",
            status="PLACED",
            is_dry_run=False,
        )
        mock_record.assert_called_once_with(
            internal_order_id="int-12345",
            instrument_token=sample_buy_order.instrument_token,
            tradingsymbol=sample_buy_order.tradingsymbol,
            exchange=sample_buy_order.exchange,
            transaction_type="BUY",
            requested_quantity=10,
            price_at_signal=Decimal("1500.00"),
            status="PLACED",
            kite_order_id="kite-99999",
            error_message=None,
            is_dry_run=False,
            notes="SECTOR_DRIFT",
        )

    @patch("src.execution.order_router.is_dry_run_enabled", return_value=False)
    def test_place_order_unapproved_live_fails_fast(self, mock_dry_run, sample_buy_order) -> None:
        sample_buy_order.is_approved = False
        res = place_order(sample_buy_order, dry_run=False)
        assert res["status"] == "SKIPPED"
        assert res["reason"] == "not_approved"

    @patch("src.execution.order_router.is_dry_run_enabled", return_value=False)
    def test_place_order_live_missing_audit_record_fails(
        self, mock_dry_run, sample_buy_order
    ) -> None:
        sample_buy_order.is_approved = True
        res = place_order(sample_buy_order, dry_run=False, internal_order_id=None)
        assert res["status"] == "FAILED"
        assert "gatekeeper audit record required" in res["error"]

    @patch("src.execution.order_router._log_order_to_audit_trail")
    def test_place_order_dry_run_success(self, mock_log_audit, sample_buy_order) -> None:
        res = place_order(sample_buy_order, dry_run=True, internal_order_id="audit-uuid-1")
        assert res["status"] == "DRY_RUN"
        assert res["symbol"] == "INFY"
        assert res["quantity"] == 10
        mock_log_audit.assert_called_once()

    @patch("src.execution.order_router.is_dry_run_enabled", return_value=False)
    @patch("src.execution.order_router._log_order_to_audit_trail")
    @patch("src.execution.order_router.get_authenticated_kite")
    def test_place_order_live_success_buy(
        self, mock_get_kite, mock_log_audit, mock_dry_run, sample_buy_order
    ) -> None:
        mock_kite = MagicMock()
        mock_kite.place_order.return_value = "2409270001"
        mock_kite.VARIETY_REGULAR = "regular"
        mock_kite.TRANSACTION_TYPE_BUY = "BUY"
        mock_kite.TRANSACTION_TYPE_SELL = "SELL"
        mock_kite.PRODUCT_CNC = "CNC"
        mock_kite.ORDER_TYPE_MARKET = "MARKET"
        mock_get_kite.return_value = mock_kite

        sample_buy_order.is_approved = True
        res = place_order(sample_buy_order, dry_run=False, internal_order_id="uuid-buy")

        assert res["status"] == "PLACED"
        assert res["kite_order_id"] == "2409270001"
        mock_kite.place_order.assert_called_once_with(
            variety="regular",
            exchange="NSE",
            tradingsymbol="INFY",
            transaction_type="BUY",
            quantity=10,
            product="CNC",
            order_type="MARKET",
        )
        mock_log_audit.assert_called_once()

    @patch("src.execution.order_router.is_dry_run_enabled", return_value=False)
    @patch("src.execution.order_router._log_order_to_audit_trail")
    @patch("src.execution.order_router.get_authenticated_kite")
    def test_place_order_live_success_sell(
        self, mock_get_kite, mock_log_audit, mock_dry_run, sample_sell_order
    ) -> None:
        mock_kite = MagicMock()
        mock_kite.place_order.return_value = "2409270002"
        mock_kite.VARIETY_REGULAR = "regular"
        mock_kite.TRANSACTION_TYPE_BUY = "BUY"
        mock_kite.TRANSACTION_TYPE_SELL = "SELL"
        mock_kite.PRODUCT_CNC = "CNC"
        mock_kite.ORDER_TYPE_MARKET = "MARKET"
        mock_get_kite.return_value = mock_kite

        sample_sell_order.is_approved = True
        res = place_order(sample_sell_order, dry_run=False, internal_order_id="uuid-sell")

        assert res["status"] == "PLACED"
        assert res["kite_order_id"] == "2409270002"
        assert mock_kite.place_order.call_args[1]["transaction_type"] == "SELL"

    @patch("src.execution.order_router.is_dry_run_enabled", return_value=False)
    @patch("src.execution.order_router._log_order_to_audit_trail")
    @patch("src.execution.order_router.get_authenticated_kite")
    def test_place_order_live_broker_exception(
        self, mock_get_kite, mock_log_audit, mock_dry_run, sample_buy_order
    ) -> None:
        mock_kite = MagicMock()
        mock_kite.place_order.side_effect = RuntimeError("Broker connection timeout")
        mock_get_kite.return_value = mock_kite

        sample_buy_order.is_approved = True
        res = place_order(sample_buy_order, dry_run=False, internal_order_id="uuid-err")

        assert res["status"] == "FAILED"
        assert "Broker connection timeout" in res["error"]
        mock_log_audit.assert_called_once()

    def test_execute_plan_no_approved_orders(self, sample_buy_order) -> None:
        gk_result = GatekeeperResult(
            reports=[
                ValidationReport(
                    order=sample_buy_order,
                    result=ValidationResult.REJECTED,
                )
            ],
            is_dry_run=True,
        )
        res = execute_plan(gk_result)
        assert res == []

    @patch("src.execution.order_router.place_order")
    def test_execute_plan_with_approved_orders(self, mock_place_order, sample_buy_order) -> None:
        mock_place_order.return_value = {"status": "DRY_RUN", "symbol": "INFY"}
        gk_result = GatekeeperResult(
            reports=[
                ValidationReport(
                    order=sample_buy_order,
                    result=ValidationResult.SKIPPED_DRY_RUN,
                    internal_order_id="int-1",
                )
            ],
            is_dry_run=True,
        )
        res = execute_plan(gk_result)
        assert len(res) == 1
        assert res[0]["status"] == "DRY_RUN"
        mock_place_order.assert_called_once_with(
            sample_buy_order,
            dry_run=True,
            internal_order_id="int-1",
        )
