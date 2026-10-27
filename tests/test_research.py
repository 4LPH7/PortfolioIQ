"""Research committee: snapshot and grounding, the pipeline against a fake Claude client, the
fundamentals child process, run quota/idempotency/sweep/settlement in Postgres, and the API."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import anthropic
import httpx
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text

from src.research import committee, fundamentals, runs
from src.research.committee import DEBATE, PM, ROLES, CommitteeConfig, call_cost, run_committee
from src.research.snapshot import allowlist, build_snapshot, check_citation

NOW = datetime(2026, 10, 26, 18, 0, tzinfo=UTC)
MODEL = "claude-opus-5-5"


def _bars(n=300, drift=0.0005, seed=1, start="2025-08-01"):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.01, n)))
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame({"Close": close, "Raw Close": close * 1.02}, index=idx)


def _snapshot():
    return build_snapshot(
        "TESTCO",
        _bars(),
        {"weight_pct": 8.0, "unrealised_pnl_pct": 12.5},
        {
            "fields": {
                "pe_ttm": {"value": 22.5, "unit": "x"},
                "roe": {"value": 0.18, "unit": "ratio"},
                "market_cap": {"value": 5e11, "unit": "INR"},
            },
            "source": "yfinance",
            "fetched_at": NOW,
        },
        NOW,
    )


# ── Snapshot and grounding ──


def test_snapshot_values_units_and_allowlist():
    snap = _snapshot()
    allowed = allowlist(snap)
    assert snap["schema_version"] == 1
    assert {"price.last_close", "price.return_12m", "price.rsi_14", "position.weight_pct"} <= set(
        allowed
    )
    assert allowed["fundamentals.roe"] == {
        **allowed["fundamentals.roe"],
        "value": 18.0,
        "unit": "pct",
    }
    assert allowed["fundamentals.market_cap"]["value"] == 50000.0  # INR crore
    assert allowed["fundamentals.market_cap"]["unit"] == "INR_crore"
    # last_close is the traded price, returns come from the adjusted series
    assert allowed["price.last_close"]["value"] == round(_bars()["Raw Close"].iloc[-1], 2)
    assert snap["unavailable"] == []


def test_snapshot_marks_missing_inputs_and_needs_history():
    snap = build_snapshot("TESTCO", _bars(), None, None, NOW)
    assert "position" not in snap["data"] and "fundamentals" not in snap["data"]
    assert len(snap["unavailable"]) == 2
    with pytest.raises(ValueError, match="needs 60"):
        build_snapshot("TESTCO", _bars(n=30), None, None, NOW)


def test_check_citation_tolerances_and_units():
    allowed = {"price.return_1m": {"value": 10.0, "unit": "pct"}}
    ok = check_citation({"snapshot_key": "price.return_1m", "value": 10.04, "unit": "pct"}, allowed)
    assert ok["ok"]  # within 0.5% relative
    assert not check_citation(
        {"snapshot_key": "price.return_1m", "value": 10.2, "unit": "pct"}, allowed
    )["ok"]
    assert (
        "unit"
        in check_citation({"snapshot_key": "price.return_1m", "value": 10.0, "unit": "x"}, allowed)[
            "reason"
        ]
    )
    assert (
        "not in the snapshot"
        in check_citation({"snapshot_key": "price.made_up", "value": 1, "unit": "pct"}, allowed)[
            "reason"
        ]
    )


# ── Fake Claude client ──

GOOD_CITE = {"snapshot_key": "position.weight_pct", "value": 8.0, "unit": "pct"}
VIEW = {
    "stance": "bullish",
    "confidence": 60,
    "summary": "Fine",
    "key_points": ["Trend up"],
    "citations": [GOOD_CITE],
}
TURN = {"argument": "Because", "citations": [GOOD_CITE]}
DECISION = {
    "rating": "Overweight",
    "conviction": 65,
    "thesis": "Good business",
    "key_risks": ["Valuation"],
    "what_would_change_my_mind": ["Margins fall"],
    "citations": [GOOD_CITE, {"snapshot_key": "price.rsi_14", "value": -5, "unit": "index"}],
}


def _usage(**kw):
    base = {
        "input_tokens": 100,
        "output_tokens": 200,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 0,
        "iterations": None,
    }
    return SimpleNamespace(**{**base, **kw})


def _resp(model, body=None, stop="end_turn", raw=None):
    text_ = raw if raw is not None else (json.dumps(body) if body is not None else "")
    return SimpleNamespace(
        model=model,
        stop_reason=stop,
        stop_details=SimpleNamespace(category="other") if stop == "refusal" else None,
        content=[SimpleNamespace(type="text", text=text_)],
        usage=_usage(),
        _request_id="req_test",
    )


def _stage_of(params) -> str:
    if params["max_tokens"] == 0:
        return "warmup"
    user = params["messages"][0]["content"]
    for name, role in ROLES.items():
        if user == role:
            return name
    for side, prompt in DEBATE.items():
        if user.startswith(prompt):
            return side
    assert user.startswith(PM)
    return "pm"


def _default(stage, params):
    body = {"warmup": None, "bull": TURN, "bear": TURN, "pm": DECISION}.get(stage, VIEW)
    return _resp(params["model"], body)


class FakeClient:
    def __init__(self, handler=_default):
        self.handler, self.stages, self.params = handler, [], []
        self._lock = threading.Lock()
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **params):
        stage = _stage_of(params)
        with self._lock:
            self.stages.append(stage)
            self.params.append(params)
        return self.handler(stage, params)


def _cfg(budget=1.0):
    return CommitteeConfig(MODEL, MODEL, budget, time.monotonic() + 600)


def _request():
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


# ── Committee pipeline ──


def test_stage_order_warmup_first_and_audit_fields():
    client, progress = FakeClient(), []
    out = run_committee(_snapshot(), client, _cfg(), lambda o: progress.append(o["cost_usd"]))

    assert out["status"] == "completed"
    assert client.stages[0] == "warmup"
    assert set(client.stages[1:7]) == set(ROLES)
    assert client.stages[7:] == ["bull", "bear", "bull", "bear", "pm"]
    assert out["decision"]["rating"] == "Overweight"
    assert set(out["prompt_hashes"]) >= set(ROLES) | {"portfolio_manager"}
    assert len(out["calls"]) == 12 and all(c["request_id"] == "req_test" for c in out["calls"])
    assert out["cost_usd"] == pytest.approx(12 * (100 * 4 + 200 * 20 + 900 * 0.2) / 1e6, abs=1e-4)
    assert progress == sorted(progress) and len(progress) == 10  # 6 analysts + 4 debate turns

    warm, *structured = client.params
    assert "betas" not in warm and "format" not in warm["output_config"]
    for p in structured:
        assert p["fallbacks"] == "default" and p["betas"] == [committee.FALLBACK_BETA]
        assert p["output_config"]["format"]["type"] == "json_schema"
        assert p["thinking"] == {"type": "adaptive"}
        assert "tools" not in p  # advisory only: the committee can call nothing
    efforts = {_stage_of(p): p["output_config"]["effort"] for p in structured}
    assert efforts["graham"] == "low" and efforts["bull"] == "medium" and efforts["pm"] == "high"


def test_grounding_flags_a_citation_that_does_not_match():
    out = run_committee(_snapshot(), FakeClient(), _cfg())
    g = out["grounding"]
    assert g["checked"] == 6 + 4 + 2 and g["passed"] == 11
    assert g["failures"][0]["snapshot_key"] == "price.rsi_14"


def test_invalid_output_from_one_analyst_does_not_stop_the_run():
    def handler(stage, params):
        if stage == "risk":
            return _resp(params["model"], raw='{"stance": "sideways"}')
        return _default(stage, params)

    out = run_committee(_snapshot(), FakeClient(handler), _cfg())
    assert out["status"] == "completed"
    assert out["stages"]["risk"]["status"] == "invalid_output"


def test_refusals_end_the_run_as_declined():
    def handler(stage, params):
        if stage in ROLES:
            return _resp(params["model"], stop="refusal")
        return _default(stage, params)

    out = run_committee(_snapshot(), FakeClient(handler), _cfg())
    assert out["status"] == "declined" and out["decision"] is None
    assert out["stages"]["graham"] == {"status": "declined", "category": "other"}


def test_budget_too_small_for_any_call_makes_no_request():
    client = FakeClient()
    out = run_committee(_snapshot(), client, _cfg(budget=0.001))
    assert out["status"] == "budget_exceeded" and client.stages == []


def test_budget_stop_keeps_partial_results_and_never_overspends():
    client = FakeClient()
    out = run_committee(_snapshot(), client, _cfg(budget=0.30))
    assert out["status"] == "budget_exceeded" and out["decision"] is None
    assert "pm" not in client.stages
    assert out["cost_usd"] <= 0.30
    assert any(s["status"] == "ok" for s in out["stages"].values())


def test_rejected_key_fails_the_run_at_warmup():
    def handler(stage, params):
        raise anthropic.AuthenticationError(
            "bad key", response=httpx.Response(401, request=_request()), body=None
        )

    out = run_committee(_snapshot(), FakeClient(handler), _cfg())
    assert out["status"] == "failed" and "API key" in out["error"]


def test_warmup_network_error_is_not_fatal():
    def handler(stage, params):
        if stage == "warmup":
            raise anthropic.APIConnectionError(request=_request())
        return _default(stage, params)

    out = run_committee(_snapshot(), FakeClient(handler), _cfg())
    assert out["status"] == "completed"
    assert out["calls"][0]["stage"] == "warmup" and "error" in out["calls"][0]


def test_call_cost_sums_every_attempt():
    usage = _usage(
        iterations=[
            SimpleNamespace(model="claude-opus-5-5", input_tokens=1_000_000, output_tokens=0),
            SimpleNamespace(model="claude-sonnet-5-5", input_tokens=0, output_tokens=1_000_000),
        ]
    )
    assert call_cost(usage, MODEL) == pytest.approx(4.0 + 10.0)


# ── Fundamentals child process ──


def _hang_after_first(yf_tickers, out):
    out.put((yf_tickers[0], {"pe_ttm": {"value": 20.0, "unit": "x"}}))
    time.sleep(60)


def _quick(yf_tickers, out):
    for t in yf_tickers:
        out.put((t, {"pe_ttm": {"value": 20.0, "unit": "x"}, "ticker": t}))


def test_fundamentals_batch_keeps_results_before_a_hang(monkeypatch):
    monkeypatch.setattr(fundamentals, "_fetch_many", _hang_after_first)
    monkeypatch.setattr(fundamentals, "STARTUP_GRACE_SEC", 15)  # Windows spawn is slow to start
    started = time.monotonic()
    got = fundamentals.fetch_fundamentals_batch(["TESTCO", "OTHERCO"], per_symbol_timeout=1)
    assert got == {"TESTCO": {"pe_ttm": {"value": 20.0, "unit": "x"}}}  # OTHERCO hung
    assert 15 <= time.monotonic() - started < 40  # waited the deadline, then terminated


def test_fundamentals_fetch_returns_child_result(monkeypatch):
    monkeypatch.setattr(fundamentals, "_fetch_many", _quick)
    got = fundamentals.fetch_fundamentals("TESTCO", timeout=60)
    assert got["ticker"] == "TESTCO.NS" and got["pe_ttm"]["value"] == 20.0


def test_fundamentals_fields_skip_missing_and_non_finite():
    info = {"trailingPE": 22.5, "priceToBook": float("nan"), "marketCap": 5e11, "debtToEquity": "x"}
    assert fundamentals._fields(info) == {
        "pe_ttm": {"value": 22.5, "unit": "x"},
        "market_cap": {"value": 5e11, "unit": "INR"},
    }
    assert fundamentals._fields({}) is None


# ── Runs in Postgres (savepoint, rolled back) ──

USER = "t-research"


def _insert(session, **cols):
    base = {
        "user_id": USER,
        "idempotency_key": uuid.uuid4().hex,
        "tradingsymbol": "TESTCO",
        "run_date": runs._today(),
        "status": "completed",
    }
    row = {**base, **cols}
    names = ", ".join(row)
    values = ", ".join(
        f"CAST(:{k} AS JSONB)" if k in ("decision", "stages") else f":{k}" for k in row
    )
    return session.execute(
        text(f"INSERT INTO research_runs ({names}) VALUES ({values}) RETURNING id"), row
    ).scalar_one()


@pytest.mark.postgres
def test_enqueue_is_idempotent_and_allows_one_active_run(db_session):
    first, created = runs.enqueue_run(USER, "TESTCO", "key-aaaaaaaa")
    again, created_again = runs.enqueue_run(USER, "TESTCO", "key-aaaaaaaa")
    assert created and not created_again and again["id"] == first["id"]
    assert first["status"] == "queued" and first["budget_usd"] == 1.0
    with pytest.raises(runs.QuotaError, match="already in progress"):
        runs.enqueue_run(USER, "OTHER", "key-bbbbbbbb")


@pytest.mark.postgres
def test_daily_limit_counts_spending_runs_only(db_session):
    for _ in range(4):
        _insert(db_session)
    _insert(db_session, status="failed", cost_usd=0)  # failed before spending: free
    run, created = runs.enqueue_run(USER, "TESTCO", "key-fifth-run")
    assert created
    db_session.execute(
        text("UPDATE research_runs SET status='completed' WHERE id=:i"), {"i": run["id"]}
    )
    with pytest.raises(runs.QuotaError, match="daily limit of 5"):
        runs.enqueue_run(USER, "TESTCO", "key-sixth-run")
    assert runs.usage_today(USER)["runs_today"] == 5


@pytest.mark.postgres
def test_sweep_marks_stuck_runs_abandoned(db_session):
    stuck = _insert(db_session, status="running")
    db_session.execute(
        text("UPDATE research_runs SET created_at = NOW() - INTERVAL '1 hour' WHERE id = :i"),
        {"i": stuck},
    )
    fresh = _insert(db_session, status="queued")
    assert runs.sweep_abandoned(USER) == 1
    status = dict(
        db_session.execute(
            text("SELECT id, status FROM research_runs WHERE id IN (:a, :b)"),
            {"a": stuck, "b": fresh},
        ).all()
    )
    assert status == {stuck: "abandoned", fresh: "queued"}


@pytest.mark.postgres
def test_execute_run_persists_decision_and_audit_fields(db_session):
    run, _ = runs.enqueue_run(USER, "TESTCO", "key-execute-1")
    with patch.object(runs, "_build_snapshot", return_value=_snapshot()):
        runs.execute_run(run["id"], client=FakeClient())
    row = runs.get_run(USER, run["id"])
    assert row["status"] == "completed" and row["rating"] == "Overweight"
    assert row["snapshot_schema_version"] == 1 and row["data_as_of"] is not None
    assert row["models"]["portfolio_manager"] == MODEL
    assert len(row["prompt_hashes"]["graham"]) == 64
    assert len(row["calls"]) == 12 and row["cost_usd"] > 0
    assert row["price_at_run"] == _snapshot()["data"]["price"]["last_close"]["value"]
    assert row["finished_at"] is not None
    runs.execute_run(run["id"], client=FakeClient())  # already claimed: a no-op


@pytest.mark.postgres
def test_execute_run_without_enough_data_fails_free(db_session):
    run, _ = runs.enqueue_run(USER, "TESTCO", "key-execute-2")
    with patch.object(runs, "_build_snapshot", side_effect=ValueError("needs 60")):
        runs.execute_run(run["id"], client=FakeClient())
    row = runs.get_run(USER, run["id"])
    assert row["status"] == "failed" and "snapshot" in row["error"] and row["cost_usd"] == 0


@pytest.mark.postgres
def test_settlement_computes_forward_return_and_alpha(db_session):
    rid = _insert(db_session, data_as_of="2026-01-01")
    idx = pd.bdate_range("2026-01-01", periods=70)
    stock = pd.DataFrame({"Close": 100 * 1.01 ** np.arange(70)}, index=idx)
    bench = pd.DataFrame({"Close": 100 * 1.005 ** np.arange(70)}, index=idx)
    with patch(
        "src.analytics.historical_cache.get_or_sync_historical_bars",
        side_effect=lambda sym, **_: bench if sym == "NIFTY 50 TRI" else stock,
    ):
        assert runs.settle_research_runs() == {"5d": 1, "20d": 1, "60d": 1}
    row = runs.get_run(USER, rid)
    assert row["fwd_return_5d"] == pytest.approx(1.01**5 - 1, abs=1e-6)
    assert row["alpha_60d"] == pytest.approx((1.01**60 - 1) - (1.005**60 - 1), abs=1e-6)


@pytest.mark.postgres
def test_scorecard_needs_fifteen_settled_runs(db_session):
    def add(rating, conviction, alpha):
        _insert(
            db_session,
            decision=json.dumps({"rating": rating, "conviction": conviction}),
            alpha_20d=alpha,
        )

    for i in range(3):
        add("Buy", 80, 0.02 + i / 100)
    card = runs.scorecard(USER)
    assert card["conviction_ic_20d"]["status"] == "insufficient_data"
    for i in range(6):
        add("Sell", 70, -0.03 - i / 100)
        add("Hold", 50, 0.0 + i / 1000)
    card = runs.scorecard(USER)
    assert card["settled_runs"] == 15
    assert card["by_rating"]["Buy"] == {"n": 3, "avg_alpha_20d_pct": 3.0}
    assert card["conviction_ic_20d"]["status"] == "ok" and card["conviction_ic_20d"]["value"] > 0.8


@pytest.mark.postgres
def test_quota_holds_when_two_requests_race():
    """Two real connections, committed: without the advisory lock both would pass the check."""
    from src.db.connection import execute_sql

    user = f"race-{uuid.uuid4().hex[:8]}"
    barrier, results = threading.Barrier(2), []

    def attempt(key):
        barrier.wait()
        try:
            results.append(runs.enqueue_run(user, "TESTCO", key)[1])
        except runs.QuotaError:
            results.append("quota")

    try:
        threads = [threading.Thread(target=attempt, args=(f"race-key-{i}",)) for i in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert sorted(results, key=str) == [True, "quota"]
    finally:
        execute_sql("DELETE FROM research_runs WHERE user_id = :u", {"u": user})


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


BODY = {"symbol": "TESTCO", "idempotency_key": "ui-1234567890"}


def test_post_run_without_a_key_is_off(client, headers, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    res = client.post("/api/v1/research/runs", headers=headers, json=BODY)
    assert res.status_code == 503 and res.get_json()["error"]["code"] == "RESEARCH_OFF"


def test_post_run_starts_once_and_replays(client, headers, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    with (
        patch.object(runs, "enqueue_run", side_effect=[({"id": 7}, True), ({"id": 7}, False)]),
        patch.object(runs, "start_run") as start,
    ):
        assert client.post("/api/v1/research/runs", headers=headers, json=BODY).status_code == 202
        assert client.post("/api/v1/research/runs", headers=headers, json=BODY).status_code == 200
    start.assert_called_once_with(7)


def test_post_run_over_quota_is_429(client, headers, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    with patch.object(runs, "enqueue_run", side_effect=runs.QuotaError("limit")):
        res = client.post("/api/v1/research/runs", headers=headers, json=BODY)
    assert res.status_code == 429 and res.get_json()["error"]["code"] == "RESEARCH_QUOTA"


def test_post_run_validates_symbol_and_auth(client, headers):
    bad = client.post("/api/v1/research/runs", headers=headers, json={**BODY, "symbol": "x;drop"})
    assert bad.status_code == 422
    assert client.post("/api/v1/research/runs", json=BODY).status_code == 401


def test_scheduler_has_research_jobs():
    from src.scheduler.jobs import create_scheduler

    ids = {j.id for j in create_scheduler().get_jobs()}
    assert {"research_settlement", "research_fundamentals"} <= ids


def test_run_date_is_an_ist_day():
    assert date.fromisoformat(runs._today())


@pytest.mark.skipif(os.environ.get("RUN_LIVE_LLM") != "1", reason="costs money: set RUN_LIVE_LLM=1")
def test_live_committee_run():
    """One real run on a synthetic snapshot: caching, grounding and the cost cap, measured."""
    client = anthropic.Anthropic(timeout=anthropic.Timeout(180.0, connect=10.0), max_retries=2)
    out = run_committee(_snapshot(), client, _cfg(budget=1.0))
    print(json.dumps({k: out[k] for k in ("status", "cost_usd", "grounding")}, indent=2))
    assert out["status"] == "completed", out["error"]
    assert out["cost_usd"] <= 1.0
    assert sum(c.get("cache_read_input_tokens", 0) for c in out["calls"][1:]) > 0


@pytest.mark.postgres
def test_fundamentals_saved_and_expire_after_seven_days(db_session, monkeypatch):
    sym = "ZZFUNDTEST"
    fields = {"pe_ttm": {"value": 20.0, "unit": "x"}}
    monkeypatch.setattr(
        fundamentals, "fetch_fundamentals_batch", lambda syms: {s: fields for s in syms if s == sym}
    )
    assert fundamentals.refresh_fundamentals([sym, "ZZMISSING"]) == {"saved": 1, "failed": 1}
    row = fundamentals.latest_fundamentals(sym)
    assert row["fields"] == fields and row["source"] == "yfinance"
    later = datetime.now(UTC) + pd.Timedelta(days=8)
    assert fundamentals.latest_fundamentals(sym, now=later) is None
