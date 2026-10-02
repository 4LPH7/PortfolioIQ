"""
Tests for Stock Analyzer 5-Method Analytics & Frontend Integration.
Verifies that all 5 analytical methods (RSI, MACD, Bollinger Bands, Linear Regression, Monte Carlo)
are computed and exposed with proper data contracts and UI elements.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from flask.testing import FlaskClient
import pytest

from flask_app import app
from src.config.settings import get_settings


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


def test_analyzer_html_structure_and_methods():
    html_path = Path("frontend/analyzer.html")
    assert html_path.exists(), "frontend/analyzer.html must exist"
    content = html_path.read_text(encoding="utf-8")

    # Verify no emojis
    emojis = re.findall(r"[\U0001F300-\U0001F6FF\U0001F900-\U0001F9FF\u2600-\u26FF\u2700-\u27BF]", content)
    assert not emojis, f"Found emojis in analyzer.html: {emojis}"

    # Verify Google Material Symbols stylesheet
    assert "Material+Symbols+Outlined" in content

    # Verify All 5 Method Tabs exist
    assert 'data-tab="rsi"' in content
    assert 'data-tab="macd"' in content
    assert 'data-tab="bb"' in content
    assert 'data-tab="lr"' in content
    assert 'data-tab="mc"' in content

    # Verify Tab Panels exist
    assert 'id="tab-rsi"' in content
    assert 'id="tab-macd"' in content
    assert 'id="tab-bb"' in content
    assert 'id="tab-lr"' in content
    assert 'id="tab-mc"' in content

    # Verify Symbol Select, Custom Input, Run Button, and Quick Chips
    assert 'id="symbol-select"' in content
    assert 'id="symbol-input"' in content
    assert 'id="run-btn"' in content
    assert "quickSelect('INFY')" in content
    assert "quickSelect('GOLDCASE')" in content
    assert "quickSelect('MONQ50')" in content

    # Verify Plotly charts
    assert 'id="rsi-chart"' in content or 'Plotly.newPlot("rsi-chart"' in content
    assert 'id="macd-chart"' in content or 'Plotly.newPlot("macd-chart"' in content
    assert 'id="bb-chart"' in content or 'Plotly.newPlot("bb-chart"' in content
    assert 'id="lr-chart"' in content or 'Plotly.newPlot("lr-chart"' in content
    assert 'id="mc-chart"' in content or 'Plotly.newPlot("mc-chart"' in content

    # Verify Export CSV button
    assert 'data-export="analyzer-signals-table"' in content

    # Verify Critical Script Order
    all_scripts = re.findall(r'<script src="([^"]+)"></script>', content)
    local_scripts = [s for s in all_scripts if s.startswith("js/")]
    expected_order = ["js/session.js", "js/export.js", "js/alerts.js", "js/api.js"]
    assert local_scripts == expected_order, f"Script order mismatch: {local_scripts}"


def test_stock_analysis_endpoint_contract(client: FlaskClient, api_key: str):
    """Verify that /api/v1/analysis/<symbol> returns all 5 analytical methods."""
    res = client.get("/api/v1/analysis/INFY", headers={"X-API-Key": api_key})
    assert res.status_code == 200
    data = res.json.get("data", {})

    # Fundamental fields
    assert data.get("tradingsymbol") == "INFY"
    assert "current_price" in data
    assert "composite" in data

    # 1. RSI
    rsi = data.get("rsi")
    assert rsi is not None, "RSI must be present"
    assert "current_rsi" in rsi
    assert "signal" in rsi
    assert "rsi_series" in rsi
    assert "dates" in rsi

    # 2. MACD
    macd = data.get("macd")
    assert macd is not None, "MACD must be present"
    assert "macd_line" in macd
    assert "signal_line" in macd
    assert "histogram" in macd
    assert "macd_series" in macd
    assert "signal_series" in macd
    assert "hist_series" in macd
    assert "dates" in macd

    # 3. Bollinger Bands
    bb = data.get("bollinger")
    assert bb is not None, "Bollinger must be present"
    assert "upper_band" in bb
    assert "middle_band" in bb
    assert "lower_band" in bb
    assert "percent_b" in bb
    assert "upper_series" in bb
    assert "middle_series" in bb
    assert "lower_series" in bb
    assert "price_series" in bb
    assert "dates" in bb

    # 4. Linear Regression
    lr = data.get("linear_regression")
    assert lr is not None, "Linear Regression must be present"
    assert "slope" in lr
    assert "slope_pct" in lr
    assert "r_squared" in lr
    assert "predicted_30d" in lr
    assert "regression_series" in lr
    assert "price_series" in lr
    assert "dates" in lr

    # 5. Monte Carlo
    mc = data.get("monte_carlo")
    assert mc is not None, "Monte Carlo must be present"
    assert "p10" in mc
    assert "p25" in mc
    assert "p50" in mc
    assert "p75" in mc
    assert "p90" in mc
    assert "prob_profit" in mc
    assert "fan_dates" in mc
    assert "fan_p10" in mc
    assert "fan_p50" in mc
    assert "fan_p90" in mc

    # Composite Score
    comp = data.get("composite")
    assert comp is not None, "Composite score must be present"
    assert "score" in comp
    assert "signal" in comp
    assert "rsi_score" in comp
    assert "macd_score" in comp
    assert "bollinger_score" in comp
    assert "lr_score" in comp
