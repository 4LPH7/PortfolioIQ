"""Optimiser preview and allocation-profile endpoints, and drift picking up saved targets."""

from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import patch

import pytest

from flask_app import app
from src.analytics.valuator import HoldingValuation, PortfolioValuation
from src.config.settings import get_settings

TOKENS = (2_147_000_201, 2_147_000_202)


@pytest.fixture
def client():
    os.environ["TESTING"] = "true"
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def headers():
    return {"X-API-Key": get_settings().portfolioiq_api_key}


def _holding(sym, token, weight, value):
    return HoldingValuation(
        instrument_token=token,
        tradingsymbol=sym,
        exchange="NSE",
        isin=None,
        sector="IT",
        industry=None,
        quantity=10,
        t1_quantity=0,
        average_price=Decimal("100"),
        current_price=Decimal("100"),
        close_price=None,
        price_source="live",
        current_value=Decimal(value),
        weight_pct=Decimal(weight),
    )


# ── Validation at the boundary ──


def test_profile_targets_over_100_are_rejected(client, headers):
    res = client.post(
        "/api/v1/rebalance/profiles",
        headers=headers,
        json={
            "profile_name": "Too much",
            "targets": [
                {"instrument_token": 1, "target_weight_pct": 60},
                {"instrument_token": 2, "target_weight_pct": 50},
            ],
        },
    )
    assert res.status_code == 422


def test_profile_duplicate_instrument_is_rejected(client, headers):
    res = client.post(
        "/api/v1/rebalance/profiles",
        headers=headers,
        json={
            "profile_name": "Dup",
            "targets": [
                {"instrument_token": 1, "target_weight_pct": 10},
                {"instrument_token": 1, "target_weight_pct": 10},
            ],
        },
    )
    assert res.status_code == 422


def test_optimize_rejects_unknown_method(client, headers):
    res = client.post("/api/v1/rebalance/optimize", headers=headers, json={"method": "kelly"})
    assert res.status_code == 422


def test_optimize_requires_api_key(client):
    assert client.post("/api/v1/rebalance/optimize", json={}).status_code == 401


def test_optimize_uses_configured_cap_and_cash_buffer(client, headers):
    valuation = PortfolioValuation(
        holdings=[_holding("AAA", 1, "50", "5000"), _holding("BBB", 2, "50", "5000")],
        total_aum=Decimal("10000"),
    )
    with (
        patch("src.analytics.valuator.compute_portfolio_valuation", return_value=valuation),
        patch(
            "src.analytics.drift_detector._get_active_targets",
            return_value={("STOCK", "AAA"): {"target_weight_pct": Decimal("40")}},
        ),
        patch("src.analytics.drift_detector._get_system_config_value", return_value=Decimal("20")),
        patch("src.analytics.optimizer.build_suggestion", return_value={"status": "ok"}) as build,
    ):
        res = client.post("/api/v1/rebalance/optimize", headers=headers, json={"method": "hrp"})
    assert res.status_code == 200
    args, kwargs = build.call_args
    assert args[1] == "hrp"
    assert [h["symbol"] for h in args[0]] == ["AAA", "BBB"]
    assert kwargs["cap_pct"] == 20.0
    assert kwargs["cash_floor_pct"] == pytest.approx(
        float(get_settings().rebalancer_cash_buffer_pct) * 100
    )
    assert kwargs["current_targets_pct"] == {"AAA": 40.0}
    assert kwargs["aum"] == 10000.0


# ── Drift reads saved STOCK and CASH targets ──


def test_drift_matches_stock_and_cash_targets(monkeypatch):
    from src.analytics import drift_detector

    rows = [
        {
            "allocation_type": "STOCK",
            "sector": None,
            "tradingsymbol": "AAA",
            "target_weight_pct": 30,
            "drift_threshold_pct": 2,
        },
        {
            "allocation_type": "CASH",
            "sector": None,
            "tradingsymbol": None,
            "target_weight_pct": 5,
            "drift_threshold_pct": 1,
        },
    ]
    monkeypatch.setattr(drift_detector, "execute_sql", lambda _q, _p=None: rows)
    monkeypatch.setattr(drift_detector, "_get_system_config_value", lambda *_: Decimal("80"))
    valuation = PortfolioValuation(
        holdings=[_holding("AAA", 1, "60", "6000"), _holding("BBB", 2, "40", "4000")],
        total_aum=Decimal("10000"),
        available_cash=Decimal("0"),
        net_worth=Decimal("10000"),
    )
    signals = drift_detector.detect_drift(valuation=valuation)
    stock = [s for s in signals if s.name == "AAA" and s.target_weight_pct == Decimal("30")]
    cash = [s for s in signals if s.name == "CASH"]
    assert stock and stock[0].drift_pct == Decimal("30")
    assert cash and cash[0].target_weight_pct == Decimal("5")


