"""Decision journal: context captured server-side, settlement against NIFTY 50 TRI, and a summary
that refuses to conclude below 15 settled entries."""

from __future__ import annotations

import os
from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text

from src.analytics import journal as jr

USER = "t-journal"


def _bars(closes, start=date(2026, 1, 1)):
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"Close": closes, "Raw Close": closes}, index=idx)


# ── Validation ──


def test_action_and_reason_are_checked():
    with pytest.raises(ValueError, match="action"):
        jr.check_entry("buy-maybe", "")
    with pytest.raises(ValueError, match="reason"):
        jr.check_entry("took", "x" * 501)
    assert jr.check_entry("skipped", "  too expensive  ") == ("skipped", "too expensive")


# ── Context is taken on the server ──


def test_context_is_built_from_the_latest_scan_and_odds_not_the_client():
    scan = {"rank": 12, "of": 476, "status": "edge"}
    with (
        patch.object(jr, "_scanner_context", return_value=scan) as sc,
        patch.object(jr, "_committee_context", return_value={"rating": "Hold", "run_id": 4}),
        patch.object(jr, "_price_now", return_value=1500.0),
    ):
        ctx = jr.build_context("INFY", user_id=USER)
    sc.assert_called_once_with("INFY")
    assert ctx == {
        "scanner": scan,
        "committee": {"rating": "Hold", "run_id": 4},
        "price": 1500.0,
    }


def test_context_tolerates_missing_scan_and_committee():
    with (
        patch.object(jr, "_scanner_context", return_value=None),
        patch.object(jr, "_committee_context", return_value=None),
        patch.object(jr, "_price_now", return_value=None),
    ):
        assert jr.build_context("NOPE", user_id=USER) == {
            "scanner": None,
            "committee": None,
            "price": None,
        }


# ── Settlement maths ──


def test_settlement_uses_the_next_twenty_trading_days_against_the_index():
    closes = [100.0] * 5 + [110.0] * 25  # the stock is +10% from day 5 on
    stock = _bars(closes)
    index = _bars([100.0] * 30)
    start = stock.index[0].date()
    got = jr.settled_returns(stock, index, start)
    assert got["fwd_return_20d"] == pytest.approx(0.10)
    assert got["alpha_20d"] == pytest.approx(0.10)


def test_settlement_waits_until_the_horizon_has_passed():
    stock = _bars([100.0] * 10)
    got = jr.settled_returns(stock, _bars([100.0] * 10), stock.index[0].date())
    assert got == {"fwd_return_20d": None, "alpha_20d": None}


# ── Summary ──


def test_summary_says_insufficient_below_fifteen_settled_entries():
    rows = [{"action": "took", "alpha_20d": 0.02, "scanner_rank": 5}] * 14
    out = jr.summarise(rows)
    assert out["status"] == "insufficient_data"
    assert out["settled"] == 14 and out["took_avg_alpha_20d_pct"] is None


def test_summary_compares_took_with_skipped_and_by_rank_bucket():
    rows = [{"action": "took", "alpha_20d": 0.03, "scanner_rank": 5}] * 8
    rows += [{"action": "skipped", "alpha_20d": -0.01, "scanner_rank": 250}] * 8
    out = jr.summarise(rows)
    assert out["status"] == "ok"
    assert out["took_avg_alpha_20d_pct"] == pytest.approx(3.0)
    assert out["skipped_avg_alpha_20d_pct"] == pytest.approx(-1.0)
    assert out["by_rank"]["top_100"]["avg_alpha_20d_pct"] == pytest.approx(3.0)
    assert out["by_rank"]["outside_top_100"]["n"] == 8


def test_watching_entries_are_not_counted_as_decisions():
    rows = [{"action": "watching", "alpha_20d": 0.5, "scanner_rank": 1}] * 20
    assert jr.summarise(rows)["settled"] == 0


# ── Database ──


@pytest.mark.postgres
def test_record_and_list_round_trip(db_session):
    entry = jr.record(
        user_id=USER,
        symbol="INFY",
        action="took",
        side="BUY",
        reason="  fits the plan ",
        context={"scanner": {"rank": 3}},
        price=1500.0,
    )
    assert entry["reason"] == "fits the plan" and entry["context"]["scanner"]["rank"] == 3
    rows = jr.list_entries(USER)
    assert [r["id"] for r in rows] == [entry["id"]]


@pytest.mark.postgres
def test_settle_fills_matured_entries_and_leaves_the_rest(db_session):
    old = date.today() - timedelta(days=60)
    entry = jr.record(
        user_id=USER,
        symbol="ZZJOURNAL",
        action="skipped",
        side="BUY",
        reason="",
        context={},
        price=100.0,
    )
    db_session.execute(
        text("UPDATE decision_journal SET decided_on = :d WHERE id = :i"),
        {"d": old, "i": entry["id"]},
    )
    stock = _bars(np.linspace(100, 120, 90), start=old)
    index = _bars(np.full(90, 100.0), start=old)
    with patch.object(
        jr, "_bars_for", side_effect=lambda s, **_: stock if s == "ZZJOURNAL" else index
    ):
        assert jr.settle(USER) == 1
    row = jr.list_entries(USER)[0]
    assert row["alpha_20d"] is not None and row["settled_at"] is not None
    assert jr.settle(USER) == 0  # already settled, nothing to do


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


def test_post_rejects_bad_input_and_needs_the_key(client, headers):
    bad = client.post(
        "/api/v1/journal", headers=headers, json={"symbol": "INFY", "action": "maybe"}
    )
    assert bad.status_code == 422
    assert (
        client.post("/api/v1/journal", json={"symbol": "INFY", "action": "took"}).status_code == 401
    )


def test_post_stores_server_side_context(client, headers):
    with (
        patch.object(
            jr,
            "build_context",
            return_value={"scanner": {"rank": 7}, "committee": None, "price": 10.0},
        ),
        patch.object(
            jr, "record", return_value={"id": 9, "context": {"scanner": {"rank": 7}}}
        ) as rec,
    ):
        res = client.post(
            "/api/v1/journal",
            headers=headers,
            json={
                "symbol": "INFY",
                "action": "took",
                "reason": "ok",
                "context": {"scanner": {"rank": 1}},
            },
        )
    assert res.status_code == 201
    assert rec.call_args.args[5] == {
        "scanner": {"rank": 7},
        "committee": None,
        "price": 10.0,
    }


def test_scheduler_has_the_journal_settlement_job():
    from src.scheduler.jobs import create_scheduler

    assert "journal_settlement" in {j.id for j in create_scheduler().get_jobs()}
