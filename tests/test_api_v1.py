"""
PortfolioIQ — API v1 Integration and Security Tests
Tests dual-mounting, API key authentication, correlation IDs, uniform error envelopes,
and CORS preflight handling.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from flask import Flask
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