# ── Save, list, activate (rolled back after each test) ──


def _seed_instruments():
    from src.db.connection import execute_sql

    for token, sym in zip(TOKENS, ("OPTTEST1", "OPTTEST2"), strict=True):
        execute_sql(
            """INSERT INTO instrument_master (instrument_token, exchange_token, tradingsymbol, exchange)
               VALUES (:token, :token, :symbol, 'NSE')
               ON CONFLICT (instrument_token) DO UPDATE SET tradingsymbol = EXCLUDED.tradingsymbol""",
            {"token": token, "symbol": sym},
        )


def _save(client, headers, name, weights=(40.0, 35.5)):
    return client.post(
        "/api/v1/rebalance/profiles",
        headers=headers,
        json={
            "user_id": "opt-test",
            "profile_name": name,
            "targets": [
                {"instrument_token": t, "target_weight_pct": w}
                for t, w in zip(TOKENS, weights, strict=True)
            ],
        },
    )


@pytest.mark.postgres
def test_saved_profile_is_inactive_until_activated(db_session, client, headers):
    from src.analytics.drift_detector import _get_active_targets

    _seed_instruments()
    first = _save(client, headers, "Risk parity A")
    assert first.status_code == 201
    assert first.get_json()["data"]["is_active"] is False
    assert _get_active_targets("opt-test") == {}

    listed = client.get("/api/v1/rebalance/profiles?user_id=opt-test", headers=headers).get_json()
    profile = listed["data"][0]
    assert profile["is_active"] is False
    targets = {t["name"]: t for t in profile["targets"]}
    assert targets["OPTTEST1"]["target_weight_pct"] == 40.0
    assert targets["OPTTEST1"]["drift_threshold_pct"] == 10.0  # 25% of the target
    assert targets["CASH"]["target_weight_pct"] == 24.5  # the rest

    pid = first.get_json()["data"]["id"]
    res = client.post(
        f"/api/v1/rebalance/profiles/{pid}/activate", headers=headers, json={"user_id": "opt-test"}
    )
    assert res.status_code == 200
    active = _get_active_targets("opt-test")
    assert active[("STOCK", "OPTTEST1")]["target_weight_pct"] == Decimal("40.000")
    assert active[("CASH", "CASH")]["target_weight_pct"] == Decimal("24.500")

    second = _save(client, headers, "Risk parity B", weights=(10.0, 10.0)).get_json()["data"]["id"]
    client.post(
        f"/api/v1/rebalance/profiles/{second}/activate",
        headers=headers,
        json={"user_id": "opt-test"},
    )
    states = {
        p["id"]: p["is_active"]
        for p in client.get(
            "/api/v1/rebalance/profiles?user_id=opt-test", headers=headers
        ).get_json()["data"]
    }
    assert states == {pid: False, second: True}


@pytest.mark.postgres
def test_profile_conflicts_and_missing_profile(db_session, client, headers):
    _seed_instruments()
    assert _save(client, headers, "Same name").status_code == 201
    dup = _save(client, headers, "Same name")
    assert dup.status_code == 409
    assert "already exists" in dup.get_json()["error"]["message"]

    unknown = client.post(
        "/api/v1/rebalance/profiles",
        headers=headers,
        json={
            "user_id": "opt-test",
            "profile_name": "Ghost",
            "targets": [{"instrument_token": 2_147_000_299, "target_weight_pct": 10}],
        },
    )
    assert unknown.status_code == 409

    missing = client.post(
        "/api/v1/rebalance/profiles/999999999/activate",
        headers=headers,
        json={"user_id": "opt-test"},
    )
    assert missing.status_code == 404
