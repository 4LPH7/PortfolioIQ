"""Market scanner: signal maths, the out-of-sample rule (no look-ahead), the verdict on planted
and random markets, costs, the NSE list, the price matrix, runs and the API."""

from __future__ import annotations

import os
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text

from src.analytics import scanner as sc


def _market(n_stocks=150, days=1260, drift_sd=0.0008, seed=1):
    """Each stock keeps its own drift (drift_sd > 0), so past winners keep winning."""
    rng = np.random.default_rng(seed)
    mu = rng.normal(0.0003, drift_sd, n_stocks)
    r = mu + rng.normal(0, 0.018, (days, n_stocks))
    idx = pd.bdate_range("2021-01-01", periods=days)
    cols = [f"S{i:03d}" for i in range(n_stocks)]
    return pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=idx, columns=cols)


# ── Pure maths ──


def test_signal_values_on_a_known_series():
    idx = pd.bdate_range("2022-01-03", periods=300)
    prices = pd.DataFrame({"A": 100 * 1.001 ** np.arange(300)}, index=idx)
    s = sc.compute_signals(prices)
    t = 299
    assert s["mom_12_1"]["A"].iloc[t] == pytest.approx(1.001 ** (t - 21) / 1.001 ** (t - 252) - 1)
    assert s["near_52w_high"]["A"].iloc[t] == pytest.approx(0.0)  # a steady rise is at its high
    assert s["low_vol"]["A"].iloc[t] == pytest.approx(0.0, abs=1e-12)
    assert s["reversal_1m"]["A"].iloc[t] == pytest.approx(-(1.001**21 - 1))
    assert np.isnan(s["mom_12_1"]["A"].iloc[200])  # not enough history yet


def test_month_ends_and_rank_ic():
    idx = pd.bdate_range("2024-01-01", "2024-03-15")
    assert [str(d.date()) for d in sc.month_ends(idx)] == ["2024-01-31", "2024-02-29", "2024-03-15"]
    x = pd.Series(np.arange(40.0))
    assert sc.rank_ic(x, x * 3) == pytest.approx(1.0)
    assert sc.rank_ic(x, -x) == pytest.approx(-1.0)
    assert sc.rank_ic(x.head(10), x.head(10)) is None  # too few stocks


def test_select_signals_needs_a_significant_positive_record():
    rng = np.random.default_rng(0)
    ics = pd.DataFrame(
        {
            "mom_12_1": 0.05 + rng.normal(0, 0.02, 24),  # strong, steady
            "low_vol": rng.normal(0, 0.05, 24),  # noise
            "trend_200": [0.2] * 6 + [np.nan] * 18,  # too few months
        }
    )
    sel = sc.select_signals(ics)
    assert sel["mom_12_1"]["used"] and sel["mom_12_1"]["weight"] == pytest.approx(1.0)
    assert not sel["low_vol"]["used"] and not sel["trend_200"]["used"]
    assert sel["trend_200"]["months"] == 6 and sel["trend_200"]["mean_ic"] is None


def test_backtest_only_learns_from_earlier_months():
    seen = []
    real = sc.select_signals

    def spy(ics):
        seen.append(len(ics))
        return real(ics)

    prices = _market(n_stocks=60, days=900)
    sigs = sc.compute_signals(prices)
    ends = sc.month_ends(prices.index)
    monthly = prices.loc[ends]
    fwd = monthly.shift(-1) / monthly - 1
    ics = pd.DataFrame(
        {k: [sc.rank_ic(sigs[k].loc[d], fwd.loc[d]) for d in ends] for k in sc.SIGNALS},
        index=ends,
    )
    with patch.object(sc, "select_signals", side_effect=spy):
        sc.backtest(sigs, monthly, ics, 0.0)
    # deciding month i uses IC rows 0..i-1, whose returns ended by month i's close
    assert seen == list(range(sc.MIN_IC_MONTHS, len(ends) - 1))


def test_planted_momentum_is_found_and_beats_the_universe():
    out = sc.compute_scan(_market())
    assert out["status"] == "edge"
    assert out["signals"]["mom_12_1"]["used"]
    bt = out["backtest"]
    assert bt["cagr_pct"] > bt["universe_cagr_pct"] and bt["composite_ic_t"] > 2
    assert out["short_history"]  # under five years of tested months
    top = out["ranking"][0]
    assert top["rank"] == 1 and top["score"] == 100.0
    assert set(top["signals"]) == set(sc.SIGNALS)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_markets_show_no_edge(seed):
    out = sc.compute_scan(_market(drift_sd=0.0, seed=seed))
    assert out["status"] == "no_edge"


