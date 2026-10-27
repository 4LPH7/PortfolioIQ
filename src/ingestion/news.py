"""
Headlines for holdings, after trader-news in Jakeschincariol/day-trader-skill (MIT).

Source: Yahoo Finance's free RSS feed per NSE ticker. It is unofficial and can lag, change or
stop; ETFs usually have no headlines. Fetched on a schedule and stored, so no page waits on it.
Titles are untrusted text: pages escape them, and they never reach the research committee.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests
from loguru import logger

from src.db.connection import execute_sql

FEED = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={}&region=IN&lang=en-IN"
MAX_BYTES = 1_000_000
KEEP_DAYS = 30
TAGS = {
    "results": r"results?|earnings|q[1-4]|quarterly|profit|revenue|guidance|margins?",
    "dividend": r"dividend|bonus issue|record date|stock split",
    "deal": r"acqui\w*|merger|stake|buyback|order wins?|contract|deals?",
    "rating": r"upgrades?d?|downgrades?d?|target price|price target|outperform|underperform",
    "regulatory": r"sebi|rbi|penalty|probe|investigation|lawsuit|court|tribunal",
    "management": r"ceo|cfo|chairman|resigns?|resignation|appoint\w*|steps down",
}
_TAG_RE = {k: re.compile(rf"\b(?:{v})\b", re.IGNORECASE) for k, v in TAGS.items()}


def tag(title: str) -> list[str]:
    return [k for k, rx in _TAG_RE.items() if rx.search(title)]


def parse_feed(xml_bytes: bytes) -> list[dict]:
    """Items with an http(s) link only; anything else (javascript:, data:) is dropped."""
    items = []
    for it in ET.fromstring(xml_bytes).iter("item"):
        title = " ".join((it.findtext("title") or "").split())[:300]
        link = (it.findtext("link") or "").strip()
        url = urlparse(link)
        if not title or url.scheme not in ("http", "https") or not url.netloc:
            continue
        try:
            published = parsedate_to_datetime(it.findtext("pubDate") or "")
            published = published if published.tzinfo else published.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            published = None
        items.append(
            {
                "title": title,
                "link": link[:1000],
                "source": url.netloc.removeprefix("www."),
                "published_at": published,
                "tags": tag(title),
            }
        )
    return items


def fetch_symbol(symbol: str) -> list[dict]:
    from src.ingestion.ticker_map import get_yf_ticker

    resp = requests.get(
        FEED.format(get_yf_ticker(symbol)),
        headers={"User-Agent": "Mozilla/5.0 PortfolioIQ"},
        timeout=10,
        stream=True,
    )
    resp.raise_for_status()
    body = resp.raw.read(MAX_BYTES + 1, decode_content=True)
    if len(body) > MAX_BYTES:
        raise ValueError("feed larger than 1 MB")
    return parse_feed(body)


def refresh_news(symbols: list[str]) -> dict[str, int]:
    saved = failed = 0
    for sym in symbols:
        try:
            items = fetch_symbol(sym)
        except Exception as exc:
            failed += 1
            logger.warning("News feed for {} unavailable: {}", sym, exc)
            continue
        for it in items:
            saved += len(
                execute_sql(
                    """INSERT INTO news_items (tradingsymbol, title, link, source, published_at, tags)
                       VALUES (:sym, :title, :link, :source, :published_at, :tags)
                       ON CONFLICT (tradingsymbol, link) DO NOTHING RETURNING id""",
                    {"sym": sym, **it},
                )
            )
        time.sleep(0.5)  # one ticker at a time, politely
    execute_sql(
        "DELETE FROM news_items WHERE fetched_at < NOW() - make_interval(days => :d)",
        {"d": KEEP_DAYS},
    )
    logger.info("News refresh: {} new headlines, {} feeds failed", saved, failed)
    return {"saved": saved, "failed": failed, "symbols": len(symbols)}


def recent_news(symbol: str | None = None, days: int = 7, limit: int = 50) -> list[dict]:
    return execute_sql(
        """SELECT tradingsymbol, title, link, source, published_at, tags FROM news_items
           WHERE (CAST(:sym AS TEXT) IS NULL OR tradingsymbol = :sym)
             AND COALESCE(published_at, fetched_at) > NOW() - make_interval(days => :days)
           ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT :n""",
        {"sym": symbol, "days": days, "n": limit},
    )
