"""ntfy push notifications: off by default, opt-in per event, deduplicated per day, never raises,
and the message carries no account data or secrets."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.ingestion import notify as nt


def _cfg(**over):
    base = {"ntfy_topic": "pf-test-7f3a9c", "ntfy_server": "https://ntfy.sh"}
    base.update({k: str(v).lower() if isinstance(v, bool) else v for k, v in over.items()})
    return base


# ── Off by default and opt-in ──


def test_nothing_is_sent_without_a_topic():
    with (
        patch.object(nt, "_config", return_value={"ntfy_topic": ""}),
        patch.object(nt.requests, "post") as post,
    ):
        assert nt.send("price_alert", "INFY rises to 1,250", "x") is False
    post.assert_not_called()


def test_an_event_is_sent_only_when_its_switch_is_on():
    with (
        patch.object(nt, "_config", return_value=_cfg(notify_price_alert=False)),
        patch.object(nt.requests, "post") as post,
    ):
        assert nt.send("price_alert", "t", "m") is False
    post.assert_not_called()


# ── Sending ──


def test_sends_plain_text_to_the_topic_with_a_title_and_priority():
    resp = MagicMock(status_code=200)
    with (
        patch.object(nt, "_config", return_value=_cfg(notify_price_alert=True)),
        patch.object(nt.requests, "post", return_value=resp) as post,
    ):
        assert nt.send("price_alert", "INFY rises to ₹1,250", "Price ₹1,251 (kite).") is True
    url = post.call_args.args[0]
    kwargs = post.call_args.kwargs
    assert url == "https://ntfy.sh/pf-test-7f3a9c"
    assert kwargs["timeout"] == 5
    assert kwargs["headers"]["Title"] == "INFY rises to Rs 1,250"  # headers must be ASCII
    assert kwargs["headers"]["Priority"] == "default"
    assert kwargs["data"] == "Price ₹1,251 (kite).".encode()  # the body keeps the rupee sign


def test_a_failed_or_slow_server_never_raises():
    for effect in (requests.Timeout("slow"), requests.ConnectionError("down")):
        with (
            patch.object(nt, "_config", return_value=_cfg(notify_price_alert=True)),
            patch.object(nt.requests, "post", side_effect=effect),
        ):
            assert nt.send("price_alert", "t", "m") is False


def test_a_rejected_status_is_reported_as_not_sent():
    with (
        patch.object(nt, "_config", return_value=_cfg(notify_price_alert=True)),
        patch.object(nt.requests, "post", return_value=MagicMock(status_code=500)),
    ):
        assert nt.send("price_alert", "t", "m") is False


def test_the_same_event_is_sent_once_per_day():
    resp = MagicMock(status_code=200)
    key = "scanner_top5:INFY"
    with (
        patch.object(nt, "_config", return_value=_cfg(notify_scanner_top5=True)),
        patch.object(nt.requests, "post", return_value=resp) as post,
        patch.object(nt, "_today", return_value=date(2026, 10, 29)),
    ):
        nt.send("scanner_top5", "t", "m", dedupe_key=key)
        nt.send("scanner_top5", "t", "m", dedupe_key=key)
        with patch.object(nt, "_today", return_value=date(2026, 10, 30)):
            nt.send("scanner_top5", "t", "m", dedupe_key=key)
    assert post.call_count == 2


# ── Message content ──


def test_the_message_never_carries_the_key_or_account_numbers():
    text = nt.price_alert_message("INFY", "rises", 1250, 1251.0, "kite")
    title, body = text
    assert "ksk" not in body.lower() and "api" not in body.lower()
    assert "no order" in body.lower()  # every notification says it is not an order


def test_scanner_verdict_change_only_names_the_change():
    title, body = nt.verdict_change_message("no_edge", "edge")
    assert "tested edge" in title.lower()
    assert "no order was placed" in body.lower()


def test_topic_is_validated_so_it_cannot_inject_a_path():
    with (
        patch.object(
            nt, "_config", return_value=_cfg(ntfy_topic="../admin", notify_price_alert=True)
        ),
        patch.object(nt.requests, "post") as post,
    ):
        assert nt.send("price_alert", "t", "m") is False
    post.assert_not_called()


# ── Settings API ──


@pytest.fixture
def client():
    import os

    from flask_app import app

    os.environ["TESTING"] = "true"
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def headers():
    from src.config.settings import get_settings

    return {"X-API-Key": get_settings().portfolioiq_api_key}


def test_test_button_sends_one_message(client, headers):
    with (
        patch.object(nt, "_config", return_value=_cfg()),
        patch.object(nt, "send", return_value=True) as send,
    ):
        res = client.post("/api/v1/notify/test", headers=headers, json={})
    assert res.status_code == 200 and res.get_json()["data"]["sent"] is True
    assert send.call_args.kwargs.get("force") is True


def test_test_button_reports_when_no_topic_is_set(client, headers):
    with patch.object(nt, "_config", return_value={"ntfy_topic": ""}):
        res = client.post("/api/v1/notify/test", headers=headers, json={})
    assert res.status_code == 422 and res.get_json()["error"]["code"] == "NOTIFY_OFF"


# ── Triggers ──


def test_a_rung_price_alert_pushes_once_per_day():
    from src.analytics import price_alerts as pa

    alert = {
        "id": 7,
        "tradingsymbol": "INFY",
        "kind": "above",
        "price": 1250.0,
        "rearm": False,
        "pct": None,
        "triggered_on": date(2026, 10, 29),
    }
    with patch.object(nt, "send", return_value=True) as send:
        pa._notify_ring(alert, 1251.0)
    assert send.call_args.args[0] == "price_alert"
    assert (
        send.call_args.args[1] == "INFY rises to ₹1,250.00"
    )  # the header form is checked in send()
    assert send.call_args.kwargs["dedupe_key"] == "alert:7:2026-10-29"


def test_scanner_pushes_a_verdict_flip_and_new_top_five_entrants():
    from src.analytics import scanner

    prev = {
        "status": "no_edge",
        "as_of": "2026-10-28",
        "ranking": [{"symbol": "AAA", "rank": 1}, {"symbol": "BBB", "rank": 2}],
    }
    now = {
        "status": "edge",
        "as_of": "2026-10-29",
        "ranking": [{"symbol": "AAA", "rank": 1}, {"symbol": "CCC", "rank": 2}],
    }
    with (
        patch.object(scanner, "_previous_completed", return_value=prev),
        patch.object(nt, "send", return_value=True) as send,
    ):
        scanner._notify_changes(now)
    events = [c.args[0] for c in send.call_args_list]
    assert events == ["scanner_verdict", "scanner_top5"]


def test_scanner_pushes_nothing_on_the_first_ever_scan():
    from src.analytics import scanner

    with (
        patch.object(scanner, "_previous_completed", return_value=None),
        patch.object(nt, "send", return_value=True) as send,
    ):
        scanner._notify_changes(
            {"status": "edge", "as_of": "2026-10-29", "ranking": [{"symbol": "AAA", "rank": 1}]}
        )
    send.assert_not_called()


def test_a_notification_failure_does_not_fail_the_scan():
    from src.analytics import scanner

    with patch.object(scanner, "_previous_completed", side_effect=RuntimeError("db down")):
        scanner._notify_changes({"status": "edge", "as_of": "2026-10-29", "ranking": []})


def test_holding_loss_warning_pushes_only_the_loss_guard():
    from src.analytics import risk_guards

    alerts = [
        {
            "id": "risk_loss_TATAMOTORS",
            "title": "TATAMOTORS is 28.9% below cost",
            "timestamp": "2026-10-29T10:00:00",
        },
        {"id": "risk_trail_ITC", "title": "ITC is 15% off", "timestamp": "2026-10-29T10:00:00"},
    ]
    with patch.object(nt, "send", return_value=True) as send:
        risk_guards._notify_loss_alerts(alerts)
    assert [c.args[0] for c in send.call_args_list] == ["guard_critical"]
    assert send.call_args.args[1] == "Critical risk alert: TATAMOTORS"


def test_notification_settings_are_validated():
    from src.db import repository as repo

    with pytest.raises(ValueError, match="ntfy_topic"):
        repo._normalise_config_value("ntfy_topic", "string", "../admin")
    with pytest.raises(ValueError, match="https"):
        repo._normalise_config_value("ntfy_server", "string", "http://evil.example")
    assert repo._normalise_config_value("ntfy_topic", "string", "") == ""
    assert repo._normalise_config_value("notify_price_alert", "boolean", "TRUE") == "true"
