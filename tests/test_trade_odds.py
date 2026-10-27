"""Stop and target odds: known-answer price series, gap fills, ties, break-even, sizing, API."""

from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.analytics.trade_odds import compute_odds, size_position, verdict
from src.analytics.valuator import HoldingValuation, PortfolioValuation


def _bars(ratios, n=400, start=100.0):
    """Every day repeats the same (open, high, low, close) ratios to the previous close."""
    o, h, low, c = ratios
    closes = start * c ** np.arange(n)
    prev = np.concatenate([[start / c], closes[:-1]])
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {
            "Open": prev * o,
            "High": prev * h,
            "Low": prev * low,
            "Close": closes,
            "Raw Close": closes,
        },
        index=idx,
    )


def _random_bars(n=900, seed=3, sigma=0.015):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, sigma, n)
    r -= r.mean()  # no drift, so up and down are equally likely in log terms
    c = 100 * np.exp(np.cumsum(r))
    prev = np.concatenate([[100.0], c[:-1]])
    idx = pd.bdate_range("2023-01-02", periods=n)
    hi, lo = np.maximum(prev, c) * 1.004, np.minimum(prev, c) * 0.996
    return pd.DataFrame(
        {"Open": prev, "High": hi, "Low": lo, "Close": c, "Raw Close": c}, index=idx
    )


def test_steady_rise_always_hits_the_target():
    bars = _bars((1.0, 1.012, 0.999, 1.01))
    entry = bars["Close"].iloc[-1]
    out = compute_odds(bars, stop=entry * 0.9, target=entry * 1.05, horizon_days=10, replays=2000)
    assert out["odds"]["target_first"] == 1.0 and out["odds"]["green"] == 1.0
    assert out["history"]["target_first"] == out["history"]["windows"]
    assert out["median_days"]["to_target"] == 5  # 1.01^5 > 1.05 on the 5th day's high
    assert out["verdict"]["text"] == "Strongly toward the target."


def test_steady_fall_always_hits_the_stop():
    bars = _bars((1.0, 1.001, 0.988, 0.99))
    entry = bars["Close"].iloc[-1]
    out = compute_odds(bars, stop=entry * 0.95, target=entry * 1.1, horizon_days=20, replays=2000)
    assert out["odds"]["stop_first"] == 1.0
    assert out["history"]["stop_first"] == out["history"]["windows"]


def test_a_day_touching_both_levels_counts_as_the_stop():
    bars = _bars((1.0, 1.10, 0.90, 1.0))
    entry = bars["Close"].iloc[-1]
    out = compute_odds(bars, stop=entry * 0.95, target=entry * 1.05, horizon_days=5, replays=500)
    assert out["odds"]["stop_first"] == 1.0


def test_a_gap_below_the_stop_fills_at_the_open():
    bars = _bars((0.90, 0.92, 0.88, 0.90))
    entry = float(bars["Close"].iloc[-1])
    out = compute_odds(bars, stop=entry * 0.95, target=entry * 1.05, horizon_days=5, replays=500)
    assert out["net_pnl"]["worst_stop_fill"] == pytest.approx(entry * 0.90, rel=1e-6)
    assert out["net_pnl"]["mean_r"] < -1.9  # a 10% gap through a 5% stop loses about 2R


def test_driftless_walk_gives_even_odds_for_a_symmetric_plan():
    bars = _random_bars()
    entry = float(bars["Close"].iloc[-1])
    out = compute_odds(
        bars,
        stop=entry * np.exp(-0.06),
        target=entry * np.exp(0.06),
        horizon_days=120,
        lookback_years=3,
    )
    assert abs(out["odds"]["target_first"] - out["odds"]["stop_first"]) < 0.06
    assert out["break_even"]["target_share_needed"] > 0.5  # charges tilt break-even past 50%


def test_same_seed_repeats_and_another_seed_differs():
    bars = _random_bars()
    e = float(bars["Close"].iloc[-1])
    a = compute_odds(bars, stop=e * 0.93, target=e * 1.07, seed=19)
    b = compute_odds(bars, stop=e * 0.93, target=e * 1.07, seed=19)
    c = compute_odds(bars, stop=e * 0.93, target=e * 1.07, seed=20)
    assert a["odds"] == b["odds"] and a["odds"] != c["odds"]


