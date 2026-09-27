"""
PortfolioIQ — Centralized Data Access Repository
Encapsulates all database queries using parameterized SQLAlchemy Core and Pydantic DTOs.
Adheres to strict append-only constraints for audit tables.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from loguru import logger
from sqlalchemy import text

from src.db.connection import get_db_session
from src.models.dtos import (
    AppConfigDTO,
    BrokerExecutionDTO,
    HoldingDTO,
    OrderAttemptDTO,
    ValidationCheckDTO,
)


def record_order_attempt(
    internal_order_id: str | UUID,
    instrument_token: int,
    tradingsymbol: str,
    exchange: str,
    transaction_type: str,
    requested_quantity: int,
    price_at_signal: Decimal | float,
    validation_status: str,
    is_dry_run: bool,
    notes: str | None = None,
    trigger_source: str = "REBALANCER",
    user_id: str = "default",
    order_type: str = "MARKET",
    product: str = "CNC",
    validity: str = "DAY",
    target_weight_pct: Decimal | float | None = None,
    current_weight_pct: Decimal | float | None = None,
    drift_pct: Decimal | float | None = None,
    tax_type: str | None = None,
    estimated_tax_impact: Decimal | float | None = None,
) -> OrderAttemptDTO:
    """
    Record an initial order validation attempt into order_audit_trail.
    Initial broker_status is set to 'NOT_SENT'.
    """
    query = text("""
        INSERT INTO order_audit_trail (
            internal_order_id, user_id, instrument_token, tradingsymbol, exchange,
            transaction_type, order_type, product, validity, requested_quantity,
            price_at_signal, validation_status, broker_status, trigger_source,
            is_dry_run, target_weight_pct, current_weight_pct, drift_pct,
            tax_type, estimated_tax_impact, notes, validated_at, created_at
        )
        VALUES (
            CAST(:internal_order_id AS UUID), :user_id, :instrument_token, :tradingsymbol, :exchange,
            :transaction_type, :order_type, :product, :validity, :requested_quantity,
            :price_at_signal, :validation_status, 'NOT_SENT', :trigger_source,
            :is_dry_run, :target_weight_pct, :current_weight_pct, :drift_pct,
            :tax_type, :estimated_tax_impact, :notes, NOW(), NOW()
        )
        RETURNING *
    """)
    params = {
        "internal_order_id": str(internal_order_id),
        "user_id": user_id,
        "instrument_token": instrument_token,
        "tradingsymbol": tradingsymbol,
        "exchange": exchange,
        "transaction_type": transaction_type,
        "order_type": order_type,
        "product": product,
        "validity": validity,
        "requested_quantity": requested_quantity,
        "price_at_signal": float(price_at_signal),
        "validation_status": validation_status,
        "trigger_source": trigger_source,
        "is_dry_run": is_dry_run,
        "target_weight_pct": float(target_weight_pct) if target_weight_pct is not None else None,
        "current_weight_pct": float(current_weight_pct) if current_weight_pct is not None else None,
        "drift_pct": float(drift_pct) if drift_pct is not None else None,
        "tax_type": tax_type,
        "estimated_tax_impact": float(estimated_tax_impact) if estimated_tax_impact is not None else None,
        "notes": notes,
    }

    with get_db_session() as session:
        result = session.execute(query, params)
        row = result.mappings().one()
        logger.debug("Recorded order attempt {}: id={}, status={}", internal_order_id, row["id"], validation_status)
        return OrderAttemptDTO.model_validate(dict(row))


def record_validation_check(
    audit_id: int,
    check_name: str,
    passed: bool,
    message: str,
    expected_value: str | None = None,
    actual_value: str | None = None,
) -> ValidationCheckDTO:
    """Record an individual rule check result in order_validation_log."""
    query = text("""
        INSERT INTO order_validation_log (
            audit_id, check_name, passed, message, expected_value, actual_value, checked_at
        )
        VALUES (
            :audit_id, :check_name, :passed, :message, :expected_value, :actual_value, NOW()
        )
        RETURNING *
    """)
    params = {
        "audit_id": audit_id,
        "check_name": check_name,
        "passed": passed,
        "message": message,
        "expected_value": str(expected_value) if expected_value is not None else None,
        "actual_value": str(actual_value) if actual_value is not None else None,
    }
    with get_db_session() as session:
        result = session.execute(query, params)
        row = result.mappings().one()
        return ValidationCheckDTO.model_validate(dict(row))


def record_broker_execution(
    internal_order_id: str | UUID,
    instrument_token: int,
    tradingsymbol: str,
    exchange: str,
    transaction_type: str,
    requested_quantity: int,
    price_at_signal: Decimal | float,
    status: str,  # 'DRY_RUN', 'PLACED', 'FAILED'
    kite_order_id: str | None = None,
    error_message: str | None = None,
    is_dry_run: bool = True,
    notes: str | None = None,
    user_id: str = "default",
    executed_quantity: int = 0,
    execution_price: Decimal | float | None = None,
) -> BrokerExecutionDTO:
    """
    Append an immutable broker execution outcome row to order_audit_trail.
    IMPORTANT: Never issues an UPDATE to preserve 009_audit_immutability trigger compliance.
    """
    broker_status = {
        "DRY_RUN": "NOT_SENT",
        "PLACED": "OPEN",
        "FAILED": "REJECTED",
    }.get(status)
    if not broker_status:
        raise ValueError(f"Unsupported broker outcome status: {status}")

    validation_status = "DRY_RUN" if status == "DRY_RUN" else "APPROVED"

    query = text("""
        INSERT INTO order_audit_trail (
            internal_order_id, user_id, kite_order_id, instrument_token,
            tradingsymbol, exchange, transaction_type,
            requested_quantity, executed_quantity, price_at_signal,
            execution_price, validation_status, broker_status,
            broker_status_message, trigger_source, is_dry_run,
            notes, executed_at, created_at
        )
        VALUES (
            CAST(:internal_order_id AS UUID), :user_id, :kite_order_id, :instrument_token,
            :tradingsymbol, :exchange, :transaction_type,
            :requested_quantity, :executed_quantity, :price_at_signal,
            :execution_price, :validation_status, :broker_status,
            :error_message, 'REBALANCER', :is_dry_run,
            :notes, NOW(), NOW()
        )
        RETURNING id, internal_order_id, kite_order_id, broker_status,
                  broker_status_message, executed_quantity, execution_price,
                  executed_at, is_dry_run
    """)
    params = {
        "internal_order_id": str(internal_order_id),
        "user_id": user_id,
        "kite_order_id": kite_order_id,
        "instrument_token": instrument_token,
        "tradingsymbol": tradingsymbol,
        "exchange": exchange,
        "transaction_type": transaction_type,
        "requested_quantity": requested_quantity,
        "executed_quantity": executed_quantity,
        "price_at_signal": float(price_at_signal),
        "execution_price": float(execution_price) if execution_price is not None else None,
        "validation_status": validation_status,
        "broker_status": broker_status,
        "error_message": error_message,
        "is_dry_run": is_dry_run,
        "notes": notes,
    }
    with get_db_session() as session:
        result = session.execute(query, params)
        row = result.mappings().one()
        logger.debug("Recorded broker execution for {}: broker_status={}", internal_order_id, broker_status)
        return BrokerExecutionDTO.model_validate(dict(row))


def get_current_holdings(user_id: str = "default", active_only: bool = True) -> list[HoldingDTO]:
    """Retrieve holdings from user_holdings for a user."""
    sql = """
        SELECT * FROM user_holdings
        WHERE user_id = :user_id
    """
    if active_only:
        sql += " AND (quantity + t1_quantity) > 0"
    sql += " ORDER BY tradingsymbol ASC"

    with get_db_session() as session:
        result = session.execute(text(sql), {"user_id": user_id})
        return [HoldingDTO.model_validate(dict(r)) for r in result.mappings().all()]


def get_app_config(key: str) -> AppConfigDTO | None:
    """Retrieve a single runtime configuration key from system_config."""
    query = text("SELECT key, value, value_type, description, updated_at FROM system_config WHERE key = :key")
    with get_db_session() as session:
        result = session.execute(query, {"key": key})
        row = result.mappings().first()
        return AppConfigDTO.model_validate(dict(row)) if row else None


def list_app_configs() -> list[AppConfigDTO]:
    """Retrieve all configuration records from system_config."""
    query = text("SELECT key, value, value_type, description, updated_at FROM system_config ORDER BY key ASC")
    with get_db_session() as session:
        result = session.execute(query)
        return [AppConfigDTO.model_validate(dict(r)) for r in result.mappings().all()]


def update_app_config(key: str, value: str) -> None:
    """Update runtime configuration setting in system_config."""
    query = text("UPDATE system_config SET value = :value, updated_at = NOW() WHERE key = :key")
    with get_db_session() as session:
        res = session.execute(query, {"key": key, "value": str(value)})
        if res.rowcount == 0:
            raise KeyError(f"Configuration key '{key}' not found in system_config")