def test_costs_come_off_the_portfolio_return():
    prices = _market(n_stocks=80, days=1000)
    sigs = sc.compute_signals(prices)
    ends = sc.month_ends(prices.index)
    monthly = prices.loc[ends]
    fwd = monthly.shift(-1) / monthly - 1
    ics = pd.DataFrame(
        {k: [sc.rank_ic(sigs[k].loc[d], fwd.loc[d]) for d in ends] for k in sc.SIGNALS},
        index=ends,
    )
    free = sc.backtest(sigs, monthly, ics, 0.0)
    costly = sc.backtest(sigs, monthly, ics, 0.01)
    assert costly["cagr_pct"] < free["cagr_pct"]
    assert costly["universe_cagr_pct"] == free["universe_cagr_pct"]
    assert costly["cost_drag_pct_per_year"] > 0 == free["cost_drag_pct_per_year"]


def test_too_few_stocks_is_refused():
    with pytest.raises(ValueError, match="have a year of prices"):
        sc.compute_scan(_market(n_stocks=10))


# ── NSE list ──


def _csv(n):
    rows = ["Company Name,Industry,Symbol,Series,ISIN Code"]
    rows += [f"Company {i} Ltd.,Industry {i % 5},SYM{i},EQ,INE{i:09d}" for i in range(n)]
    rows += ["Bad Ltd.,X,bad symbol!,EQ,INE0"]
    return ("﻿" + "\n".join(rows)).encode("utf-8")


def test_fetch_universe_parses_and_sanity_checks():
    resp = SimpleNamespace(raise_for_status=lambda: None, content=_csv(450))
    with patch.object(sc.requests, "get", return_value=resp):
        rows = sc.fetch_universe()
    assert len(rows) == 450 and rows[0] == {
        "symbol": "SYM0",
        "company": "Company 0 Ltd.",
        "industry": "Industry 0",
        "isin": "INE000000000",
    }
    short = SimpleNamespace(raise_for_status=lambda: None, content=_csv(50))
    with patch.object(sc.requests, "get", return_value=short), pytest.raises(ValueError):
        sc.fetch_universe()


# ── Database ──


@pytest.mark.postgres
def test_refresh_universe_stores_and_falls_back(db_session):
    db_session.execute(text("DELETE FROM scanner_universe"))
    fresh = [{"symbol": f"ZZ{i}", "company": "C", "industry": "I", "isin": "X"} for i in range(3)]
    with patch.object(sc, "fetch_universe", return_value=fresh) as fetch:
        assert len(sc.refresh_universe()) == 3
        assert len(sc.refresh_universe()) == 3  # fresh: no second fetch
    assert fetch.call_count == 1
    db_session.execute(text("UPDATE scanner_universe SET fetched_at = NOW() - INTERVAL '30 days'"))
    with patch.object(sc, "fetch_universe", side_effect=OSError("NSE down")):
        assert {r["symbol"] for r in sc.refresh_universe()} == {"ZZ0", "ZZ1", "ZZ2"}


@pytest.mark.postgres
def test_load_prices_adjusts_and_drops_short_histories(db_session):
    start = date.today() - timedelta(days=500)
    days = pd.bdate_range(start, periods=300)
    rows = []
    for i, d in enumerate(days):
        # ZZLONG: traded price halves on a 2:1 split halfway; adj_close is split-adjusted
        traded = 200.0 if i < 150 else 100.0
        rows.append({"s": "ZZLONG", "d": d.date(), "c": traded, "a": 100.0})
        if i >= 200:
            rows.append({"s": "ZZSHORT", "d": d.date(), "c": 50.0, "a": 50.0})
    db_session.execute(
        text(
            """INSERT INTO historical_daily_bars
               (tradingsymbol, bar_date, open, high, low, close, adj_close)
               VALUES (:s, :d, :c, :c, :c, :c, :a)"""
        ),
        rows,
    )
    prices = sc.load_prices(["ZZLONG", "ZZSHORT"])
    assert list(prices.columns) == ["ZZLONG"]  # ZZSHORT has only 100 days
    assert prices["ZZLONG"].dropna().nunique() == 1  # the split no longer looks like a crash


@pytest.mark.postgres
def test_only_one_scan_runs_and_stale_runs_are_closed(db_session):
    db_session.execute(text("DELETE FROM scanner_runs WHERE status = 'running'"))
    first = sc.start_scan("test")
    assert first is not None and sc.start_scan("test") is None
    db_session.execute(
        text("UPDATE scanner_runs SET started_at = NOW() - INTERVAL '4 hours' WHERE id = :i"),
        {"i": first},
    )
    assert sc.start_scan("test") is not None
    status = db_session.execute(
        text("SELECT status FROM scanner_runs WHERE id = :i"), {"i": first}
    ).scalar_one()
    assert status == "failed"


