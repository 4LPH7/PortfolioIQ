"""
Tests for Quantitative Signals REST API Endpoints & Legacy Analysis Aliasing
(src/api/v1/blueprint.py).
Phase 5: Make the Signal Engine Evidence-Based (Plan 05-07)

Validates:
- GET /api/v1/signals/<symbol>: Returns complete HoldingSignalDTO with calibrated Monte Carlo cones (P10..P90).
- GET /api/v1/signals: Returns signals for all active holdings.
- POST /api/v1/signals/backtest: Triggers walk-forward backtest returning BacktestRunDTO.
- GET /api/v1/signals/history: Retrieves historical daily signal snapshots with multi-horizon outcomes.
- Legacy GET /api/v1/analysis/<symbol>: Emits HTTP 299 Deprecation Warning header.
- API Key authentication and uniform error envelopes.
"""

import os
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

from flask_app import app
from src.config.settings import get_settings
from src.models.dtos import (
    BacktestRunDTO,
    CalibratedMonteCarloDTO,
    HoldingSignalDTO,
    IndicatorEvaluationDTO,
    SignalSnapshotDTO,
)


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


@pytest.fixture
def sample_holding_signal() -> HoldingSignalDTO:
    """Provide a realistic HoldingSignalDTO."""
    mc = CalibratedMonteCarloDTO(
        horizon_days=30,
        current_price=1500.0,
        degrees_of_freedom=4.2,
        scale_multiplier=1.15,
        empirical_coverage_80=81.2,
        p10=1410.5,
        p25=1460.0,
        p50=1510.0,
        p75=1565.0,
        p90=1620.0,
        prob_profit=54.2,
        fan_dates=["2025-06-02", "2025-06-03"],
        fan_p10=[1490.0, 1485.0],
        fan_p50=[1502.0, 1505.0],
        fan_p90=[1515.0, 1525.0],
    )
    ind = IndicatorEvaluationDTO(
        name="RSI",
        mean_ic=0.085,
        p_value=0.012,
        is_pruned=False,
        weight=0.55,
        score=75.0,
    )
    return HoldingSignalDTO(
        symbol="INFY",
        tradingsymbol="INFY",
        current_price=1500.0,
        composite_score=72.5,
        signal_label="BUY",
        status="PROVEN_EDGE",
        evidence_badge="PROVEN EDGE (Alpha: +14.2%, Trades: 12)",
        indicators=[ind],
        monte_carlo=mc,
        avg_buy_price=1450.0,
        data_start="2024-01-01",
        data_end="2025-05-30",
        data_points=252,
    )


