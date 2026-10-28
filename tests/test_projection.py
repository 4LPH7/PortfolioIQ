"""Portfolio projection: exact answers on constant returns, whole-day replay keeping hedges
hedged, cash drag, the benchmark comparison, data exclusions and the API."""

from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.analytics.projection import YEAR, daily_returns, project
from src.analytics.valuator import HoldingValuation, PortfolioValuation


def _bars(returns, start="2021-01-01"):
    closes = 100 * np.cumprod(np.concatenate([[1.0], 1 + np.asarray(returns)]))
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"Close": closes}, index=idx)


def test_constant_return_projects_exactly():
    r = np.full(600, 0.0004)
    out = project(r, None, 1_000_000, 1.0, 3, 1000, 19)
    expected = 1_000_000 * 1.0004 ** (3 * YEAR)
    assert out["end_value"]["p5"] == out["end_value"]["p95"] == pytest.approx(expected, abs=1)
    assert out["prob_end_below_start"] == 0.0
    assert out["max_drawdown_pct"]["median"] == 0.0
    assert out["cagr_pct"]["p50"] == pytest.approx((1.0004**YEAR - 1) * 100, abs=0.01)
    assert out["yearly"][0]["p50"] == pytest.approx(1_000_000 * 1.0004**YEAR, abs=1)
    assert out["prob_beats_benchmark"] is None


def test_cash_earns_nothing():
    r = np.full(600, 0.0004)
    out = project(r, None, 1_000_000, 0.5, 2, 1000, 19)
    assert out["end_value"]["p50"] == pytest.approx(1_000_000 * 1.0002 ** (2 * YEAR), abs=1)


def test_whole_days_keep_a_hedge_hedged():
    rng = np.random.default_rng(5)
    x = rng.normal(0, 0.02, 700)
    holdings = [{"symbol": "LONG", "value": 50_000}, {"symbol": "SHORT", "value": 50_000}]
    bars = {"LONG": _bars(x), "SHORT": _bars(-x)}
    rets = daily_returns(holdings, bars.__getitem__, 2, benchmark=None)
    # Same-day moves cancel only if each replayed day moves both holdings together
    assert np.abs(rets["portfolio"]).max() < 1e-12
    out = project(rets["portfolio"], None, 100_000, 1.0, 5, 2000, 7)
    assert out["end_value"]["p5"] == out["end_value"]["p95"] == pytest.approx(100_000, abs=1)


def test_benchmark_is_replayed_on_the_same_days():
    rng = np.random.default_rng(9)
    b = rng.normal(0.0003, 0.012, 800)
    ahead = project(b + 0.0002, b, 100_000, 1.0, 3, 2000, 1)
    level = project(b, b, 100_000, 1.0, 3, 2000, 1)
    assert ahead["prob_beats_benchmark"] == 1.0 and level["prob_beats_benchmark"] == 0.0


def test_same_seed_repeats_and_drawdowns_are_negative():
    rng = np.random.default_rng(2)
    r = rng.normal(0.0004, 0.015, 900)
    a = project(r, None, 1e6, 1.0, 5, 2000, 19)
    b = project(r, None, 1e6, 1.0, 5, 2000, 19)
    assert a["end_value"] == b["end_value"]
    assert a["max_drawdown_pct"]["worst_10pct"] < a["max_drawdown_pct"]["median"] < 0
    assert sum(a["histogram"]["counts"]) + a["histogram"]["outside_range"] == 2000


def test_daily_returns_weights_by_value_and_explains_exclusions():
    rng = np.random.default_rng(4)
    a, b = rng.normal(0, 0.01, 600), rng.normal(0, 0.01, 600)
    bars = {"AAA": _bars(a), "BBB": _bars(b), "NEWIPO": _bars(a[:100]), "NIFTY 50 TRI": _bars(a)}

    def load(sym):
        if sym == "BROKEN":
            raise OSError("feed down")
        return bars[sym]

    holdings = [
        {"symbol": "AAA", "value": 30_000},
        {"symbol": "BBB", "value": 10_000},
        {"symbol": "NEWIPO", "value": 5_000},
        {"symbol": "BROKEN", "value": 5_000},
    ]
    rets = daily_returns(holdings, load, 2)
    assert rets["symbols"] == ["AAA", "BBB"] and rets["covered_value"] == 40_000
    np.testing.assert_allclose(
        rets["portfolio"], 0.75 * a[-len(rets["portfolio"]) :] + 0.25 * b[-len(rets["portfolio"]) :]
    )
    assert {e["symbol"] for e in rets["excluded"]} == {"NEWIPO", "BROKEN"}
    assert rets["benchmark"] is not None
    with pytest.raises(ValueError, match="a year of price history"):
        daily_returns([{"symbol": "NEWIPO", "value": 1}], load, 2)


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


def _holding(sym, value):
    return HoldingValuation(
        instrument_token=1,
        tradingsymbol=sym,
        exchange="NSE",
        isin=None,
        sector=None,
        industry=None,
        quantity=1,
        t1_quantity=0,
        average_price=Decimal(value),
        current_price=Decimal(value),
        close_price=None,
        price_source="live",
    )


def test_api_projects_holdings_plus_cash(client, headers):
    rng = np.random.default_rng(8)
    bars = _bars(rng.normal(0.0004, 0.01, 900))
    val = PortfolioValuation(holdings=[_holding("AAA", "60000")], available_cash=Decimal("40000"))
    with (
        patch("src.analytics.valuator.compute_portfolio_valuation", return_value=val),
        patch("src.analytics.historical_cache.get_or_sync_historical_bars", return_value=bars),
    ):
        res = client.post(
            "/api/v1/portfolio/projection", headers=headers, json={"years": 3, "simulations": 1000}
        )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["start_value"] == 100_000 and data["cash_inr"] == 40_000
    assert len(data["yearly"]) == 3 and data["history"]["days"] > 250
    assert data["prob_beats_benchmark"] is not None


def test_api_rejects_an_empty_portfolio(client, headers):
    with patch(
        "src.analytics.valuator.compute_portfolio_valuation", return_value=PortfolioValuation()
    ):
        res = client.post("/api/v1/portfolio/projection", headers=headers, json={})
    assert res.status_code == 422
    bad = client.post("/api/v1/portfolio/projection", headers=headers, json={"years": 30})
    assert bad.status_code == 422


def test_stale_and_suspended_holdings_are_left_out_with_a_reason():
    rng = np.random.default_rng(6)
    live = _bars(rng.normal(0, 0.01, 600))
    stale = live.iloc[:-30]  # stopped trading a month before the others
    frozen = live.copy()
    frozen.iloc[-30:] = frozen.iloc[-31]  # price has not moved in 30 days
    bars = {"LIVE": live, "STALE": stale, "FROZEN": frozen}
    holdings = [{"symbol": s, "value": 10_000} for s in bars]
    rets = daily_returns(holdings, bars.__getitem__, 2, benchmark=None)
    assert rets["symbols"] == ["LIVE"]
    reasons = {e["symbol"]: e["reason"] for e in rets["excluded"]}
    assert reasons["STALE"].startswith("last price is ")
    assert reasons["FROZEN"] == "price has not moved in the last 20 days"
