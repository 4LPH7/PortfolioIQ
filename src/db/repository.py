"""
PortfolioIQ — Centralized Data Access Repository
Encapsulates all database queries using parameterized SQLAlchemy Core and Pydantic DTOs.
Adheres to strict append-only constraints for audit tables.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.connection import get_db_session
from src.models.dtos import (
    AppConfigDTO,
    BacktestRunDTO,
    BrokerExecutionDTO,
    CreateCashFlowDTO,
    CreateMarketCalendarDTO,
    CreateSignalSnapshotDTO,
    CreateSnapshotDTO,
    HistoricalBarDTO,
    HoldingDTO,
    HoldingsReconciliationDTO,
    IndicatorEvaluationDTO,
    MarketCalendarDTO,
    OrderAttemptDTO,
    PortfolioCashFlowDTO,
    PortfolioDailySnapshotDTO,
    SignalSnapshotDTO,
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
        "estimated_tax_impact": float(estimated_tax_impact)
        if estimated_tax_impact is not None
        else None,
        "notes": notes,
    }

    with get_db_session() as session:
        result = session.execute(query, params)
        row = result.mappings().one()
        logger.debug(
            "Recorded order attempt {}: id={}, status={}",
            internal_order_id,
            row["id"],
            validation_status,
        )
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
        logger.debug(
            "Recorded broker execution for {}: broker_status={}", internal_order_id, broker_status
        )
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
    query = text(
        "SELECT key, value, value_type, description, updated_at FROM system_config WHERE key = :key"
    )
    with get_db_session() as session:
        result = session.execute(query, {"key": key})
        row = result.mappings().first()
        return AppConfigDTO.model_validate(dict(row)) if row else None


def list_app_configs() -> list[AppConfigDTO]:
    """Retrieve all configuration records from system_config."""
    query = text(
        "SELECT key, value, value_type, description, updated_at FROM system_config ORDER BY key ASC"
    )
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


def get_audit_orders(
    status: str = "ALL",
    side: str = "ALL",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Retrieve filtered order audit log entries."""
    status_sql = """CASE
        WHEN a.is_dry_run THEN 'DRY_RUN'
        WHEN a.broker_status = 'OPEN' THEN 'PLACED'
        WHEN a.broker_status = 'REJECTED' THEN 'FAILED'
        WHEN a.validation_status = 'BLOCKED' THEN 'FAILED'
        ELSE COALESCE(a.broker_status, a.validation_status)
    END"""
    where_parts: list[str] = []
    params: dict[str, Any] = {"limit": limit}

    if status != "ALL":
        where_parts.append(f"{status_sql} = :status")
        params["status"] = status
    if side != "ALL":
        where_parts.append("a.transaction_type = :side")
        params["side"] = side

    where_parts.append("""NOT EXISTS (
        SELECT 1 FROM order_audit_trail newer
        WHERE newer.internal_order_id = a.internal_order_id
          AND newer.id > a.id
    )""")
    where_sql = "WHERE " + " AND ".join(where_parts) if where_parts else ""

    query = text(f"""
        SELECT a.id, a.kite_order_id, a.tradingsymbol, a.exchange,
               a.transaction_type,
               a.requested_quantity AS quantity,
               COALESCE(a.execution_price, a.requested_price,
                        a.price_at_signal) AS price,
               {status_sql} AS status,
               a.trigger_source, a.notes AS reason,
               a.broker_status_message AS error_message,
               a.is_dry_run, a.created_at::text AS placed_at
        FROM order_audit_trail a {where_sql}
        ORDER BY a.created_at DESC LIMIT :limit
    """)
    with get_db_session() as session:
        result = session.execute(query, params)
        return [dict(r) for r in result.mappings().all()]


