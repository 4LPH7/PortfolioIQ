"""
Tests for Phase 6 Onboarding & CSV Portfolio Import (Plan 06-03).

Validates:
- POST /api/v1/holdings/import-csv endpoint:
  - Zerodha Holdings format parsing
  - Zerodha Tradebook format parsing and net quantity aggregation
  - Generic broker CSV parsing
  - Missing required columns rejection (400)
  - Empty file / payload handling (400)
  - Authentication protection (401)
- frontend/onboarding.html UI page structure and script tags.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from flask_app import app
from src.config.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"


@pytest.fixture
def client() -> FlaskClient:
    os.environ["TESTING"] = "true"
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def api_key() -> str:
    return get_settings().portfolioiq_api_key


class TestCSVImportEndpoint:
    def test_import_requires_authentication(self, client: FlaskClient):
        res = client.post("/api/v1/holdings/import-csv")
        assert res.status_code == 401

    def test_import_empty_payload(self, client: FlaskClient, api_key: str):
        res = client.post(
            "/api/v1/holdings/import-csv",
            headers={"X-API-Key": api_key},
            json={},
        )
        assert res.status_code == 400
        data = res.get_json()
        assert data["error"]["code"] in ["EMPTY_FILE", "MISSING_FILE"]

    def test_import_missing_columns(self, client: FlaskClient, api_key: str):
        csv_data = "Instrument,CMP,LTP\nINFY,1500.00,1510.00"
        res = client.post(
            "/api/v1/holdings/import-csv",
            headers={"X-API-Key": api_key},
            json={"csv_text": csv_data},
        )
        assert res.status_code == 400
        data = res.get_json()
        assert data["error"]["code"] == "INVALID_CSV"
        assert "Required column" in data["error"]["message"]

    def test_import_valid_zerodha_holdings(self, client: FlaskClient, api_key: str):
        csv_data = (
            "Instrument,Qty.,Avg. cost,LTP,Cur. val,P&L,Net chg.,Day chg.\n"
            "INFY,10,1450.50,1520.00,15200.00,695.00,4.79%,0.50%\n"
            "TCS,5,3500.00,3600.00,18000.00,500.00,2.86%,-0.20%\n"
        )
        res = client.post(
            "/api/v1/holdings/import-csv",
            headers={"X-API-Key": api_key},
            json={"csv_text": csv_data},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["ok"] is True
        result = data["data"]
        assert result["imported_count"] == 2
        symbols = [h["tradingsymbol"] for h in result["holdings"]]
        assert "INFY" in symbols
        assert "TCS" in symbols

        # Verify quantities and average prices
        infy = next(h for h in result["holdings"] if h["tradingsymbol"] == "INFY")
        assert infy["quantity"] == 10
        assert float(infy["average_price"]) == 1450.50

    def test_import_valid_tradebook(self, client: FlaskClient, api_key: str):
        csv_data = (
            "symbol,trade_date,exchange,segment,series,trade_type,quantity,price,order_id,trade_id\n"
            "RELIANCE,2026-09-01,NSE,EQ,EQ,buy,15,2400.00,ord1,trd1\n"
            "RELIANCE,2026-09-05,NSE,EQ,EQ,buy,5,2600.00,ord2,trd2\n"
        )
        res = client.post(
            "/api/v1/holdings/import-csv",
            headers={"X-API-Key": api_key},
            json={"csv_text": csv_data},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["ok"] is True
        result = data["data"]
        assert result["imported_count"] == 1
        rel = result["holdings"][0]
        assert rel["tradingsymbol"] == "RELIANCE"
        assert rel["quantity"] == 20
        # Weighted average: (15*2400 + 5*2600)/20 = (36000 + 13000)/20 = 2450.00
        assert float(rel["average_price"]) == 2450.00

    def test_import_via_file_upload(self, client: FlaskClient, api_key: str):
        csv_bytes = b"Symbol,Quantity,Average Price\nHDFCBANK,25,1600.00\n"
        data = {
            "file": (io.BytesIO(csv_bytes), "holdings.csv", "text/csv"),
        }
        res = client.post(
            "/api/v1/holdings/import-csv",
            headers={"X-API-Key": api_key},
            content_type="multipart/form-data",
            data=data,
        )
        assert res.status_code == 200
        result = res.get_json()["data"]
        assert result["imported_count"] == 1
        assert result["holdings"][0]["tradingsymbol"] == "HDFCBANK"
        assert result["holdings"][0]["quantity"] == 25


class TestOnboardingPage:
    def test_onboarding_html_exists(self):
        page = FRONTEND_DIR / "onboarding.html"
        assert page.exists()
        assert page.stat().st_size > 1000

    def test_onboarding_html_structure(self):
        content = (FRONTEND_DIR / "onboarding.html").read_text(encoding="utf-8")
        assert 'id="panel-1"' in content
        assert 'id="panel-2"' in content
        assert 'id="panel-3"' in content
        assert 'id="drop-zone"' in content
        assert 'id="slider-target-equity"' in content
        assert 'id="input-cash-buffer"' in content
        assert 'id="slider-drift-threshold"' in content

    def test_onboarding_includes_scripts(self):
        content = (FRONTEND_DIR / "onboarding.html").read_text(encoding="utf-8")
        assert '<script src="js/session.js"></script>' in content
        assert '<script src="js/export.js"></script>' in content
        assert '<script src="js/api.js"></script>' in content
