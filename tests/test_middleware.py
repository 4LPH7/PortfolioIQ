"""
Tests for src/api/middleware.py
Verifies correlation ID handling, uniform error envelopes, API key authentication,
and Pydantic JSON request validation.
"""

from __future__ import annotations

import pytest
from flask import Flask, g, jsonify
from pydantic import BaseModel, Field

from src.api.middleware import (
    attach_correlation_id_header,
    format_error_response,
    require_api_key,
    setup_correlation_id,
    validate_json,
)


class DummyPayload(BaseModel):
    name: str
    amount: int = Field(gt=0)


@pytest.fixture
def app():
    """Create a minimal Flask test application with middleware routes."""
    test_app = Flask("test_middleware_app")
    test_app.config["TESTING"] = True

    @test_app.before_request
    def before_req():
        setup_correlation_id()

    @test_app.after_request
    def after_req(response):
        return attach_correlation_id_header(response)

    @test_app.route("/public", methods=["GET"])
    def public_route():
        return jsonify({"message": "public"})

    @test_app.route("/protected", methods=["GET", "POST", "OPTIONS"])
    @require_api_key
    def protected_route():
        return jsonify({"message": "protected"})

    @test_app.route("/exempt", methods=["GET"])
    @require_api_key
    def exempt_route():
        return jsonify({"message": "exempt"})

    @test_app.route("/validate", methods=["POST"])
    @validate_json(DummyPayload)
    def validate_route(payload: DummyPayload):
        return jsonify({"name": payload.name, "amount": payload.amount})

    return test_app


class TestCorrelationId:
    """Tests for correlation ID tracking in contextvars and response headers."""

    def test_custom_correlation_id_propagated(self, app) -> None:
        client = app.test_client()
        custom_id = "test-cid-98765"
        resp = client.get("/public", headers={"X-Request-ID": custom_id})
        assert resp.status_code == 200
        assert resp.headers.get("X-Request-ID") == custom_id

    def test_auto_generated_correlation_id(self, app) -> None:
        client = app.test_client()
        resp = client.get("/public")
        assert resp.status_code == 200
        cid = resp.headers.get("X-Request-ID")
        assert cid is not None
        assert len(cid) == 12

    def test_attach_correlation_id_without_g_id(self, app) -> None:
        with app.test_request_context():
            g.correlation_id = None
            response = app.response_class("test")
            result = attach_correlation_id_header(response)
            assert "X-Request-ID" not in result.headers


class TestErrorResponseEnvelope:
    """Tests for format_error_response."""

    def test_format_error_response_structure(self, app) -> None:
        with app.app_context():
            resp, status = format_error_response(
                code="CUSTOM_ERR",
                message="Something went wrong",
                details={"hint": "check parameters"},
                status_code=403,
            )
            assert status == 403
            data = resp.get_json()
            assert data == {
                "ok": False,
                "error": {
                    "code": "CUSTOM_ERR",
                    "message": "Something went wrong",
                    "details": {"hint": "check parameters"},
                },
            }


class TestRequireApiKey:
    """Tests for require_api_key decorator."""

    def test_cors_preflight_options_allowed(self, app) -> None:
        client = app.test_client()
        resp = client.options("/protected")
        assert resp.status_code == 204
        assert resp.data == b""

    def test_missing_api_key_returns_401(self, app) -> None:
        client = app.test_client()
        resp = client.get("/protected")
        assert resp.status_code == 401
        data = resp.get_json()
        assert data["ok"] is False
        assert data["error"]["code"] == "UNAUTHORIZED"

    def test_invalid_api_key_returns_401(self, app) -> None:
        client = app.test_client()
        resp = client.get("/protected", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401
        data = resp.get_json()
        assert data["ok"] is False
        assert data["error"]["code"] == "UNAUTHORIZED"

    def test_valid_api_key_success(self, app, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.config.settings import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "portfolioiq_api_key", "secret-test-key-123")

        client = app.test_client()
        resp = client.get("/protected", headers={"X-API-Key": "secret-test-key-123"})
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "protected"

    def test_valid_api_key_with_whitespace_success(
        self, app, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.config.settings import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "portfolioiq_api_key", "secret-test-key-123")

        client = app.test_client()
        resp = client.get("/protected", headers={"X-API-Key": "  secret-test-key-123  "})
        assert resp.status_code == 200

    def test_exempt_endpoint_bypasses_auth(self, app, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.api import middleware

        monkeypatch.setattr(
            middleware, "EXEMPT_ENDPOINTS", middleware.EXEMPT_ENDPOINTS | {"exempt_route"}
        )
        client = app.test_client()
        resp = client.get("/exempt")
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "exempt"


class TestValidateJson:
    """Tests for validate_json decorator."""

    def test_valid_payload_passes_through(self, app) -> None:
        client = app.test_client()
        resp = client.post("/validate", json={"name": "Tata Motors", "amount": 50})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "Tata Motors"
        assert data["amount"] == 50

    def test_missing_body_returns_422(self, app) -> None:
        client = app.test_client()
        resp = client.post("/validate", data="", content_type="application/json")
        assert resp.status_code == 422
        data = resp.get_json()
        assert data["ok"] is False
        assert data["error"]["code"] == "VALIDATION_ERROR"
        assert isinstance(data["error"]["details"], list)

    def test_invalid_fields_returns_422(self, app) -> None:
        client = app.test_client()
        resp = client.post("/validate", json={"name": "Tata Motors", "amount": -10})
        assert resp.status_code == 422
        data = resp.get_json()
        assert data["ok"] is False
        assert data["error"]["code"] == "VALIDATION_ERROR"
        assert len(data["error"]["details"]) > 0
