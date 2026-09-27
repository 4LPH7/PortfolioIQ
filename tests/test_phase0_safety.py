"""Regression coverage for Phase 0 security and schema corrections."""
from __future__ import annotations

import importlib
import os
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

import pytest

from src.analytics.rebalancer import OrderSide, RebalanceOrder
from src.config.settings import is_dry_run_enabled


def _order() -> RebalanceOrder:
    return RebalanceOrder(
        tradingsymbol="PHASE0TEST",
        exchange="NSE",
        instrument_token=2_147_000_001,
        side=OrderSide.BUY,
        quantity=1,
        estimated_price=Decimal("100.00"),
    )


def test_dry_run_environment_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DRY_RUN_MODE", raising=False)
    assert is_dry_run_enabled() is True

    monkeypatch.setenv("DRY_RUN_MODE", "sometimes")
    assert is_dry_run_enabled() is True

    monkeypatch.setenv("DRY_RUN_MODE", "false")
    assert is_dry_run_enabled() is False


def test_gatekeeper_dry_run_environment_overrides_requested_live_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.execution import gatekeeper

    monkeypatch.setenv("DRY_RUN_MODE", "true")
    monkeypatch.setattr(gatekeeper, "is_market_open", lambda: True)
    monkeypatch.setattr(gatekeeper, "validate_margin", lambda _order: (True, "ok"))
    monkeypatch.setattr(gatekeeper, "validate_slippage", lambda _order: (True, "ok"))
    monkeypatch.setattr(gatekeeper, "validate_concentration", lambda _order: (True, "ok"))
    monkeypatch.setattr(gatekeeper, "validate_no_duplicate", lambda _order: (True, "ok"))
    monkeypatch.setattr(gatekeeper, "_log_validation", lambda _report: None)

    report = gatekeeper.validate_order(_order(), dry_run=False)

    assert report.result is gatekeeper.ValidationResult.SKIPPED_DRY_RUN
    assert report.is_dry_run is True


def test_order_router_never_calls_broker_when_environment_forces_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.execution import order_router

    monkeypatch.setenv("DRY_RUN_MODE", "true")
    order = _order()
    with patch.object(order_router, "get_authenticated_kite") as get_kite:
        with patch.object(order_router, "_log_order_to_audit_trail") as log_order:
            result = order_router.place_order(order, dry_run=False)

    assert result["status"] == "DRY_RUN"
    get_kite.assert_not_called()
    assert log_order.call_args.kwargs["status"] == "DRY_RUN"


def test_live_order_requires_gatekeeper_audit_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.execution import order_router

    monkeypatch.setenv("DRY_RUN_MODE", "false")
    order = _order()
    order.is_approved = True
    with patch.object(order_router, "get_authenticated_kite") as get_kite:
        result = order_router.place_order(order, dry_run=False)

    assert result["status"] == "FAILED"
    get_kite.assert_not_called()


def test_cors_allowlist_allows_only_configured_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://allowed.example")
    flask_app = importlib.reload(importlib.import_module("flask_app"))
    client = flask_app.app.test_client()

    allowed = client.options(
        "/api/health",
        headers={
            "Origin": "https://allowed.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    denied = client.options(
        "/api/health",
        headers={
            "Origin": "https://unlisted.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.headers.get("Access-Control-Allow-Origin") == "https://allowed.example"
    assert denied.headers.get("Access-Control-Allow-Origin") is None


def test_drift_detector_uses_canonical_allocation_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.analytics import drift_detector

    captured: dict[str, str] = {}

    def fake_execute_sql(query: str, _params: dict) -> list[dict]:
        captured["query"] = query
        return [{
            "allocation_type": "STOCK",
            "sector": None,
            "tradingsymbol": "PHASE0TEST",
            "target_weight_pct": 10,
            "drift_threshold_pct": 5,
        }]

    monkeypatch.setattr(drift_detector, "execute_sql", fake_execute_sql)
    targets = drift_detector._get_active_targets()

    assert ("STOCK", "PHASE0TEST") in targets
    assert "im.tradingsymbol" in captured["query"]
    assert "ta.tradingsymbol" not in captured["query"]
    assert "min_weight_pct" not in captured["query"]
    assert "max_weight_pct" not in captured["query"]


def test_partition_job_calls_migration_function(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.db import connection
    from src.scheduler import jobs

    statements: list[str] = []

    class FakeSession:
        def execute(self, statement: object) -> None:
            statements.append(str(statement))

    @contextmanager
    def fake_session():
        yield FakeSession()

    monkeypatch.setattr(connection, "get_db_session", fake_session)
    jobs._partition_maintenance_job()

    assert any("create_price_partition(CURRENT_DATE + 1)" in query for query in statements)


@pytest.mark.postgres
def test_gatekeeper_writes_canonical_order_and_validation_rows(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.db.connection import execute_sql
    from src.execution import gatekeeper

    execute_sql("""
        INSERT INTO instrument_master (
            instrument_token, exchange_token, tradingsymbol, exchange
        ) VALUES (:token, :token, :symbol, 'NSE')
        ON CONFLICT (instrument_token) DO UPDATE
        SET tradingsymbol = EXCLUDED.tradingsymbol
    """, {"token": 2_147_000_001, "symbol": "PHASE0TEST"})
    execute_sql("UPDATE system_config SET value = 'false' WHERE key = 'dry_run_mode'")

    monkeypatch.setenv("DRY_RUN_MODE", "true")
    monkeypatch.setattr(gatekeeper, "is_market_open", lambda: True)
    monkeypatch.setattr(gatekeeper, "validate_margin", lambda _order: (True, "ok"))
    monkeypatch.setattr(gatekeeper, "validate_slippage", lambda _order: (True, "ok"))
    monkeypatch.setattr(gatekeeper, "validate_concentration", lambda _order: (True, "ok"))
    monkeypatch.setattr(gatekeeper, "validate_no_duplicate", lambda _order: (True, "ok"))

    report = gatekeeper.validate_order(_order(), dry_run=False)
    order_rows = execute_sql(
        "SELECT validation_status, is_dry_run, requested_quantity, price_at_signal "
        "FROM order_audit_trail WHERE id = :id",
        {"id": report.audit_id},
    )
    checks = execute_sql(
        "SELECT check_name, passed FROM order_validation_log WHERE audit_id = :id",
        {"id": report.audit_id},
    )

    assert order_rows == [{
        "validation_status": "DRY_RUN",
        "is_dry_run": True,
        "requested_quantity": 1,
        "price_at_signal": Decimal("100.00"),
    }]
    assert len(checks) == 5
    assert all(row["passed"] for row in checks)
