"""News on holdings: feed parsing and link safety, tags, the size cap, storage and the API."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.ingestion import news

FEED = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Test</title>
<item><title>TestCo Q2 profit rises 12%, board declares interim dividend</title>
  <link>https://www.example.com/a</link><pubDate>Mon, 26 Oct 2026 05:56:49 +0000</pubDate></item>
<item><title>Click me</title><link>javascript:alert(1)</link><pubDate>Mon, 26 Oct 2026 05:00:00 +0000</pubDate></item>
<item><title></title><link>https://example.com/empty</link></item>
<item><title>SEBI probe into TestCo   order</title><link>http://news.example.org/b</link>
  <pubDate>not a date</pubDate></item>
</channel></rss>"""


def test_parse_keeps_web_links_and_reads_dates():
    items = news.parse_feed(FEED)
    assert [i["link"] for i in items] == ["https://www.example.com/a", "http://news.example.org/b"]
    first, second = items
    assert first["source"] == "example.com"
    assert first["published_at"].isoformat() == "2026-10-26T05:56:49+00:00"
    assert second["published_at"] is None and second["title"] == "SEBI probe into TestCo order"


def test_tags():
    assert news.tag("TestCo Q2 profit rises 12%, board declares interim dividend") == [
        "results",
        "dividend",
    ]
    assert news.tag("Brokerage upgrades TestCo, raises target price") == ["rating"]
    assert news.tag("TestCo CEO resigns") == ["management"]
    assert news.tag("Infosys shares dip as deal wins offset outlook") == ["deal"]
    assert news.tag("Markets open flat") == []


def _response(body: bytes):
    return SimpleNamespace(
        raise_for_status=lambda: None,
        raw=SimpleNamespace(read=lambda n, decode_content=True: body[:n]),
    )


def test_fetch_uses_the_nse_ticker_and_caps_size():
    with patch.object(news.requests, "get", return_value=_response(FEED)) as get:
        assert len(news.fetch_symbol("TESTCO")) == 2
    assert "s=TESTCO.NS" in get.call_args.args[0]
    with (
        patch.object(news.requests, "get", return_value=_response(b"x" * (news.MAX_BYTES + 5))),
        pytest.raises(ValueError, match="larger than 1 MB"),
    ):
        news.fetch_symbol("TESTCO")


@pytest.mark.postgres
def test_refresh_stores_new_headlines_once(db_session, monkeypatch):
    monkeypatch.setattr(news.time, "sleep", lambda s: None)

    def fetch(sym):
        if sym == "ZZDOWN":
            raise OSError("feed down")
        return news.parse_feed(FEED)

    monkeypatch.setattr(news, "fetch_symbol", fetch)
    assert news.refresh_news(["ZZNEWS", "ZZDOWN"]) == {"saved": 2, "failed": 1, "symbols": 2}
    assert news.refresh_news(["ZZNEWS"])["saved"] == 0  # same links: nothing new
    rows = news.recent_news("ZZNEWS", days=30)
    # an undated headline sorts by when it was fetched, so it comes first
    assert [r["tags"] for r in rows] == [["regulatory"], ["results", "dividend"]]


# ── API ──


@pytest.fixture
def client():
    from flask_app import app

    os.environ["TESTING"] = "true"
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.mark.postgres
def test_api_lists_news_and_clamps_limits(client, db_session, monkeypatch):
    from src.config.settings import get_settings

    monkeypatch.setattr(news.time, "sleep", lambda s: None)
    monkeypatch.setattr(news, "fetch_symbol", lambda sym: news.parse_feed(FEED))
    news.refresh_news(["ZZNEWS"])
    headers = {"X-API-Key": get_settings().portfolioiq_api_key}
    res = client.get("/api/v1/news?symbol=zznews&days=999&limit=1", headers=headers)
    assert res.status_code == 200
    (item,) = res.get_json()["data"]
    assert item["tradingsymbol"] == "ZZNEWS"
    assert client.get("/api/v1/news").status_code == 401
