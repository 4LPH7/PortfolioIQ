"""
PortfolioIQ — Order Router
Places validated orders on Zerodha Kite Connect and logs to audit trail.

CRITICAL SAFETY:
    - ONLY processes orders marked is_approved=True by the Gatekeeper
    - ALL orders are logged to order_audit_trail (immutable — cannot be
      modified or deleted thanks to the 009 trigger)
    - Respects DRY_RUN_MODE from system_config
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from loguru import logger
from sqlalchemy import text

from src.analytics.rebalancer import RebalanceOrder, OrderSide
from src.config.settings import is_dry_run_enabled
from src.db.connection import get_db_session
from src.db.repository import record_broker_execution
from src.execution.gatekeeper import GatekeeperResult, ValidationResult
from src.ingestion.kite_auth import get_authenticated_kite


def _log_order_to_audit_trail(
    order: RebalanceOrder,
    internal_order_id: str,
    kite_order_id: str | None,
    status: str,
    error_message: str | None = None,
    is_dry_run: bool = True,
) -> None:
    """
    Append an immutable broker outcome record to the order_audit_trail via repository.
    This table has a BEFORE UPDATE OR DELETE trigger that prevents
    any modification — ensuring complete audit integrity.
    """
    record_broker_execution(
        internal_order_id=internal_order_id,
        instrument_token=order.instrument_token,
        tradingsymbol=order.tradingsymbol,
        exchange=order.exchange,
        transaction_type=order.side.value,
        requested_quantity=order.quantity,
        price_at_signal=order.estimated_price,
        status=status,
        kite_order_id=kite_order_id,
        error_message=error_message,
        is_dry_run=is_dry_run,
        notes=order.reason.value,
    )


def place_order(
    order: RebalanceOrder,
    dry_run: bool = True,
    internal_order_id: str | None = None,
) -> dict[str, Any]:
    """
    Place a single order on Kite Connect.

    Args:
        order: A gatekeeper-approved RebalanceOrder.
        dry_run: If True, log but do NOT actually place on Kite.

    Returns:
        Dict with order placement result.
    """
    dry_run = dry_run or is_dry_run_enabled()

    if not order.is_approved and not dry_run:
        logger.warning(
            "Order {} {} x {} was NOT approved by gatekeeper. Skipping.",
            order.side.value, order.quantity, order.tradingsymbol
        )
        return {"status": "SKIPPED", "reason": "not_approved"}

    if not dry_run and internal_order_id is None:
        logger.error("Refusing live order without a gatekeeper audit record")
        return {"status": "FAILED", "error": "gatekeeper audit record required"}

    if dry_run:
        logger.info(
            "[DRY RUN] Would place: {} {} x {} @ ~{} on {}",
            order.side.value, order.quantity, order.tradingsymbol,
            order.estimated_price, order.exchange
        )
        _log_order_to_audit_trail(
            order=order,
            internal_order_id=internal_order_id or str(uuid4()),
            kite_order_id=None,
            status="DRY_RUN",
            is_dry_run=True,
        )
        return {
            "status": "DRY_RUN",
            "symbol": order.tradingsymbol,
            "side": order.side.value,
            "quantity": order.quantity,
            "estimated_price": float(order.estimated_price),
        }

    # ─── LIVE ORDER PLACEMENT ─────────────────────────────────
    try:
        kite = get_authenticated_kite()

        kite_order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=order.exchange,
            tradingsymbol=order.tradingsymbol,
            transaction_type=(
                kite.TRANSACTION_TYPE_BUY
                if order.side == OrderSide.BUY
                else kite.TRANSACTION_TYPE_SELL
            ),
            quantity=order.quantity,
            product=kite.PRODUCT_CNC,
            order_type=kite.ORDER_TYPE_MARKET,
        )
    except Exception as exc:
        error_msg = str(exc)
        logger.error(
            "ORDER FAILED: {} {} x {} — Error: {}",
            order.side.value, order.quantity, order.tradingsymbol, error_msg
        )

        _log_order_to_audit_trail(
            order=order,
            internal_order_id=internal_order_id,
            kite_order_id=None,
            status="FAILED",
            error_message=error_msg,
            is_dry_run=False,
        )

        return {
            "status": "FAILED",
            "symbol": order.tradingsymbol,
            "error": error_msg,
        }

    logger.success(
        "ORDER PLACED: {} {} x {} — Kite Order ID: {}",
        order.side.value, order.quantity, order.tradingsymbol, kite_order_id
    )
    _log_order_to_audit_trail(
        order=order,
        internal_order_id=internal_order_id,
        kite_order_id=str(kite_order_id),
        status="PLACED",
        is_dry_run=False,
    )

    return {
        "status": "PLACED",
        "kite_order_id": str(kite_order_id),
        "symbol": order.tradingsymbol,
        "side": order.side.value,
        "quantity": order.quantity,
    }


def execute_plan(gatekeeper_result: GatekeeperResult) -> list[dict[str, Any]]:
    """
    Execute all approved orders from a gatekeeper-validated plan.

    Args:
        gatekeeper_result: Output from gatekeeper.validate_plan()

    Returns:
        List of per-order placement results.
    """
    results = []
    dry_run = gatekeeper_result.is_dry_run

    approved_reports = [
        r for r in gatekeeper_result.reports
        if r.result in (ValidationResult.APPROVED, ValidationResult.SKIPPED_DRY_RUN)
    ]

    if not approved_reports:
        logger.info("No approved orders to execute.")
        return results

    logger.info(
        "Executing {} orders (dry_run={})",
        len(approved_reports), dry_run
    )

    for report in approved_reports:
        result = place_order(
            report.order,
            dry_run=dry_run,
            internal_order_id=report.internal_order_id,
        )
        results.append(result)

    # Summary
    placed = sum(1 for r in results if r["status"] == "PLACED")
    dry_runs = sum(1 for r in results if r["status"] == "DRY_RUN")
    failed = sum(1 for r in results if r["status"] == "FAILED")

    logger.info(
        "Execution complete: {} placed, {} dry-run, {} failed",
        placed, dry_runs, failed
    )
    return results


if __name__ == "__main__":
    """
    Test the full pipeline: Rebalance → Gatekeeper → Order Router
    (runs in dry-run mode by default)
    """
    from src.analytics.rebalancer import generate_rebalance_plan
    from src.execution.gatekeeper import validate_plan

    plan = generate_rebalance_plan()
    gk_result = validate_plan(plan)
    results = execute_plan(gk_result)

    import json
    print(json.dumps(results, indent=2, default=str))