def get_audit_validations(limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve recent order validation logs aggregated by audit event."""
    query = text("""
        SELECT
            a.id,
            a.tradingsymbol,
            a.transaction_type,
            a.requested_quantity AS quantity,
            a.price_at_signal AS price,
            CASE
                WHEN a.validation_status = 'DRY_RUN' THEN 'DRY_RUN'
                WHEN BOOL_AND(v.passed) THEN 'APPROVED'
                ELSE 'REJECTED'
            END AS validation_result,
            STRING_AGG(v.message, '; ')
                FILTER (WHERE NOT v.passed) AS failure_reason,
            STRING_AGG(v.check_name, ', ')
                FILTER (WHERE v.passed) AS checks_passed,
            STRING_AGG(v.check_name, ', ')
                FILTER (WHERE NOT v.passed) AS checks_failed,
            a.validated_at::text AS validated_at
        FROM order_validation_log v
        JOIN order_audit_trail a ON a.id = v.audit_id
        GROUP BY a.id, a.tradingsymbol, a.transaction_type,
                 a.requested_quantity, a.price_at_signal,
                 a.validation_status, a.validated_at
        ORDER BY a.validated_at DESC LIMIT :limit
    """)
    with get_db_session() as session:
        result = session.execute(query, {"limit": limit})
        return [dict(r) for r in result.mappings().all()]


def get_database_stats() -> dict[str, int]:
    """Retrieve operational database row counts across core tables."""
    query = text("""
        SELECT
            (SELECT count(*) FROM instrument_master WHERE is_active=TRUE)      AS instruments,
            (SELECT count(*) FROM user_holdings WHERE user_id='default')       AS holdings,
            (SELECT count(*) FROM live_prices)                                 AS live_prices,
            (SELECT count(*) FROM price_history)                               AS price_history,
            (SELECT count(*) FROM order_audit_trail)                           AS audit_entries,
            (SELECT count(*) FROM order_validation_log)                        AS validation_entries,
            (SELECT count(*) FROM holding_tax_lots WHERE remaining_quantity>0) AS tax_lots,
            (SELECT count(*) FROM market_calendar)                             AS holidays
    """)
    with get_db_session() as session:
        result = session.execute(query)
        row = result.mappings().first()
        return dict(row) if row else {}


def get_market_calendar_entries(
    year: int | None = None, segment: str = "equity"
) -> list[MarketCalendarDTO]:
    """Retrieve market calendar entries for a given year and segment."""
    where_clause = "WHERE segment = :segment"
    params: dict[str, Any] = {"segment": segment}
    if year is not None:
        where_clause += " AND EXTRACT(YEAR FROM holiday_date) = :year"
        params["year"] = year

    query = text(f"""
        SELECT id, holiday_date, exchange, segment, holiday_name, session_type,
               is_trading_holiday, special_session_open, special_session_close,
               description, created_at
        FROM market_calendar
        {where_clause}
        ORDER BY holiday_date ASC
    """)
    with get_db_session() as session:
        result = session.execute(query, params)
        return [MarketCalendarDTO.model_validate(dict(r)) for r in result.mappings().all()]


def upsert_market_calendar_entry(entry: CreateMarketCalendarDTO) -> MarketCalendarDTO:
    """Upsert a market calendar entry."""
    query = text("""
        INSERT INTO market_calendar (
            holiday_date, exchange, segment, holiday_name, session_type,
            is_trading_holiday, special_session_open, special_session_close,
            description, created_at
        )
        VALUES (
            :holiday_date, :exchange, :segment, :holiday_name, :session_type,
            :is_trading_holiday, CAST(:special_session_open AS TIME), CAST(:special_session_close AS TIME),
            :description, NOW()
        )
        ON CONFLICT (holiday_date, exchange) DO UPDATE SET
            segment = EXCLUDED.segment,
            holiday_name = EXCLUDED.holiday_name,
            session_type = EXCLUDED.session_type,
            is_trading_holiday = EXCLUDED.is_trading_holiday,
            special_session_open = EXCLUDED.special_session_open,
            special_session_close = EXCLUDED.special_session_close,
            description = EXCLUDED.description
        RETURNING *
    """)
    with get_db_session() as session:
        result = session.execute(query, entry.model_dump(exclude_unset=True, exclude_none=True))
        row = result.mappings().one()
        return MarketCalendarDTO.model_validate(dict(row))


def record_holdings_reconciliation(entry: HoldingsReconciliationDTO) -> HoldingsReconciliationDTO:
    """Record a holdings reconciliation log entry."""
    query = text("""
        INSERT INTO holdings_reconciliation_log (
            user_id, instrument_token, tradingsymbol, old_quantity, new_quantity,
            old_avg_price, new_avg_price, delta_quantity, reconciliation_reason, detected_at
        )
        VALUES (
            :user_id, :instrument_token, :tradingsymbol, :old_quantity, :new_quantity,
            :old_avg_price, :new_avg_price, :delta_quantity, :reconciliation_reason, NOW()
        )
        RETURNING *
    """)
    dump = entry.model_dump()
    dump.pop("id", None)
    dump.pop("detected_at", None)
    with get_db_session() as session:
        result = session.execute(query, dump)
        row = result.mappings().one()
        return HoldingsReconciliationDTO.model_validate(dict(row))


def get_holdings_reconciliation_logs(
    user_id: str = "default", limit: int = 50, reason: str | None = None
) -> list[HoldingsReconciliationDTO]:
    """Retrieve holdings reconciliation logs."""
    where_clause = "WHERE user_id = :user_id"
    params: dict[str, Any] = {"user_id": user_id, "limit": limit}

    if reason is not None:
        where_clause += " AND reconciliation_reason = :reason"
        params["reason"] = reason

    query = text(f"""
        SELECT * FROM holdings_reconciliation_log
        {where_clause}
        ORDER BY detected_at DESC
        LIMIT :limit
    """)
    with get_db_session() as session:
        result = session.execute(query, params)
        return [HoldingsReconciliationDTO.model_validate(dict(r)) for r in result.mappings().all()]


def record_daily_snapshot(snapshot: CreateSnapshotDTO) -> PortfolioDailySnapshotDTO:
    """Record or update a daily portfolio snapshot (upsert on user_id, snapshot_date)."""
    query = text("""
        INSERT INTO portfolio_daily_snapshots (
            snapshot_date, user_id, total_equity_value, cash_balance, total_nav,
            units, unit_nav, daily_return_pct, benchmark_name, benchmark_value,
            benchmark_daily_return_pct, net_external_flow, gross_daily_return_pct,
            stt_drag_bps, fee_drag_bps, tax_drag_bps, created_at
        )
        VALUES (
            :snapshot_date, :user_id, :total_equity_value, :cash_balance, :total_nav,
            :units, :unit_nav, :daily_return_pct, :benchmark_name, :benchmark_value,
            :benchmark_daily_return_pct, :net_external_flow, :gross_daily_return_pct,
            :stt_drag_bps, :fee_drag_bps, :tax_drag_bps, NOW()
        )
        ON CONFLICT (user_id, snapshot_date) DO UPDATE SET
            total_equity_value = EXCLUDED.total_equity_value,
            cash_balance = EXCLUDED.cash_balance,
            total_nav = EXCLUDED.total_nav,
            units = EXCLUDED.units,
            unit_nav = EXCLUDED.unit_nav,
            daily_return_pct = EXCLUDED.daily_return_pct,
            benchmark_name = EXCLUDED.benchmark_name,
            benchmark_value = EXCLUDED.benchmark_value,
            benchmark_daily_return_pct = EXCLUDED.benchmark_daily_return_pct,
            net_external_flow = EXCLUDED.net_external_flow,
            gross_daily_return_pct = EXCLUDED.gross_daily_return_pct,
            stt_drag_bps = EXCLUDED.stt_drag_bps,
            fee_drag_bps = EXCLUDED.fee_drag_bps,
            tax_drag_bps = EXCLUDED.tax_drag_bps
        RETURNING *
    """)
    with get_db_session() as session:
        result = session.execute(query, snapshot.model_dump())
        row = result.mappings().one()
        return PortfolioDailySnapshotDTO.model_validate(dict(row))


def get_daily_snapshots(
    user_id: str = "default",
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 365,
) -> list[PortfolioDailySnapshotDTO]:
    """Retrieve daily portfolio snapshots for a user in ascending chronological order."""
    where_clauses = ["user_id = :user_id"]
    params: dict[str, Any] = {"user_id": user_id, "limit": limit}

    if start_date is not None:
        where_clauses.append("snapshot_date >= :start_date")
        params["start_date"] = start_date

    if end_date is not None:
        where_clauses.append("snapshot_date <= :end_date")
        params["end_date"] = end_date

    where_sql = "WHERE " + " AND ".join(where_clauses)
    query = text(f"""
        SELECT * FROM portfolio_daily_snapshots
        {where_sql}
        ORDER BY snapshot_date ASC
        LIMIT :limit
    """)
    with get_db_session() as session:
        result = session.execute(query, params)
        return [PortfolioDailySnapshotDTO.model_validate(dict(r)) for r in result.mappings().all()]


def record_cash_flow(flow: CreateCashFlowDTO) -> PortfolioCashFlowDTO:
    """Record an external cash flow into the cash flows ledger."""
    query = text("""
        INSERT INTO portfolio_cash_flows (
            user_id, flow_date, flow_type, amount, units_affected,
            nav_per_unit, source, external_reference, notes, created_at
        )
        VALUES (
            :user_id, :flow_date, :flow_type, :amount, :units_affected,
            :nav_per_unit, :source, :external_reference, :notes, NOW()
        )
        RETURNING *
    """)
    with get_db_session() as session:
        result = session.execute(query, flow.model_dump())
        row = result.mappings().one()
        return PortfolioCashFlowDTO.model_validate(dict(row))


def get_cash_flows(
    user_id: str = "default",
    start_date: date | None = None,
    limit: int = 100,
) -> list[PortfolioCashFlowDTO]:
    """Retrieve cash flow entries for a user in descending chronological order."""
    where_clauses = ["user_id = :user_id"]
    params: dict[str, Any] = {"user_id": user_id, "limit": limit}

    if start_date is not None:
        where_clauses.append("flow_date >= :start_date")
        params["start_date"] = start_date

    where_sql = "WHERE " + " AND ".join(where_clauses)
    query = text(f"""
        SELECT * FROM portfolio_cash_flows
        {where_sql}
        ORDER BY flow_date DESC, id DESC
        LIMIT :limit
    """)
    with get_db_session() as session:
        result = session.execute(query, params)
        return [PortfolioCashFlowDTO.model_validate(dict(r)) for r in result.mappings().all()]


def get_realized_ltcg_ytd(
    user_id: str = "default",
    fy_start_date: date | None = None,
) -> Decimal:
    """Calculate total realized Long-Term Capital Gains (LTCG) in the current financial year."""
    if fy_start_date is None:
        today = date.today()
        fy_year = today.year if today.month >= 4 else today.year - 1
        fy_start_date = date(fy_year, 4, 1)

    query = text("""
        SELECT COALESCE(SUM(realized_pnl), 0) AS total_ltcg
        FROM holding_tax_lots
        WHERE user_id = :user_id
          AND holding_period_category = 'LTCG'
          AND updated_at >= :fy_start_date
          AND remaining_quantity = 0
    """)
    with get_db_session() as session:
        result = session.execute(query, {"user_id": user_id, "fy_start_date": fy_start_date})
        row = result.mappings().first()
        return Decimal(str(row["total_ltcg"])) if row else Decimal("0.00")


# ============================================================
# Phase 5: Quantitative Signal Engine & Backtest Persistence
# ============================================================


def record_signal_snapshot(
    snapshot: CreateSignalSnapshotDTO, session: Session | None = None
) -> SignalSnapshotDTO:
    """Record or update a daily holding quantitative signal snapshot."""
    query = text("""
        INSERT INTO signal_snapshots (
            snapshot_date, user_id, tradingsymbol, model_version,
            current_price, benchmark_price, composite_score, signal_label, status,
            indicators, monte_carlo, created_at, updated_at
        )
        VALUES (
            :snapshot_date, :user_id, :tradingsymbol, :model_version,
            :current_price, :benchmark_price, :composite_score, :signal_label, :status,
            CAST(:indicators AS JSONB), CAST(:monte_carlo AS JSONB), NOW(), NOW()
        )
        ON CONFLICT (user_id, tradingsymbol, snapshot_date, model_version) DO UPDATE SET
            current_price = EXCLUDED.current_price,
            benchmark_price = EXCLUDED.benchmark_price,
            composite_score = EXCLUDED.composite_score,
            signal_label = EXCLUDED.signal_label,
            status = EXCLUDED.status,
            indicators = EXCLUDED.indicators,
            monte_carlo = EXCLUDED.monte_carlo,
            updated_at = NOW()
        RETURNING *
    """)
    params = snapshot.model_dump()
    params["indicators"] = json.dumps(params.get("indicators") or {})
    params["monte_carlo"] = json.dumps(params.get("monte_carlo") or {})

    def _execute(s):
        result = s.execute(query, params)
        row = result.mappings().one()
        return SignalSnapshotDTO.model_validate(dict(row))

    if session is not None:
        return _execute(session)
    with get_db_session() as s:
        return _execute(s)


def get_signal_snapshots(
    tradingsymbol: str,
    user_id: str = "default",
    limit: int = 60,
    session: Session | None = None,
) -> list[SignalSnapshotDTO]:
    """Retrieve historical signal snapshots for a symbol in descending chronological order."""
    query = text("""
        SELECT * FROM signal_snapshots
        WHERE user_id = :user_id AND tradingsymbol = :tradingsymbol
        ORDER BY snapshot_date DESC
        LIMIT :limit
    """)
    params = {"user_id": user_id, "tradingsymbol": tradingsymbol, "limit": limit}

    def _execute(s):
        result = s.execute(query, params)
        return [SignalSnapshotDTO.model_validate(dict(r)) for r in result.mappings().all()]

    if session is not None:
        return _execute(session)
    with get_db_session() as s:
        return _execute(s)


def get_pending_forward_return_snapshots(
    horizon_days: int,
    session: Session | None = None,
) -> list[SignalSnapshotDTO]:
    """Retrieve signal snapshots where forward return for horizon_days (5, 20, 60) is pending."""
    col_name = f"return_{horizon_days}d_stock"
    if col_name not in ("return_5d_stock", "return_20d_stock", "return_60d_stock"):
        raise ValueError(f"Invalid horizon_days: {horizon_days}. Must be 5, 20, or 60.")

    query = text(f"""
        SELECT * FROM signal_snapshots
        WHERE {col_name} IS NULL
          AND snapshot_date <= CURRENT_DATE - INTERVAL '{horizon_days} days'
        ORDER BY snapshot_date ASC
    """)

    def _execute(s):
        result = s.execute(query)
        return [SignalSnapshotDTO.model_validate(dict(r)) for r in result.mappings().all()]

    if session is not None:
        return _execute(session)
    with get_db_session() as s:
        return _execute(s)


def update_signal_forward_returns(
    snapshot_id: int,
    horizon: str,
    stock_return: Decimal | float,
    bench_return: Decimal | float,
    excess_return: Decimal | float,
    session: Session | None = None,
) -> None:
    """Update realized forward returns on a signal snapshot once horizon has matured."""
    if horizon not in ("5d", "20d", "60d"):
        raise ValueError(f"Invalid horizon: {horizon}. Must be '5d', '20d', or '60d'.")

    query = text(f"""
        UPDATE signal_snapshots
        SET
            return_{horizon}_stock = :stock_return,
            return_{horizon}_benchmark = :bench_return,
            excess_return_{horizon} = :excess_return,
            realized_{horizon}_at = CURRENT_DATE,
            updated_at = NOW()
        WHERE id = :snapshot_id
    """)
    params = {
        "snapshot_id": snapshot_id,
        "stock_return": float(stock_return),
        "bench_return": float(bench_return),
        "excess_return": float(excess_return),
    }

    def _execute(s):
        s.execute(query, params)

    if session is not None:
        _execute(session)
    else:
        with get_db_session() as s:
            _execute(s)


def record_backtest_run(run: BacktestRunDTO, session: Session | None = None) -> BacktestRunDTO:
    """Record a walk-forward backtest run and its child indicator evaluations."""
    run_query = text("""
        INSERT INTO backtest_runs (
            run_id, tradingsymbol, model_version, train_start_date, train_end_date,
            test_start_date, test_end_date, train_window_days, test_window_days,
            total_folds, strategy_cagr, strategy_sharpe, strategy_sortino,
            strategy_max_drawdown, strategy_win_rate, strategy_profit_factor,
            total_trades, stock_cagr, stock_sharpe, stock_max_drawdown,
            benchmark_cagr, benchmark_sharpe, benchmark_max_drawdown,
            excess_cagr_vs_stock, excess_cagr_vs_benchmark, total_cost_drag_bps,
            status, passed_hurdle, hurdle_details, created_at
        )
        VALUES (
            CAST(:run_id AS UUID), :tradingsymbol, :model_version, :train_start_date, :train_end_date,
            :test_start_date, :test_end_date, :train_window_days, :test_window_days,
            :total_folds, :strategy_cagr, :strategy_sharpe, :strategy_sortino,
            :strategy_max_drawdown, :strategy_win_rate, :strategy_profit_factor,
            :total_trades, :stock_cagr, :stock_sharpe, :stock_max_drawdown,
            :benchmark_cagr, :benchmark_sharpe, :benchmark_max_drawdown,
            :excess_cagr_vs_stock, :excess_cagr_vs_benchmark, :total_cost_drag_bps,
            :status, :passed_hurdle, CAST(:hurdle_details AS JSONB), NOW()
        )
        ON CONFLICT (tradingsymbol, model_version, train_start_date, test_end_date) DO UPDATE SET
            train_end_date = EXCLUDED.train_end_date,
            test_start_date = EXCLUDED.test_start_date,
            train_window_days = EXCLUDED.train_window_days,
            test_window_days = EXCLUDED.test_window_days,
            total_folds = EXCLUDED.total_folds,
            strategy_cagr = EXCLUDED.strategy_cagr,
            strategy_sharpe = EXCLUDED.strategy_sharpe,
            strategy_sortino = EXCLUDED.strategy_sortino,
            strategy_max_drawdown = EXCLUDED.strategy_max_drawdown,
            strategy_win_rate = EXCLUDED.strategy_win_rate,
            strategy_profit_factor = EXCLUDED.strategy_profit_factor,
            total_trades = EXCLUDED.total_trades,
            stock_cagr = EXCLUDED.stock_cagr,
            stock_sharpe = EXCLUDED.stock_sharpe,
            stock_max_drawdown = EXCLUDED.stock_max_drawdown,
            benchmark_cagr = EXCLUDED.benchmark_cagr,
            benchmark_sharpe = EXCLUDED.benchmark_sharpe,
            benchmark_max_drawdown = EXCLUDED.benchmark_max_drawdown,
            excess_cagr_vs_stock = EXCLUDED.excess_cagr_vs_stock,
            excess_cagr_vs_benchmark = EXCLUDED.excess_cagr_vs_benchmark,
            total_cost_drag_bps = EXCLUDED.total_cost_drag_bps,
            status = EXCLUDED.status,
            passed_hurdle = EXCLUDED.passed_hurdle,
            hurdle_details = EXCLUDED.hurdle_details
        RETURNING *
    """)

    eval_insert = text("""
        INSERT INTO indicator_evaluations (
            backtest_run_id, indicator_name, in_sample_ic, in_sample_p_value,
            out_sample_ic, out_sample_p_value, mean_ic, std_ic,
            information_ratio, weight, is_pruned, prune_reason, created_at
        )
        VALUES (
            :backtest_run_id, :indicator_name, :in_sample_ic, :in_sample_p_value,
            :out_sample_ic, :out_sample_p_value, :mean_ic, :std_ic,
            :information_ratio, :weight, :is_pruned, :prune_reason, NOW()
        )
        ON CONFLICT (backtest_run_id, indicator_name) DO UPDATE SET
            in_sample_ic = EXCLUDED.in_sample_ic,
            in_sample_p_value = EXCLUDED.in_sample_p_value,
            out_sample_ic = EXCLUDED.out_sample_ic,
            out_sample_p_value = EXCLUDED.out_sample_p_value,
            mean_ic = EXCLUDED.mean_ic,
            std_ic = EXCLUDED.std_ic,
            information_ratio = EXCLUDED.information_ratio,
            weight = EXCLUDED.weight,
            is_pruned = EXCLUDED.is_pruned,
            prune_reason = EXCLUDED.prune_reason
    """)

    run_dict = run.model_dump()
    indicators = run_dict.pop("indicators", [])
    run_dict["run_id"] = str(run_dict["run_id"])
    run_dict["hurdle_details"] = json.dumps(run_dict.get("hurdle_details") or {})

    def _execute(s):
        res = s.execute(run_query, run_dict)
        saved_run_dict = dict(res.mappings().one())
        run_id_db = saved_run_dict["id"]

        saved_evals = []
        for ind in indicators:
            ind_dict = {
                "backtest_run_id": run_id_db,
                "indicator_name": ind["name"],
                "in_sample_ic": ind.get("in_sample_ic"),
                "in_sample_p_value": ind.get("in_sample_p_value"),
                "out_sample_ic": ind.get("out_sample_ic"),
                "out_sample_p_value": ind.get("out_sample_p_value"),
                "mean_ic": ind.get("mean_ic", 0.0),
                "std_ic": ind.get("std_ic", 0.0),
                "information_ratio": ind.get("information_ratio", 0.0),
                "weight": ind.get("weight", 0.0),
                "is_pruned": ind.get("is_pruned", True),
                "prune_reason": ind.get("prune_reason"),
            }
            s.execute(eval_insert, ind_dict)
            saved_evals.append(
                IndicatorEvaluationDTO(
                    name=ind["name"],
                    in_sample_ic=ind.get("in_sample_ic"),
                    in_sample_p_value=ind.get("in_sample_p_value"),
                    out_sample_ic=ind.get("out_sample_ic"),
                    out_sample_p_value=ind.get("out_sample_p_value"),
                    mean_ic=ind.get("mean_ic", 0.0),
                    std_ic=ind.get("std_ic", 0.0),
                    information_ratio=ind.get("information_ratio", 0.0),
                    p_value=ind.get("p_value", 1.0),
                    weight=ind.get("weight", 0.0),
                    is_pruned=ind.get("is_pruned", True),
                    prune_reason=ind.get("prune_reason"),
                    score=ind.get("score", 50.0),
                )
            )

        saved_run_dict["indicators"] = saved_evals
        return BacktestRunDTO.model_validate(saved_run_dict)

    if session is not None:
        return _execute(session)
    with get_db_session() as s:
        return _execute(s)


def get_latest_backtest_run(
    tradingsymbol: str,
    model_version: str = "v1.0.0",
    session: Session | None = None,
) -> BacktestRunDTO | None:
    """Retrieve the latest walk-forward backtest run and its indicator evaluations."""
    run_query = text("""
        SELECT * FROM backtest_runs
        WHERE tradingsymbol = :tradingsymbol AND model_version = :model_version
        ORDER BY test_end_date DESC, id DESC
        LIMIT 1
    """)
    eval_query = text("""
        SELECT * FROM indicator_evaluations
        WHERE backtest_run_id = :backtest_run_id
        ORDER BY indicator_name ASC
    """)

    def _execute(s):
        run_res = s.execute(
            run_query, {"tradingsymbol": tradingsymbol, "model_version": model_version}
        )
        run_row = run_res.mappings().first()
        if not run_row:
            return None
        run_dict = dict(run_row)

        eval_res = s.execute(eval_query, {"backtest_run_id": run_dict["id"]})
        evals = []
        for er in eval_res.mappings().all():
            evals.append(
                IndicatorEvaluationDTO(
                    name=er["indicator_name"],
                    in_sample_ic=float(er["in_sample_ic"])
                    if er["in_sample_ic"] is not None
                    else None,
                    in_sample_p_value=float(er["in_sample_p_value"])
                    if er["in_sample_p_value"] is not None
                    else None,
                    out_sample_ic=float(er["out_sample_ic"])
                    if er["out_sample_ic"] is not None
                    else None,
                    out_sample_p_value=float(er["out_sample_p_value"])
                    if er["out_sample_p_value"] is not None
                    else None,
                    mean_ic=float(er["mean_ic"]) if er["mean_ic"] is not None else 0.0,
                    std_ic=float(er["std_ic"]) if er["std_ic"] is not None else 0.0,
                    information_ratio=float(er["information_ratio"])
                    if er["information_ratio"] is not None
                    else 0.0,
                    weight=float(er["weight"]) if er["weight"] is not None else 0.0,
                    is_pruned=bool(er["is_pruned"]),
                    prune_reason=er["prune_reason"],
                )
            )
        run_dict["indicators"] = evals
        return BacktestRunDTO.model_validate(run_dict)

    if session is not None:
        return _execute(session)
    with get_db_session() as s:
        return _execute(s)


def upsert_historical_bars(bars: list[HistoricalBarDTO], session: Session | None = None) -> int:
    """Batch upsert historical daily OHLCV bars."""
    if not bars:
        return 0

    query = text("""
        INSERT INTO historical_daily_bars (
            tradingsymbol, bar_date, open, high, low, close, volume, created_at
        )
        VALUES (
            :tradingsymbol, :bar_date, :open, :high, :low, :close, :volume, NOW()
        )
        ON CONFLICT (tradingsymbol, bar_date) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume
    """)
    param_list = [b.model_dump(exclude={"created_at"}) for b in bars]

    def _execute(s):
        s.execute(query, param_list)
        return len(param_list)

    if session is not None:
        return _execute(session)
    with get_db_session() as s:
        return _execute(s)


def get_historical_bars(
    tradingsymbol: str,
    start_date: date | None = None,
    end_date: date | None = None,
    session: Session | None = None,
) -> list[HistoricalBarDTO]:
    """Retrieve historical daily bars in ascending chronological order."""
    where_clauses = ["tradingsymbol = :tradingsymbol"]
    params: dict[str, Any] = {"tradingsymbol": tradingsymbol}

    if start_date is not None:
        where_clauses.append("bar_date >= :start_date")
        params["start_date"] = start_date

    if end_date is not None:
        where_clauses.append("bar_date <= :end_date")
        params["end_date"] = end_date

    where_sql = "WHERE " + " AND ".join(where_clauses)
    query = text(f"""
        SELECT * FROM historical_daily_bars
        {where_sql}
        ORDER BY bar_date ASC
    """)

    def _execute(s):
        result = s.execute(query, params)
        return [HistoricalBarDTO.model_validate(dict(r)) for r in result.mappings().all()]

    if session is not None:
        return _execute(session)
    with get_db_session() as s:
        return _execute(s)