@pytest.mark.postgres
def test_run_scan_stores_an_enriched_result(db_session):
    db_session.execute(text("DELETE FROM scanner_runs WHERE status = 'running'"))
    prices = _market(n_stocks=60)
    universe = [
        {"symbol": s, "company": f"{s} Ltd", "industry": "Tech", "isin": ""} for s in prices
    ]
    db_session.execute(
        text(
            """INSERT INTO fundamentals_snapshots (tradingsymbol, fields, source)
               VALUES ('S001', CAST(:f AS JSONB), 'test')"""
        ),
        {"f": '{"pe_ttm": {"value": 18.5, "unit": "x"}, "roe": {"value": 0.2, "unit": "ratio"}}'},
    )
    run_id = sc.start_scan("test")
    with (
        patch.object(sc, "refresh_universe", return_value=universe),
        patch.object(sc, "deep_sync_prices", return_value=[]),
        patch.object(sc, "sync_prices", return_value=["S999"]),
        patch.object(sc, "load_prices", return_value=prices),
        patch(
            "src.analytics.valuator.compute_portfolio_valuation",
            return_value=SimpleNamespace(holdings=[SimpleNamespace(tradingsymbol="S002")]),
        ),
    ):
        sc.run_scan(run_id)
    latest = sc.latest_scan()
    assert latest["running"] is None and latest["latest"]["id"] == run_id
    result = latest["latest"]["result"]
    assert result["sync_failed"] == ["S999"] and result["stocks_ranked"] == 60
    rows = {r["symbol"]: r for r in result["ranking"]}
    assert rows["S001"]["fundamentals"]["pe_ttm"] == 18.5
    assert rows["S001"]["fundamentals"]["roe_pct"] == 20.0
    assert rows["S002"]["held"] and rows["S001"]["company"] == "S001 Ltd"
    assert len(sc.top_symbols(5)) == (5 if result["status"] != "insufficient_data" else 0)


@pytest.mark.postgres
def test_run_scan_records_a_failure(db_session):
    db_session.execute(text("DELETE FROM scanner_runs WHERE status = 'running'"))
    run_id = sc.start_scan("test")
    with patch.object(sc, "refresh_universe", side_effect=OSError("NSE down, nothing stored")):
        sc.run_scan(run_id)
    fail = sc.latest_scan()["last_failure"]
    assert fail["id"] == run_id and "NSE down" in fail["error"]


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


def test_api_starts_one_scan_at_a_time(client, headers):
    with patch.object(sc, "start_in_background", side_effect=[7, None]):
        assert client.post("/api/v1/scanner/run", headers=headers).status_code == 202
        busy = client.post("/api/v1/scanner/run", headers=headers)
    assert busy.status_code == 409 and busy.get_json()["error"]["code"] == "SCAN_RUNNING"
    with patch.object(
        sc, "latest_scan", return_value={"latest": None, "running": None, "last_failure": None}
    ):
        assert client.get("/api/v1/scanner", headers=headers).get_json()["data"]["latest"] is None
    assert client.get("/api/v1/scanner").status_code == 401


def test_scheduler_has_scanner_jobs():
    from src.scheduler.jobs import create_scheduler

    ids = {j.id for j in create_scheduler().get_jobs()}
    assert {"market_scanner", "scanner_fundamentals", "news_refresh"} <= ids


# ── Ten years of history ──


def test_chart_api_accepts_a_ten_year_range():
    from src.analytics import historical_cache as hc

    body = {"chart": {"result": [{"timestamp": [], "indicators": {"quote": [{}]}}]}}
    with patch(
        "requests.get", return_value=SimpleNamespace(status_code=200, json=lambda: body)
    ) as get:
        hc._fetch_from_yahoo_chart_api("INFY.NS", period="10y")
    assert "range=10y" in get.call_args.args[0]


@pytest.mark.postgres
def test_deep_sync_fetches_each_symbol_once_and_retries_failures(db_session):
    db_session.execute(text("DELETE FROM scanner_deep_sync WHERE tradingsymbol LIKE 'ZZDEEP%'"))
    calls = []

    def fake_sync(symbol, lookback_days=750, force_sync=False):
        calls.append((symbol, force_sync))
        if symbol == "ZZDEEP2":
            raise OSError("feed down")
        return pd.DataFrame({"Close": [1.0]})

    with patch("src.analytics.historical_cache.get_or_sync_historical_bars", side_effect=fake_sync):
        assert sc.deep_sync_prices(["ZZDEEP1", "ZZDEEP2"], pause=0) == ["ZZDEEP2"]
        assert sc.deep_sync_prices(["ZZDEEP1", "ZZDEEP2"], pause=0) == ["ZZDEEP2"]
    # ZZDEEP1 was refetched in full once; ZZDEEP2 is retried each time
    assert calls == [("ZZDEEP1", True), ("ZZDEEP2", True), ("ZZDEEP2", True)]


def test_scanner_loads_ten_years_by_default():
    import inspect

    assert inspect.signature(sc.load_prices).parameters["years"].default == 10