class TestSignalsAPI:
    """Integration tests for /api/v1/signals/* routes."""

    def test_get_signal_symbol_requires_auth(self, client: FlaskClient):
        res = client.get("/api/v1/signals/INFY")
        assert res.status_code == 401
        assert res.json["error"]["code"] == "UNAUTHORIZED"

    def test_get_signal_symbol_success(
        self, client: FlaskClient, api_key: str, sample_holding_signal: HoldingSignalDTO
    ):
        with (
            patch("src.analytics.valuator.get_valuation_summary", return_value={"holdings": []}),
            patch(
                "src.analytics.signal_recorder.compute_holding_signal",
                return_value=sample_holding_signal,
            ),
        ):
            res = client.get(
                "/api/v1/signals/INFY",
                headers={"X-API-Key": api_key},
            )
            assert res.status_code == 200
            data = res.json["data"]
            assert data["tradingsymbol"] == "INFY"
            assert data["status"] == "PROVEN_EDGE"
            assert data["signal_label"] == "BUY"
            assert data["composite_score"] == 72.5

            # Assert calibrated Monte Carlo dispersion percentiles are returned
            assert data["monte_carlo"] is not None
            assert data["monte_carlo"]["p10"] == 1410.5
            assert data["monte_carlo"]["p50"] == 1510.0
            assert data["monte_carlo"]["p90"] == 1620.0
            assert "target_price" not in data["monte_carlo"]

    def test_get_portfolio_signals_success(
        self, client: FlaskClient, api_key: str, sample_holding_signal: HoldingSignalDTO
    ):
        with patch(
            "src.analytics.signal_recorder.compute_portfolio_signals",
            return_value=[sample_holding_signal],
        ):
            res = client.get(
                "/api/v1/signals",
                headers={"X-API-Key": api_key},
            )
            assert res.status_code == 200
            assert len(res.json["data"]) == 1
            assert res.json["data"][0]["tradingsymbol"] == "INFY"

    def test_post_signal_backtest_missing_symbol(self, client: FlaskClient, api_key: str):
        res = client.post(
            "/api/v1/signals/backtest",
            headers={"X-API-Key": api_key},
            json={},
        )
        assert res.status_code == 400
        assert res.json["error"]["code"] == "VALIDATION_ERROR"

    def test_post_signal_backtest_success(self, client: FlaskClient, api_key: str):
        mock_backtest = BacktestRunDTO(
            run_id=str(uuid.uuid4()),
            tradingsymbol="TCS",
            train_start_date=date(2024, 1, 1),
            train_end_date=date(2024, 12, 31),
            test_start_date=date(2025, 1, 1),
            test_end_date=date(2025, 3, 31),
            strategy_cagr=0.22,
            stock_cagr=0.14,
            benchmark_cagr=0.11,
            excess_cagr_vs_stock=0.08,
            excess_cagr_vs_benchmark=0.11,
            strategy_win_rate=0.58,
            strategy_profit_factor=2.0,
            total_trades=8,
            status="PROVEN_EDGE",
            passed_hurdle=True,
        )

        with patch(
            "src.analytics.signal_recorder.run_and_record_backtest",
            return_value=mock_backtest,
        ):
            res = client.post(
                "/api/v1/signals/backtest",
                headers={"X-API-Key": api_key},
                json={"symbol": "TCS", "train_window_days": 252, "test_window_days": 63},
            )
            assert res.status_code == 200
            data = res.json["data"]
            assert data["tradingsymbol"] == "TCS"
            assert data["strategy_cagr"] == 0.22
            assert data["excess_cagr_vs_benchmark"] == 0.11
            assert data["status"] == "PROVEN_EDGE"

    def test_get_signal_history_success(self, client: FlaskClient, api_key: str):
        snap = SignalSnapshotDTO(
            id=12,
            snapshot_date=date(2025, 5, 15),
            user_id="default",
            tradingsymbol="INFY",
            current_price=Decimal("1510.00"),
            composite_score=Decimal("68.0"),
            signal_label="BUY",
            status="PROVEN_EDGE",
            return_5d_stock=Decimal("0.025"),
            return_5d_benchmark=Decimal("0.010"),
            excess_return_5d=Decimal("0.015"),
        )
        with patch("src.db.repository.get_signal_snapshots", return_value=[snap]):
            res = client.get(
                "/api/v1/signals/history?symbol=INFY",
                headers={"X-API-Key": api_key},
            )
            assert res.status_code == 200
            data = res.json["data"]
            assert len(data) == 1
            assert data[0]["tradingsymbol"] == "INFY"
            assert (
                data[0]["excess_return_5d"] == "0.015"
                or float(data[0]["excess_return_5d"]) == 0.015
            )

    def test_legacy_analysis_endpoint_alias_and_deprecation_header(
        self, client: FlaskClient, api_key: str, sample_holding_signal: HoldingSignalDTO
    ):
        with (
            patch("src.analytics.valuator.get_valuation_summary", return_value={"holdings": []}),
            patch(
                "src.analytics.signal_recorder.compute_holding_signal",
                return_value=sample_holding_signal,
            ),
        ):
            res = client.get(
                "/api/v1/analysis/INFY",
                headers={"X-API-Key": api_key},
            )
            assert res.status_code == 200
            assert "Warning" in res.headers
            assert "299" in res.headers["Warning"]
            assert "Deprecated endpoint" in res.headers["Warning"]
            assert res.json["data"]["tradingsymbol"] == "INFY"
