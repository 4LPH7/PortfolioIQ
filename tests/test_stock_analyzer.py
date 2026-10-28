"""
Tests for the Stock Analyzer page. It renders the evidence-based signal from
/api/v1/signals/<symbol>: composite score, walk-forward indicators and the calibrated
Monte Carlo cone. The retired 5-method heuristic view (predictor.py) must stay gone.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from flask.testing import FlaskClient

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
    emojis = re.findall(
        r"[\U0001F300-\U0001F6FF\U0001F900-\U0001F9FF\u2600-\u26FF\u2700-\u27BF]", content
    )
    assert not emojis, f"Found emojis in analyzer.html: {emojis}"

    # Verify Google Material Symbols stylesheet
    assert "Material+Symbols+Outlined" in content

    # Reads the evidence-based signal, not the retired /analysis endpoint
    assert "apiGet(`/api/signals/${" in content
    assert "/api/analysis/${" not in content
    for retired in ('data-tab="rsi"', 'data-tab="macd"', 'data-tab="bb"', 'data-tab="lr"'):
        assert retired not in content, retired
    assert "five models" not in content and "5 analytical models" not in content

    # Verify Symbol Select, Custom Input, Run Button, and Quick Chips
    assert 'id="symbol-select"' in content
    assert 'id="symbol-input"' in content
    assert 'id="run-btn"' in content
    assert "quickSelect('INFY')" in content
    assert "quickSelect('GOLDCASE')" in content
    assert "quickSelect('MONQ50')" in content

    # Calibrated Monte Carlo cone and the walk-forward indicator table
    assert 'Plotly.newPlot("mc-chart"' in content
    assert 'id="analyzer-signals-tbody"' in content

    # Verify Export CSV button
    assert 'data-export="analyzer-signals-table"' in content

    # Verify Critical Script Order
    all_scripts = re.findall(r'<script src="([^"]+)"></script>', content)
    local_scripts = [s for s in all_scripts if s.startswith("js/")]
    expected_order = ["js/session.js", "js/export.js", "js/alerts.js", "js/api.js"]
    assert local_scripts == expected_order, f"Script order mismatch: {local_scripts}"
