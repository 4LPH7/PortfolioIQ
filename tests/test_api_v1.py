"""
PortfolioIQ — API v1 Integration and Security Tests
Tests dual-mounting, API key authentication, correlation IDs, uniform error envelopes,
and CORS preflight handling.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

from flask_app import app
from src.config.settings import get_settings
from src.models.dtos import AppConfigDTO


@pytest.fixture
def client() -> FlaskClient:
    """Provide a test client with rate limiter disabled for test runs."""
    os.environ["TESTING"] = "true"
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def api_key() -> str:
    """Return the configured valid API key."""
    return get_settings().portfolioiq_api_key


# ─────────────────────────────────────────────────────────────
# Public & Exempt Endpoints
# ─────────────────────────────────────────────────────────────
def test_health_endpoints_accessible_without_auth(client: FlaskClient) -> None:
    """Both /api/v1/health and legacy /api/health must be accessible unauthenticated."""
    with patch("src.db.connection.check_connection", return_value=True):
        res_v1 = client.get("/api/v1/health")
        assert res_v1.status_code == 200
        assert res_v1.json == {"status": "ok", "db": True}

        res_legacy = client.get("/api/health")
        assert res_legacy.status_code == 200
        assert res_legacy.json == {"status": "ok", "db": True}


def test_market_status_accessible_without_auth(client: FlaskClient) -> None:
    """Market status must be accessible unauthenticated on both routes."""
    mock_status = {"is_open": True, "status_text": "Market Open"}
    with patch("src.ingestion.market_hours.get_market_status", return_value=mock_status):
        res_v1 = client.get("/api/v1/market/status")
        assert res_v1.status_code == 200
        assert res_v1.json == {"ok": True, "data": mock_status}

        res_legacy = client.get("/api/market/status")
        assert res_legacy.status_code == 200
        assert res_legacy.json == {"ok": True, "data": mock_status}


def test_broker_callback_returns_to_configured_frontend(client: FlaskClient) -> None:
    """A successful Kite login returns to this deployment's configured UI."""
    from types import SimpleNamespace

    with (
        patch("src.ingestion.kite_auth.exchange_token"),
        patch(
            "src.api.v1.blueprint.get_settings",
            return_value=SimpleNamespace(frontend_origin="https://example.github.io/PortfolioIQ/"),
        ),
    ):
        response = client.get(
            "/api/v1/broker/callback?request_token=one-time-token",
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["Location"] == (
        "https://example.github.io/PortfolioIQ/settings.html?broker=connected"
    )


def test_broker_callback_missing_token_has_actionable_response(client: FlaskClient) -> None:
    response = client.get("/api/v1/broker/callback")
    assert response.status_code == 400


# ─────────────────────────────────────────────────────────────
# Authentication & Security
# ─────────────────────────────────────────────────────────────
def test_protected_endpoint_rejects_missing_api_key(client: FlaskClient) -> None:
    """Calls without X-API-Key must return 401 with uniform error format."""
    res = client.get("/api/v1/portfolio/summary")
    assert res.status_code == 401
    body = res.json
    assert body["ok"] is False
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert "Invalid or missing" in body["error"]["message"]


def test_protected_endpoint_rejects_invalid_api_key(client: FlaskClient) -> None:
    """Calls with incorrect X-API-Key must return 401."""
    res = client.get(
        "/api/v1/portfolio/summary",
        headers={"X-API-Key": "completely-invalid-key"},
    )
    assert res.status_code == 401
    body = res.json
    assert body["ok"] is False
    assert body["error"]["code"] == "UNAUTHORIZED"


def test_protected_endpoint_allows_valid_api_key(client: FlaskClient, api_key: str) -> None:
    """Calls with valid X-API-Key succeed."""
    mock_summary = {"total_aum": 250000.0, "holdings": []}
    with patch("src.analytics.valuator.get_valuation_summary", return_value=mock_summary):
        res = client.get(
            "/api/v1/portfolio/summary",
            headers={"X-API-Key": api_key},
        )
        assert res.status_code == 200
        assert res.json == {"ok": True, "data": mock_summary}


def test_legacy_alias_allows_authenticated_access(client: FlaskClient, api_key: str) -> None:
    """Legacy /api/portfolio/summary alias functions identically to /api/v1."""
    mock_summary = {"total_aum": 250000.0, "holdings": []}
    with patch("src.analytics.valuator.get_valuation_summary", return_value=mock_summary):
        res = client.get(
            "/api/portfolio/summary",
            headers={"X-API-Key": api_key},
        )
        assert res.status_code == 200
        assert res.json == {"ok": True, "data": mock_summary}


# ─────────────────────────────────────────────────────────────
# CORS Preflight
# ─────────────────────────────────────────────────────────────
def test_cors_options_returns_204_without_auth(client: FlaskClient) -> None:
    """CORS preflight OPTIONS requests must bypass auth and return 204."""
    res = client.options("/api/v1/portfolio/summary")
    assert res.status_code == 204


# ─────────────────────────────────────────────────────────────
# Correlation ID Tracing
# ─────────────────────────────────────────────────────────────
def test_request_id_generated_and_returned_in_header(client: FlaskClient) -> None:
    """A generated X-Request-ID is returned if none provided in request."""
    res = client.get("/api/v1/health")
    assert "X-Request-ID" in res.headers
    assert len(res.headers["X-Request-ID"]) >= 8


def test_request_id_propagated_from_client(client: FlaskClient) -> None:
    """Client-provided X-Request-ID is preserved and echoed back."""
    req_id = "test-req-trace-12345"
    res = client.get("/api/v1/health", headers={"X-Request-ID": req_id})
    assert res.headers.get("X-Request-ID") == req_id


# ─────────────────────────────────────────────────────────────
# Uniform Error Envelopes & Validation
# ─────────────────────────────────────────────────────────────
def test_404_conforms_to_uniform_error_envelope(client: FlaskClient) -> None:
    """404 errors adhere to the uniform envelope."""
    res = client.get("/api/v1/nonexistent/route")
    assert res.status_code == 404
    body = res.json
    assert body["ok"] is False
    assert body["error"]["code"] == "NOT_FOUND"
    assert "error" in body
    assert "message" in body["error"]


def test_patch_settings_config_validation(client: FlaskClient, api_key: str) -> None:
    """PATCH /api/v1/settings/config validates schema and updates config."""
    with patch("src.api.v1.blueprint.update_app_config") as mock_update:
        # Valid payload
        res = client.patch(
            "/api/v1/settings/config",
            headers={"X-API-Key": api_key},
            json={"key": "dry_run_mode", "value": "false"},
        )
        assert res.status_code == 200
        assert res.json == {"ok": True}
        mock_update.assert_called_once_with("dry_run_mode", "false")

    # Invalid payload (missing required value)
    res_bad = client.patch(
        "/api/v1/settings/config",
        headers={"X-API-Key": api_key},
        json={"key": "dry_run_mode"},
    )
    assert res_bad.status_code == 422
    body = res_bad.json
    assert body["ok"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"] is not None


def test_get_settings_config(client: FlaskClient, api_key: str) -> None:
    """GET /api/v1/settings/config returns mapped DTO list."""
    mock_items = [
        AppConfigDTO(key="k1", value="v1", value_type="string", description="desc 1"),
        AppConfigDTO(key="k2", value="v2", value_type="string", description="desc 2"),
    ]
    with patch("src.api.v1.blueprint.list_app_configs", return_value=mock_items):
        res = client.get("/api/v1/settings/config", headers={"X-API-Key": api_key})
        assert res.status_code == 200
        assert res.json["ok"] is True
        assert len(res.json["data"]) == 2
        assert res.json["data"][0]["key"] == "k1"


def test_get_market_calendar_endpoint(client: FlaskClient, api_key: str) -> None:
    from datetime import date

    from src.models.dtos import MarketCalendarDTO

    mock_items = [
        MarketCalendarDTO(
            id=1,
            holiday_date=date(2026, 11, 8),
            holiday_name="Diwali Muhurat",
            session_type="MUHURAT",
            is_trading_holiday=False,
            special_session_open="18:15:00",
            special_session_close="19:15:00",
        )
    ]
    with patch("src.db.repository.get_market_calendar_entries", return_value=mock_items):
        res = client.get("/api/v1/market/calendar", headers={"X-API-Key": api_key})
        assert res.status_code == 200
        assert res.json["ok"] is True
        assert len(res.json["data"]) == 1
        assert res.json["data"][0]["holiday_name"] == "Diwali Muhurat"


def test_post_market_calendar_unauthorized(client: FlaskClient) -> None:
    res = client.post(
        "/api/v1/market/calendar", json={"holiday_date": "2026-11-08", "holiday_name": "Test"}
    )
    assert res.status_code == 401


def test_post_market_calendar_authorized(client: FlaskClient, api_key: str) -> None:
    from datetime import date

    from src.models.dtos import MarketCalendarDTO

    mock_dto = MarketCalendarDTO(
        id=1, holiday_date=date(2026, 11, 8), holiday_name="Diwali Muhurat"
    )
    with patch("src.db.repository.upsert_market_calendar_entry", return_value=mock_dto):
        res = client.post(
            "/api/v1/market/calendar",
            headers={"X-API-Key": api_key},
            json={
                "holiday_date": "2026-11-08",
                "holiday_name": "Diwali Muhurat",
                "session_type": "MUHURAT",
                "is_trading_holiday": False,
            },
        )
        assert res.status_code == 200
        assert res.json["ok"] is True
        assert res.json["data"]["holiday_name"] == "Diwali Muhurat"


def test_get_holdings_reconciliation_unauthorized(client: FlaskClient) -> None:
    res = client.get("/api/v1/holdings/reconciliation")
    assert res.status_code == 401


def test_get_holdings_reconciliation_authorized(client: FlaskClient, api_key: str) -> None:
    from datetime import datetime

    from src.models.dtos import HoldingsReconciliationDTO

    mock_logs = [
        HoldingsReconciliationDTO(
            id=1,
            user_id="default",
            instrument_token=123,
            tradingsymbol="TEST",
            old_quantity=10,
            new_quantity=20,
            old_avg_price=100.0,
            new_avg_price=100.0,
            delta_quantity=10,
            reconciliation_reason="TRADE_FILL",
            detected_at=datetime(2026, 9, 28, 10, 0, 0),
        )
    ]
    with patch("src.db.repository.get_holdings_reconciliation_logs", return_value=mock_logs):
        res = client.get(
            "/api/v1/holdings/reconciliation?limit=10&reason=TRADE_FILL",
            headers={"X-API-Key": api_key},
        )
        assert res.status_code == 200
        assert res.json["ok"] is True
        assert len(res.json["data"]) == 1
        assert res.json["data"][0]["reconciliation_reason"] == "TRADE_FILL"


# ─────────────────────────────────────────────────────────────
# Phase 4 Analytics & Rebalancing Endpoints
# ─────────────────────────────────────────────────────────────
def test_get_analytics_performance_unauthorized(client: FlaskClient) -> None:
    res = client.get("/api/v1/analytics/performance")
    assert res.status_code == 401
    assert res.json["ok"] is False


def test_get_analytics_performance_authorized(client: FlaskClient, api_key: str) -> None:
    from src.models.dtos import PerformanceMetricsDTO

    mock_metrics = PerformanceMetricsDTO(
        user_id="default",
        twr_pct=15.5,
        cagr_pct=14.2,
        xirr_pct=16.0,
        sharpe_ratio=1.45,
        sortino_ratio=2.10,
        max_drawdown_pct=-8.5,
        current_drawdown_pct=-2.1,
        high_water_mark=112.5,
        beta=0.92,
        alpha_annual_pct=3.5,
        r_squared=0.88,
        tracking_error_pct=4.2,
        history_days=120,
        is_warmup_period=False,
        gross_return_pct=16.0,
        stt_drag_bps=12.5,
        fee_drag_bps=2.1,
        tax_drag_bps=15.0,
        net_realized_return_pct=15.7,
    )
    with patch(
        "src.analytics.performance.compute_portfolio_performance_summary",
        return_value=mock_metrics,
    ):
        res = client.get(
            "/api/v1/analytics/performance?benchmark=NIFTY+50+TRI&window=1y",
            headers={"X-API-Key": api_key},
        )
        assert res.status_code == 200
        assert res.json["ok"] is True
        assert res.json["data"]["twr_pct"] == 15.5
        assert res.json["data"]["sharpe_ratio"] == 1.45
        assert res.json["data"]["beta"] == 0.92


def test_get_analytics_snapshots_unauthorized(client: FlaskClient) -> None:
    res = client.get("/api/v1/analytics/snapshots")
    assert res.status_code == 401


def test_get_analytics_snapshots_authorized(client: FlaskClient, api_key: str) -> None:
    from datetime import date
    from decimal import Decimal

    from src.models.dtos import PortfolioDailySnapshotDTO

    mock_snapshots = [
        PortfolioDailySnapshotDTO(
            id=1,
            snapshot_date=date(2026, 9, 28),
            user_id="default",
            total_equity_value=Decimal("450000.00"),
            cash_balance=Decimal("50000.00"),
            total_nav=Decimal("500000.00"),
            units=Decimal("5000.000000"),
            unit_nav=Decimal("100.0000"),
            daily_return_pct=Decimal("0.50"),
            benchmark_name="NIFTY 50 TRI",
            benchmark_value=Decimal("25000.00"),
            benchmark_daily_return_pct=Decimal("0.35"),
        )
    ]
    with patch("src.db.repository.get_daily_snapshots", return_value=mock_snapshots):
        res = client.get(
            "/api/v1/analytics/snapshots?limit=30",
            headers={"X-API-Key": api_key},
        )
        assert res.status_code == 200
        assert res.json["ok"] is True
        assert res.json["data"][0]["unit_nav"] == "100.0000"


def test_get_analytics_tax_harvesting_unauthorized(client: FlaskClient) -> None:
    res = client.get("/api/v1/analytics/tax-harvesting")
    assert res.status_code == 401


def test_get_analytics_tax_harvesting_authorized(client: FlaskClient, api_key: str) -> None:
    from decimal import Decimal

    from src.models.dtos import TaxHarvestingSummaryDTO

    mock_tax_summary = TaxHarvestingSummaryDTO(
        fy_year="FY 2026-27",
        ltcg_exemption_limit=Decimal("125000.00"),
        ltcg_realized_ytd=Decimal("20000.00"),
        ltcg_exemption_remaining=Decimal("105000.00"),
        is_q4=False,
        opportunities=[],
    )
    with patch(
        "src.analytics.tax_guard.get_annual_tax_harvesting_summary",
        return_value=mock_tax_summary,
    ):
        res = client.get(
            "/api/v1/analytics/tax-harvesting",
            headers={"X-API-Key": api_key},
        )
        assert res.status_code == 200
        assert res.json["ok"] is True
        assert res.json["data"]["fy_year"] == "FY 2026-27"
        assert res.json["data"]["ltcg_exemption_remaining"] == "105000.00"


def test_portfolio_cash_flows_unauthorized(client: FlaskClient) -> None:
    res_get = client.get("/api/v1/portfolio/cash-flows")
    assert res_get.status_code == 401

    res_post = client.post(
        "/api/v1/portfolio/cash-flows",
        json={"flow_date": "2026-09-28", "flow_type": "DEPOSIT", "amount": 50000},
    )
    assert res_post.status_code == 401


def test_portfolio_cash_flows_authorized(client: FlaskClient, api_key: str) -> None:
    from datetime import date
    from decimal import Decimal

    from src.models.dtos import PortfolioCashFlowDTO

    mock_flow = PortfolioCashFlowDTO(
        id=1,
        user_id="default",
        flow_date=date(2026, 9, 28),
        flow_type="DEPOSIT",
        amount=Decimal("50000.00"),
        source="MANUAL",
        notes="Salary injection",
    )
    with (
        patch("src.db.repository.get_cash_flows", return_value=[mock_flow]),
        patch("src.db.repository.record_cash_flow", return_value=mock_flow),
    ):
        # Test GET
        res_get = client.get(
            "/api/v1/portfolio/cash-flows",
            headers={"X-API-Key": api_key},
        )
        assert res_get.status_code == 200
        assert res_get.json["ok"] is True
        assert len(res_get.json["data"]) == 1
        assert res_get.json["data"][0]["amount"] == "50000.00"

        # Test POST
        res_post = client.post(
            "/api/v1/portfolio/cash-flows",
            headers={"X-API-Key": api_key},
            json={
                "flow_date": "2026-09-28",
                "flow_type": "DEPOSIT",
                "amount": 50000.0,
                "notes": "Salary injection",
            },
        )
        assert res_post.status_code == 201
        assert res_post.json["ok"] is True
        assert res_post.json["data"]["flow_type"] == "DEPOSIT"


def test_post_rebalance_preview_unauthorized(client: FlaskClient) -> None:
    res = client.post("/api/v1/rebalance/preview", json={})
    assert res.status_code == 401


def test_post_rebalance_preview_authorized(client: FlaskClient, api_key: str) -> None:
    mock_summary = {
        "total_orders": 2,
        "sell_orders": 1,
        "buy_orders": 1,
        "total_sell_value": 25000.0,
        "total_buy_value": 20000.0,
        "total_charges": 45.5,
        "total_turnover": 45000.0,
        "turnover_cap": 75000.0,
        "turnover_cap_reached": False,
        "cash_buffer_retained": 5000.0,
        "orders_suppressed_min_trade": 0,
        "orders_scaled_adv": 0,
        "net_cash_impact": 5000.0,
        "drift_signals_addressed": 2,
        "tax_warnings": 0,
        "orders": [
            {
                "symbol": "RELIANCE",
                "exchange": "NSE",
                "side": "SELL",
                "quantity": 10,
                "price": 2500.0,
                "value": 25000.0,
                "reason": "SECTOR_DRIFT",
                "charges": 29.8,
            }
        ],
    }
    with patch("src.analytics.rebalancer.get_rebalance_summary", return_value=mock_summary):
        res = client.post(
            "/api/v1/rebalance/preview",
            headers={"X-API-Key": api_key},
            json={"min_trade_value": 2000.0, "turnover_cap_pct": 0.15},
        )
        assert res.status_code == 200
        assert res.json["ok"] is True
        assert res.json["data"]["total_orders"] == 2
        assert res.json["data"]["total_charges"] == 45.5
