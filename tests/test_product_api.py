"""
Tests for Phase 6 Product & UX Polish REST API Endpoints
(src/api/v1/blueprint.py).

Validates:
- POST /api/v1/auth/verify: Validates master API key / session token.
- GET /api/v1/alerts: Aggregates drift, stale price, tax, and system health alerts.
- GET /api/v1/system/status: Live system telemetry for DB, broker, market, scheduler.
- Proper authentication gates and error envelopes.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from flask.testing import FlaskClient

from flask_app import app
from src.analytics.drift_detector import DriftDirection, DriftSignal, DriftType
from src.config.settings import get_settings
from src.models.dtos import HoldingDTO


@pytest.fixture
def client() -> FlaskClient:
    """Provide a test client with testing mode enabled."""
    os.environ["TESTING"] = "true"
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def api_key() -> str:
    """Return the configured valid API key."""
    return get_settings().portfolioiq_api_key


# ─────────────────────────────────────────────────────────────
# 1. POST /api/v1/auth/verify Tests
# ─────────────────────────────────────────────────────────────
class TestAuthVerify:
    def test_verify_success_via_header(self, client: FlaskClient, api_key: str):
        res = client.post("/api/v1/auth/verify", headers={"X-API-Key": api_key})
        assert res.status_code == 200
        data = res.get_json()
        assert data["ok"] is True
        assert data["data"]["status"] == "authenticated"
        assert "verified successfully" in data["data"]["message"]

    def test_verify_success_via_json_body(self, client: FlaskClient, api_key: str):
        res = client.post("/api/v1/auth/verify", json={"api_key": api_key})
        assert res.status_code == 200
        data = res.get_json()
        assert data["ok"] is True
        assert data["data"]["status"] == "authenticated"

    def test_verify_invalid_key_header(self, client: FlaskClient):
        res = client.post("/api/v1/auth/verify", headers={"X-API-Key": "invalid-secret-key"})
        assert res.status_code == 401
        data = res.get_json()
        assert data["error"]["code"] == "UNAUTHORIZED"

    def test_verify_invalid_key_body(self, client: FlaskClient):
        res = client.post("/api/v1/auth/verify", json={"api_key": "wrong-key"})
        assert res.status_code == 401
        data = res.get_json()
        assert data["error"]["code"] == "UNAUTHORIZED"

    def test_verify_missing_key(self, client: FlaskClient):
        res = client.post("/api/v1/auth/verify", json={})
        assert res.status_code == 401
        data = res.get_json()
        assert data["error"]["code"] == "UNAUTHORIZED"


# ─────────────────────────────────────────────────────────────
# 2. GET /api/v1/alerts Tests
# ─────────────────────────────────────────────────────────────
class TestAlerts:
    def test_alerts_requires_authentication(self, client: FlaskClient):
        res = client.get("/api/v1/alerts")
        assert res.status_code == 401

    @patch("src.api.v1.blueprint.check_connection", return_value=True)
    @patch("src.ingestion.kite_auth.get_stored_token", return_value="dummy_token")
    @patch("src.db.repository.get_current_holdings", return_value=[])
    @patch(
        "src.analytics.tax_guard.get_annual_tax_harvesting_summary",
        return_value=MagicMock(opportunities=[]),
    )
    def test_alerts_success_empty_system_healthy(
        self,
        mock_tax,
        mock_holdings,
        mock_token,
        mock_conn,
        client: FlaskClient,
        api_key: str,
    ):
        res = client.get("/api/v1/alerts", headers={"X-API-Key": api_key})
        assert res.status_code == 200
        data = res.get_json()
        assert data["ok"] is True
        summary = data["data"]
        assert summary["unread_count"] == 0
        assert summary["alerts"] == []
        assert summary["system_healthy"] is True

    @patch("src.api.v1.blueprint.check_connection", return_value=False)
    @patch("src.ingestion.kite_auth.get_stored_token", return_value=None)
    @patch("src.db.repository.get_current_holdings", return_value=[])
    @patch(
        "src.analytics.tax_guard.get_annual_tax_harvesting_summary",
        return_value=MagicMock(opportunities=[]),
    )
    def test_alerts_system_failures(
        self,
        mock_tax,
        mock_holdings,
        mock_token,
        mock_conn,
        client: FlaskClient,
        api_key: str,
    ):
        res = client.get("/api/v1/alerts", headers={"X-API-Key": api_key})
        assert res.status_code == 200
        data = res.get_json()
        summary = data["data"]
        assert summary["unread_count"] >= 2
        assert summary["system_healthy"] is False

        alert_ids = [a["id"] for a in summary["alerts"]]
        assert "sys_db_disconnect" in alert_ids
        assert "sys_kite_token" in alert_ids

    @patch("src.api.v1.blueprint.check_connection", return_value=True)
    @patch("src.ingestion.kite_auth.get_stored_token", return_value="dummy_token")
    @patch(
        "src.db.repository.get_current_holdings",
        return_value=[
            HoldingDTO(
                instrument_token=123,
                tradingsymbol="INFY",
                average_price=Decimal("1500.00"),
                quantity=10,
            )
        ],
    )
    @patch(
        "src.analytics.drift_detector.detect_drift",
        return_value=[
            DriftSignal(
                drift_type=DriftType.HOLDING,
                name="INFY",
                current_weight_pct=Decimal("18.00"),
                target_weight_pct=Decimal("10.00"),
                drift_pct=Decimal("8.00"),
                threshold_pct=Decimal("5.00"),
                direction=DriftDirection.OVERWEIGHT,
                severity="HIGH",
            ),
            DriftSignal(
                drift_type=DriftType.HOLDING,
                name="TCS",
                current_weight_pct=Decimal("25.00"),
                target_weight_pct=Decimal("10.00"),
                drift_pct=Decimal("15.00"),
                threshold_pct=Decimal("5.00"),
                direction=DriftDirection.OVERWEIGHT,
                severity="HIGH",
            ),
        ],
    )
    @patch("src.ingestion.market_hours.is_market_open", return_value=False)
    @patch(
        "src.analytics.tax_guard.get_annual_tax_harvesting_summary",
        return_value=MagicMock(opportunities=[]),
    )
    def test_alerts_drift_triggers(
        self,
        mock_tax,
        mock_mkt,
        mock_drift,
        mock_holdings,
        mock_token,
        mock_conn,
        client: FlaskClient,
        api_key: str,
    ):
        res = client.get("/api/v1/alerts", headers={"X-API-Key": api_key})
        assert res.status_code == 200
        summary = res.get_json()["data"]
        alert_ids = [a["id"] for a in summary["alerts"]]
        assert "drift_warn_INFY" in alert_ids
        assert "drift_crit_TCS" in alert_ids

    @patch("src.api.v1.blueprint.check_connection", return_value=True)
    @patch("src.ingestion.kite_auth.get_stored_token", return_value="dummy_token")
    @patch(
        "src.db.repository.get_current_holdings",
        return_value=[
            HoldingDTO(
                instrument_token=456,
                tradingsymbol="RELIANCE",
                average_price=Decimal("2500.00"),
                quantity=10,
            )
        ],
    )
    @patch("src.analytics.drift_detector.detect_drift", return_value=[])
    @patch("src.ingestion.market_hours.is_market_open", return_value=True)
    @patch(
        "src.api.v1.blueprint.execute_sql",
        return_value=[
            {
                "instrument_token": 456,
                "last_price": Decimal("2510.00"),
                "is_stale": False,
                "last_updated": datetime.now(UTC) - timedelta(seconds=120),
            }
        ],
    )
    @patch(
        "src.analytics.tax_guard.get_annual_tax_harvesting_summary",
        return_value=MagicMock(opportunities=[]),
    )
    def test_alerts_price_staleness(
        self,
        mock_tax,
        mock_prices,
        mock_mkt,
        mock_drift,
        mock_holdings,
        mock_token,
        mock_conn,
        client: FlaskClient,
        api_key: str,
    ):
        res = client.get("/api/v1/alerts", headers={"X-API-Key": api_key})
        assert res.status_code == 200
        summary = res.get_json()["data"]
        alert_ids = [a["id"] for a in summary["alerts"]]
        assert "stale_RELIANCE" in alert_ids

    @patch("src.api.v1.blueprint.check_connection", return_value=True)
    @patch("src.ingestion.kite_auth.get_stored_token", return_value="dummy_token")
    @patch("src.db.repository.get_current_holdings", return_value=[])
    @patch("src.analytics.drift_detector.detect_drift", return_value=[])
    @patch("src.ingestion.market_hours.is_market_open", return_value=False)
    @patch("src.analytics.tax_guard.get_annual_tax_harvesting_summary")
    def test_alerts_tax_near_ltcg(
        self,
        mock_tax,
        mock_mkt,
        mock_drift,
        mock_holdings,
        mock_token,
        mock_conn,
        client: FlaskClient,
        api_key: str,
    ):
        mock_opp = MagicMock()
        mock_opp.action = "NEAR_LTCG_DEFER"
        mock_opp.tradingsymbol = "INFY"
        mock_opp.reason = "Holding INFY is within 30 days of reaching LTCG status. Defer selling."
        mock_tax.return_value = MagicMock(opportunities=[mock_opp])

        res = client.get("/api/v1/alerts", headers={"X-API-Key": api_key})
        assert res.status_code == 200
        summary = res.get_json()["data"]
        alert_ids = [a["id"] for a in summary["alerts"]]
        assert "tax_near_INFY" in alert_ids


# ─────────────────────────────────────────────────────────────
# 3. GET /api/v1/system/status Tests
# ─────────────────────────────────────────────────────────────
class TestSystemStatus:
    def test_system_status_requires_authentication(self, client: FlaskClient):
        res = client.get("/api/v1/system/status")
        assert res.status_code == 401

    @patch("src.api.v1.blueprint.check_connection", return_value=True)
    @patch("src.ingestion.kite_auth.get_stored_token", return_value="token123")
    @patch("src.api.v1.blueprint.get_database_stats", return_value={"total_orders": 42})
    @patch(
        "src.ingestion.market_hours.get_market_status",
        return_value={"is_open": False, "status_text": "Market Closed"},
    )
    def test_system_status_all_ok(
        self,
        mock_mkt,
        mock_db_stats,
        mock_token,
        mock_conn,
        client: FlaskClient,
        api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("RUN_INLINE_SCHEDULER", "true")
        res = client.get("/api/v1/system/status", headers={"X-API-Key": api_key})
        assert res.status_code == 200
        data = res.get_json()["data"]
        assert data["status"] == "OK"
        assert data["database"]["connected"] is True
        assert data["database"]["stats"]["total_orders"] == 42
        assert data["broker"]["authenticated"] is True
        assert data["market"]["status_text"] == "Market Closed"
        assert data["scheduler"]["active"] is True
        assert len(data["scheduler"]["jobs"]) >= 4

    @patch("src.api.v1.blueprint.check_connection", return_value=True)
    @patch("src.ingestion.kite_auth.get_stored_token", return_value=None)
    @patch("src.api.v1.blueprint.get_database_stats", return_value={})
    @patch(
        "src.ingestion.market_hours.get_market_status",
        return_value={"is_open": False, "status_text": "Market Closed"},
    )
    def test_system_status_degraded_when_broker_token_missing(
        self,
        mock_mkt,
        mock_db_stats,
        mock_token,
        mock_conn,
        client: FlaskClient,
        api_key: str,
    ):
        res = client.get("/api/v1/system/status", headers={"X-API-Key": api_key})
        assert res.status_code == 200
        data = res.get_json()["data"]
        assert data["status"] == "DEGRADED"
        assert data["broker"]["authenticated"] is False

    @patch("src.api.v1.blueprint.check_connection", return_value=False)
    @patch("src.ingestion.kite_auth.get_stored_token", return_value=None)
    @patch("src.api.v1.blueprint.get_database_stats", return_value={})
    @patch(
        "src.ingestion.market_hours.get_market_status",
        return_value={"is_open": False, "status_text": "Market Closed"},
    )
    def test_system_status_error_when_db_down(
        self,
        mock_mkt,
        mock_db_stats,
        mock_token,
        mock_conn,
        client: FlaskClient,
        api_key: str,
    ):
        res = client.get("/api/v1/system/status", headers={"X-API-Key": api_key})
        assert res.status_code == 200
        data = res.get_json()["data"]
        assert data["status"] == "ERROR"
        assert data["database"]["connected"] is False
