"""
PortfolioIQ — Gatekeeper
Pre-flight validation pipeline for rebalance orders.
Every order MUST pass all validators before reaching the Order Router.

Validation chain:
    1. Margin Check — sufficient cash/collateral?
    2. Slippage Check — price within tolerance of estimated price?
    3. Concentration Check — order won't create a new concentration breach?
    4. Duplicate Check — same order not placed within the cooldown window?
    5. Market Hours Check — is the exchange open?
    6. Dry Run Check — is dry_run_mode enabled in system_config?

If ANY validator fails, the order is REJECTED and logged to
order_validation_log for audit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from loguru import logger
from sqlalchemy import text

from src.analytics.rebalancer import RebalanceOrder, RebalancePlan, OrderSide
from src.db.connection import get_db_session, execute_sql
from src.execution.validators.margin_check import validate_margin
from src.execution.validators.slippage_check import validate_slippage
from src.execution.validators.concentration_check import validate_concentration
from src.execution.validators.duplicate_check import validate_no_duplicate
from src.ingestion.market_hours import is_market_open


class ValidationResult(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SKIPPED_DRY_RUN = "SKIPPED_DRY_RUN"


@dataclass
class ValidationReport:
    """Result of validating a single order through all gates."""
    order: RebalanceOrder
    result: ValidationResult
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    failure_reason: str | None = None
    validated_at: datetime = field(default_factory=datetime.now)


@dataclass
class GatekeeperResult:
    """Result of validating an entire rebalance plan."""
    reports: list[ValidationReport] = field(default_factory=list)
    approved_count: int = 0
    rejected_count: int = 0
    dry_run_count: int = 0
    is_dry_run: bool = True


def _is_dry_run_mode() -> bool:
    """Check if dry_run_mode is enabled in system_config."""
    rows = execute_sql("SELECT value FROM system_config WHERE key = 'dry_run_mode'")
    return rows[0]["value"].lower() == "true" if rows else True


def _log_validation(report: ValidationReport) -> None:
    """Log the validation result to order_validation_log table."""
    try:
        with get_db_session() as session:
            session.execute(
                text("""
                    INSERT INTO order_validation_log (
                        tradingsymbol, exchange, transaction_type,
                        quantity, price,
                        validation_result, failure_reason,
                        checks_passed, checks_failed,
                        validated_at
                    )
                    VALUES (
                        :symbol, :exchange, :txn_type,
                        :qty, :price,
                        :result, :reason,
                        :passed, :failed,
                        NOW()
                    )
                """),
                {
                    "symbol": report.order.tradingsymbol,
                    "exchange": report.order.exchange,
                    "txn_type": report.order.side.value,
                    "qty": report.order.quantity,
                    "price": float(report.order.estimated_price),
                    "result": report.result.value,
                    "reason": report.failure_reason,
                    "passed": ",".join(report.checks_passed),
                    "failed": ",".join(report.checks_failed),
                },
            )
    except Exception as exc:
        logger.error("Failed to log validation: {}", exc)


def validate_order(order: RebalanceOrder, dry_run: bool = True) -> ValidationReport:
    """
    Run a single order through the full validation chain.

    Args:
        order: The proposed rebalance order.
        dry_run: If True, skip to SKIPPED_DRY_RUN after validation.

    Returns:
        ValidationReport with pass/fail details.
    """
    report = ValidationReport(order=order, result=ValidationResult.APPROVED)

    # Gate 1: Market Hours
    if not is_market_open():
        report.checks_failed.append("MARKET_CLOSED")
        report.result = ValidationResult.REJECTED
        report.failure_reason = "Market is currently closed"
        _log_validation(report)
        return report
    report.checks_passed.append("MARKET_HOURS")

    # Gate 2: Margin Check (only for BUY orders)
    if order.side == OrderSide.BUY:
        margin_ok, margin_msg = validate_margin(order)
        if not margin_ok:
            report.checks_failed.append("MARGIN")
            report.result = ValidationResult.REJECTED
            report.failure_reason = margin_msg
            _log_validation(report)
            return report
    report.checks_passed.append("MARGIN")

    # Gate 3: Slippage Check
    slippage_ok, slippage_msg = validate_slippage(order)
    if not slippage_ok:
        report.checks_failed.append("SLIPPAGE")
        report.result = ValidationResult.REJECTED
        report.failure_reason = slippage_msg
        _log_validation(report)
        return report
    report.checks_passed.append("SLIPPAGE")

    # Gate 4: Concentration Check (only for BUY orders)
    if order.side == OrderSide.BUY:
        conc_ok, conc_msg = validate_concentration(order)
        if not conc_ok:
            report.checks_failed.append("CONCENTRATION")
            report.result = ValidationResult.REJECTED
            report.failure_reason = conc_msg
            _log_validation(report)
            return report
    report.checks_passed.append("CONCENTRATION")

    # Gate 5: Duplicate Check
    dup_ok, dup_msg = validate_no_duplicate(order)
    if not dup_ok:
        report.checks_failed.append("DUPLICATE")
        report.result = ValidationResult.REJECTED
        report.failure_reason = dup_msg
        _log_validation(report)
        return report
    report.checks_passed.append("DUPLICATE")

    # Gate 6: Dry Run Check
    if dry_run:
        report.result = ValidationResult.SKIPPED_DRY_RUN
        report.failure_reason = "Dry run mode — order not placed"
        logger.info(
            "[DRY RUN] {} {} x {} @ {} PASSED all gates but NOT placed",
            order.side.value, order.quantity, order.tradingsymbol, order.estimated_price
        )
    else:
        report.result = ValidationResult.APPROVED
        order.is_approved = True

    _log_validation(report)
    return report


def validate_plan(plan: RebalancePlan) -> GatekeeperResult:
    """
    Validate an entire rebalance plan through the gatekeeper.

    Returns:
        GatekeeperResult with per-order validation reports.
    """
    dry_run = _is_dry_run_mode()
    result = GatekeeperResult(is_dry_run=dry_run)

    logger.info(
        "Gatekeeper validating {} orders (dry_run={})",
        len(plan.orders), dry_run
    )

    for order in plan.orders:
        report = validate_order(order, dry_run=dry_run)
        result.reports.append(report)

        if report.result == ValidationResult.APPROVED:
            result.approved_count += 1
        elif report.result == ValidationResult.REJECTED:
            result.rejected_count += 1
        elif report.result == ValidationResult.SKIPPED_DRY_RUN:
            result.dry_run_count += 1

    logger.info(
        "Gatekeeper result: {} approved, {} rejected, {} dry-run skipped",
        result.approved_count, result.rejected_count, result.dry_run_count
    )
    return result


def get_gatekeeper_summary(plan: RebalancePlan) -> dict[str, Any]:
    """JSON-friendly gatekeeper summary for the dashboard."""
    result = validate_plan(plan)
    return {
        "is_dry_run": result.is_dry_run,
        "approved": result.approved_count,
        "rejected": result.rejected_count,
        "dry_run_skipped": result.dry_run_count,
        "reports": [
            {
                "symbol": r.order.tradingsymbol,
                "side": r.order.side.value,
                "quantity": r.order.quantity,
                "result": r.result.value,
                "checks_passed": r.checks_passed,
                "checks_failed": r.checks_failed,
                "failure_reason": r.failure_reason,
            }
            for r in result.reports
        ],
    }