def test_break_even_falls_with_a_bigger_target():
    bars = _random_bars()
    e = float(bars["Close"].iloc[-1])
    one_r = compute_odds(bars, stop=e * 0.95, target=e * 1.05, replays=1000)
    two_r = compute_odds(bars, stop=e * 0.95, target=e * 1.10, replays=1000)
    assert 0.5 < one_r["break_even"]["target_share_needed"] < 0.53
    assert 1 / 3 < two_r["break_even"]["target_share_needed"] < 0.36


def test_rejects_bad_levels_and_short_history():
    bars = _random_bars()
    e = float(bars["Close"].iloc[-1])
    with pytest.raises(ValueError, match="stop < entry < target"):
        compute_odds(bars, stop=e * 1.01, target=e * 1.05)
    with pytest.raises(ValueError, match="needs 250"):
        compute_odds(bars.tail(100), stop=e * 0.9, target=e * 1.1)
    broken = bars.copy()
    broken.iloc[50, broken.columns.get_loc("Close")] = 0.0
    with pytest.raises(ValueError, match="zero or missing"):
        compute_odds(broken, stop=e * 0.9, target=e * 1.1)


def test_verdict_bands():
    assert verdict(0.26, 0.24, 0.5, 0.5)["text"] == "Close to break-even after charges."
    assert verdict(0.30, 0.20, 0.5, 0.5)["text"] == "Leans toward the target."
    assert verdict(0.10, 0.40, 0.5, 0.5)["text"] == "Strongly toward the stop."
    assert verdict(0.10, 0.10, 0.5, 0.8)["notes"]  # most replays hit neither
    assert verdict(0, 0, 0.5, 1)["edge"] is None


def test_size_by_max_loss_includes_charges():
    s = size_position(100, 95, 110, 5000, None, 10_000_000, 0, 15)
    assert s["binding"] == "max loss" and s["loss_at_stop_inr"] <= 5000
    over = size_position(100, 95, 110, s["loss_at_stop_inr"] + 0.001, None, 10_000_000, 0, 15)
    assert over["quantity"] == s["quantity"]  # the next share would pass the limit
    assert s["quantity"] < 1000  # 5000 / 5 ignoring charges


def test_size_caps_by_concentration_then_cash():
    cap = size_position(100, 95, 110, 50_000, None, 1_000_000, 140_000, 15)
    assert cap["binding"] == "concentration limit" and cap["quantity"] == 100
    cash = size_position(100, 95, 110, 50_000, 2_000, 1_000_000, 0, 15)
    assert cash["binding"] == "available cash" and cash["quantity"] == 19


# ── API ──


@pytest.fixture
def client():
    from flask_app import app

    os.environ["TESTING"] = "true"
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def headers():
    from src.config.settings import get_settings

    return {"X-API-Key": get_settings().portfolioiq_api_key}


def test_api_returns_odds_and_sizing(client, headers):
    bars = _random_bars()
    e = round(float(bars["Close"].iloc[-1]), 2)
    holding = HoldingValuation(
        instrument_token=1,
        tradingsymbol="TESTCO",
        exchange="NSE",
        isin=None,
        sector=None,
        industry=None,
        quantity=10,
        t1_quantity=0,
        average_price=Decimal("90"),
        current_price=Decimal(str(e)),
        close_price=None,
        price_source="live",
    )
    val = PortfolioValuation(holdings=[holding], total_aum=Decimal("1000000"))
    with (
        patch("src.analytics.historical_cache.get_or_sync_historical_bars", return_value=bars),
        patch("src.analytics.valuator.compute_portfolio_valuation", return_value=val),
    ):
        res = client.post(
            "/api/v1/analysis/odds",
            headers=headers,
            json={"symbol": "TESTCO", "stop": e * 0.95, "target": e * 1.1, "max_loss_inr": 2000},
        )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["sizing"]["quantity"] == data["break_even"]["position_qty"] > 0
    assert data["sizing"]["cash_known"] is False
    assert abs(sum(data["odds"][k] for k in ("target_first", "stop_first", "neither")) - 1) < 1e-3


def test_api_rejects_bad_input(client, headers):
    bad = client.post(
        "/api/v1/analysis/odds",
        headers=headers,
        json={"symbol": "TESTCO", "stop": 110, "target": 100},
    )
    assert bad.status_code == 422
    with patch(
        "src.analytics.historical_cache.get_or_sync_historical_bars",
        return_value=_random_bars(n=120),
    ):
        short = client.post(
            "/api/v1/analysis/odds",
            headers=headers,
            json={"symbol": "TESTCO", "stop": 50, "target": 500},
        )
    assert short.status_code == 422 and "needs 250" in short.get_json()["error"]["message"]
