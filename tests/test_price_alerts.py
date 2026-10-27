"""Price alerts: each rule, ring-once and re-arm, quotes, the bell's escaping, and the API."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import text

from src.analytics import price_alerts as pa

TODAY = date(2026, 10, 27)
NOW = datetime(2026, 10, 27, 5, 0, tzinfo=UTC)


def _alert(**kw):
    base = {
        "kind": "above",
        "price": 100.0,
        "pct": None,
        "direction": "either",
        "armed": True,
        "rearm": False,
        "triggered_on": None,
    }
    return {**base, **kw}


# ── Rules (pure) ──


def test_above_and_below_ring_at_or_beyond_the_level():
    assert pa.evaluate(_alert(), 100.0, None, TODAY) == "ring"
    assert pa.evaluate(_alert(), 99.99, None, TODAY) is None
    assert pa.evaluate(_alert(kind="below"), 100.0, None, TODAY) == "ring"
    assert pa.evaluate(_alert(kind="below"), 100.5, None, TODAY) is None


def test_a_rung_alert_waits_for_the_cross_back_before_rearming():
    rung = _alert(armed=False, rearm=True)
    assert pa.evaluate(rung, 105, None, TODAY) is None  # still above: no repeat ring
    assert pa.evaluate(rung, 99, None, TODAY) == "rearm"
    assert pa.evaluate(_alert(armed=False), 99, None, TODAY) is None  # no rearm: stays quiet


def test_percent_move_by_direction_and_once_a_day():
    up = _alert(kind="pct_move", price=None, pct=3, direction="up")
    assert pa.evaluate(up, 103, 100, TODAY) == "ring"
    assert pa.evaluate(up, 97, 100, TODAY) is None
    down = {**up, "direction": "down"}
    assert pa.evaluate(down, 96.9, 100, TODAY) == "ring"
    either = {**up, "direction": "either"}
    assert pa.evaluate(either, 96.9, 100, TODAY) == "ring"
    assert pa.evaluate({**either, "triggered_on": TODAY}, 90, 100, TODAY) is None
    assert pa.evaluate(either, 90, None, TODAY) is None  # no previous close, no verdict


def test_describe_reads_plainly():
    assert pa.describe({**_alert(), "tradingsymbol": "INFY"}) == "INFY rises to ₹100.00"
    pct = {**_alert(kind="pct_move", pct=2.5, direction="down"), "tradingsymbol": "INFY"}
    assert pa.describe(pct) == "INFY falls 2.5% in a day"


def test_yahoo_quote_reads_price_and_previous_close():
    body = {
        "chart": {"result": [{"meta": {"regularMarketPrice": 1520.5, "chartPreviousClose": 1500}}]}
    }
    with patch.object(pa.requests, "get", return_value=SimpleNamespace(json=lambda: body)) as get:
        q = pa._yahoo_quote("INFY")
    assert q == {"price": 1520.5, "prev_close": 1500, "source": "yahoo (delayed)"}
    assert "INFY.NS" in get.call_args.args[0]
    with patch.object(pa.requests, "get", side_effect=OSError("down")):
        assert pa._yahoo_quote("INFY") is None


# ── Database ──

USER = "t-alerts"


@pytest.mark.postgres
def test_create_toggle_delete_and_limit(db_session):
    a = pa.create_alert(USER, "INFY", "above", 1600, None, "either", "breakout", False)
    assert a["active"] and a["armed"] and a["price"] == 1600 and a["pct"] is None
    assert [r["id"] for r in pa.list_alerts(USER)] == [a["id"]]
    assert pa.set_active(USER, a["id"], False)["active"] is False
    assert pa.set_active("someone-else", a["id"], True) is None
    assert pa.delete_alert(USER, a["id"]) and not pa.delete_alert(USER, a["id"])
    db_session.execute(
        text(
            "INSERT INTO price_alerts (user_id, tradingsymbol, kind, price) "
            "SELECT :u, 'INFY', 'above', 1 FROM generate_series(1, :n)"
        ),
        {"u": USER, "n": pa.MAX_ALERTS},
    )
    with pytest.raises(ValueError, match="already have 100"):
        pa.create_alert(USER, "INFY", "above", 1600, None, "either", None, False)


@pytest.mark.postgres
def test_check_rings_once_rearms_and_respects_the_day(db_session):
    once = pa.create_alert(USER, "ZZALERT", "above", 100, None, "either", None, False)
    again = pa.create_alert(USER, "ZZALERT", "below", 95, None, "either", None, True)
    daily = pa.create_alert(USER, "ZZALERT", "pct_move", None, 2, "either", None, False)

    def run(price):
        quote = {"ZZALERT": {"price": price, "prev_close": 100.0, "source": "test"}}
        with (
            patch("src.ingestion.market_hours.is_market_open", return_value=True),
            patch("src.ingestion.market_hours.today_ist", return_value=TODAY),
            patch.object(pa, "quotes", return_value=quote),
        ):
            return pa.check_price_alerts(NOW)

    assert run(101) == {"checked": 3, "rang": 1}  # only "above 100"
    rows = {r["id"]: r for r in pa.list_alerts(USER)}
    assert rows[once["id"]]["active"] is False and rows[once["id"]]["triggered_price"] == 101

    assert run(94)["rang"] == 2  # below 95 and a 6% move
    rows = {r["id"]: r for r in pa.list_alerts(USER)}
    assert rows[again["id"]]["active"] and not rows[again["id"]]["armed"]
    assert rows[daily["id"]]["triggered_on"] == TODAY

    assert run(93)["rang"] == 0  # still below, and the daily move already rang today
    run(97)  # crosses back above 95: re-armed
    assert {r["id"]: r for r in pa.list_alerts(USER)}[again["id"]]["armed"]
    assert run(94)["rang"] == 1


@pytest.mark.postgres
def test_market_closed_checks_nothing(db_session):
    pa.create_alert(USER, "ZZALERT", "above", 1, None, "either", None, False)
    with patch("src.ingestion.market_hours.is_market_open", return_value=False):
        assert pa.check_price_alerts(NOW) == {"checked": 0, "rang": 0}


@pytest.mark.postgres
def test_fresh_kite_price_is_used_before_yahoo(db_session):
    token = 2_147_000_301
    db_session.execute(
        text(
            """INSERT INTO instrument_master (instrument_token, exchange_token, tradingsymbol, exchange)
               VALUES (:t, :t, 'ZZKITE', 'NSE') ON CONFLICT (instrument_token) DO NOTHING"""
        ),
        {"t": token},
    )
    db_session.execute(
        text(
            """INSERT INTO live_prices (instrument_token, last_price, close_price, last_updated)
               VALUES (:t, 250.5, 240, NOW())"""
        ),
        {"t": token},
    )
    with patch.object(
        pa, "_yahoo_quote", return_value={"price": 1, "prev_close": 1, "source": "y"}
    ):
        got = pa.quotes(["ZZKITE", "ZZOTHER"])
    assert got["ZZKITE"] == {
        "tradingsymbol": "ZZKITE",
        "price": 250.5,
        "prev_close": 240.0,
        "source": "kite",
    }
    assert got["ZZOTHER"]["source"] == "y"


@pytest.mark.postgres
def test_bell_items_escape_the_note(db_session):
    a = pa.create_alert(
        USER, "M&M", "below", 50, None, "either", "<img src=x onerror=alert(1)>", False
    )
    db_session.execute(
        text("UPDATE price_alerts SET triggered_at = :t, triggered_price = 49 WHERE id = :i"),
        {"t": NOW, "i": a["id"]},
    )
    (item,) = pa.bell_items(USER, NOW)
    assert "<img" not in item["message"] and "&lt;img" in item["message"]
    assert item["title"] == "M&amp;M falls to ₹50.00"
    assert item["action_url"] == "/analyzer.html?sym=M%26M"
    assert item["id"].startswith(f"price_alert_{a['id']}_")


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


def test_api_validates_rules(client, headers):
    no_pct = client.post(
        "/api/v1/alerts/price", headers=headers, json={"symbol": "INFY", "kind": "pct_move"}
    )
    assert no_pct.status_code == 422
    no_price = client.post(
        "/api/v1/alerts/price", headers=headers, json={"symbol": "INFY", "kind": "above"}
    )
    assert no_price.status_code == 422
    assert client.get("/api/v1/alerts/price").status_code == 401


@pytest.mark.postgres
def test_api_lifecycle(client, headers, db_session):
    body = {"user_id": USER, "symbol": "INFY", "kind": "below", "price": 1400, "note": "stop"}
    created = client.post("/api/v1/alerts/price", headers=headers, json=body)
    assert created.status_code == 201
    aid = created.get_json()["data"]["id"]
    assert created.get_json()["data"]["label"] == "INFY falls to ₹1,400.00"
    listed = client.get(f"/api/v1/alerts/price?user_id={USER}", headers=headers).get_json()
    assert [a["id"] for a in listed["data"]["alerts"]] == [aid]
    off = client.patch(
        f"/api/v1/alerts/price/{aid}", headers=headers, json={"user_id": USER, "active": False}
    )
    assert off.status_code == 200 and off.get_json()["data"]["active"] is False
    gone = client.delete(f"/api/v1/alerts/price/{aid}?user_id={USER}", headers=headers)
    assert gone.status_code == 200
    again = client.delete(f"/api/v1/alerts/price/{aid}?user_id={USER}", headers=headers)
    assert again.status_code == 404
