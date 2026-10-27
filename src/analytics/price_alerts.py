"""
Price alerts you set, after trader-alerts in Jakeschincariol/day-trader-skill (MIT).

An alert is information, never an order. Rules:
- above / below: rings when the price is at or beyond the level, once. With `rearm` it rings
  again only after the price has crossed back; without it the alert turns itself off.
- pct_move: rings when the move from the previous close reaches `pct` (up, down or either),
  at most once per IST trading day.

Quotes come from the Kite poller's live_prices when fresh, otherwise from Yahoo's free chart
endpoint, which is delayed and unofficial. Checks run during market hours only.
"""

from __future__ import annotations

import html
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

import requests
from loguru import logger

from src.db.connection import execute_sql

FRESH_SECONDS = 300
MAX_ALERTS = 100
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=1d&interval=1d"
COLS = """id, tradingsymbol, kind, price::float AS price, pct::float AS pct, direction, note,
    rearm, active, armed, triggered_at, triggered_price::float AS triggered_price, triggered_on,
    last_price::float AS last_price, last_quote_source, last_checked_at, created_at"""


def create_alert(
    user_id: str,
    symbol: str,
    kind: str,
    price: float | None,
    pct: float | None,
    direction: str,
    note: str | None,
    rearm: bool,
) -> dict:
    count = execute_sql("SELECT COUNT(*) AS n FROM price_alerts WHERE user_id = :u", {"u": user_id})
    if count[0]["n"] >= MAX_ALERTS:
        raise ValueError(f"You already have {MAX_ALERTS} alerts. Delete some first.")
    return execute_sql(
        f"""INSERT INTO price_alerts (user_id, tradingsymbol, kind, price, pct, direction, note, rearm)
            VALUES (:u, :sym, :kind, :price, :pct, :dir, :note, :rearm) RETURNING {COLS}""",
        {
            "u": user_id,
            "sym": symbol,
            "kind": kind,
            "price": price if kind != "pct_move" else None,
            "pct": pct if kind == "pct_move" else None,
            "dir": direction,
            "note": note,
            "rearm": rearm,
        },
    )[0]


def list_alerts(user_id: str) -> list[dict]:
    return execute_sql(
        f"SELECT {COLS} FROM price_alerts WHERE user_id = :u ORDER BY active DESC, created_at DESC",
        {"u": user_id},
    )


def set_active(user_id: str, alert_id: int, active: bool) -> dict | None:
    """Turning an alert back on re-arms it and lets a percent-move alert ring again today."""
    rows = execute_sql(
        f"""UPDATE price_alerts SET active = :a,
                armed = CASE WHEN :a THEN TRUE ELSE armed END,
                triggered_on = CASE WHEN :a THEN NULL ELSE triggered_on END
            WHERE user_id = :u AND id = :id RETURNING {COLS}""",
        {"u": user_id, "id": alert_id, "a": active},
    )
    return rows[0] if rows else None


def delete_alert(user_id: str, alert_id: int) -> bool:
    return bool(
        execute_sql(
            "DELETE FROM price_alerts WHERE user_id = :u AND id = :id RETURNING id",
            {"u": user_id, "id": alert_id},
        )
    )


def evaluate(alert: dict, price: float, prev_close: float | None, today: date) -> str | None:
    """ "ring", "rearm" or None. Pure, so every rule is testable without a quote."""
    if alert["kind"] == "pct_move":
        if not prev_close or alert["triggered_on"] == today:
            return None
        move = (price / prev_close - 1) * 100
        pct, way = alert["pct"], alert["direction"]
        hit = move >= pct if way == "up" else move <= -pct if way == "down" else abs(move) >= pct
        return "ring" if hit else None
    beyond = price >= alert["price"] if alert["kind"] == "above" else price <= alert["price"]
    if alert["armed"] and beyond:
        return "ring"
    if not alert["armed"] and alert["rearm"] and not beyond:
        return "rearm"
    return None


def _yahoo_quote(symbol: str) -> dict | None:
    from src.ingestion.ticker_map import get_yf_ticker

    try:
        resp = requests.get(
            YAHOO_CHART.format(get_yf_ticker(symbol)),
            headers={"User-Agent": "Mozilla/5.0 PortfolioIQ"},
            timeout=8,
        )
        meta = resp.json()["chart"]["result"][0]["meta"]
        return {
            "price": float(meta["regularMarketPrice"]),
            "prev_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
            "source": "yahoo (delayed)",
        }
    except Exception as exc:
        logger.warning("Price alert quote for {} unavailable: {}", symbol, exc)
        return None


def quotes(symbols: list[str]) -> dict[str, dict]:
    """Fresh Kite prices first; Yahoo, one request at a time, for the rest."""
    rows = execute_sql(
        """SELECT im.tradingsymbol, lp.last_price::float AS price,
                  lp.close_price::float AS prev_close
           FROM live_prices lp JOIN instrument_master im USING (instrument_token)
           WHERE im.tradingsymbol = ANY(:syms) AND NOT lp.is_stale
             AND lp.last_updated > NOW() - make_interval(secs => :fresh)""",
        {"syms": symbols, "fresh": FRESH_SECONDS},
    )
    out = {r["tradingsymbol"]: {**r, "source": "kite"} for r in rows}
    for sym in symbols:
        if sym not in out:
            q = _yahoo_quote(sym)
            if q:
                out[sym] = q
            time.sleep(0.3)  # one ticker at a time, politely
    return out


def check_price_alerts(now: datetime | None = None) -> dict[str, int]:
    from src.ingestion.market_hours import is_market_open, today_ist

    if not is_market_open():
        return {"checked": 0, "rang": 0}
    now = now or datetime.now(UTC)
    today = today_ist()
    alerts = execute_sql(f"SELECT {COLS} FROM price_alerts WHERE active")
    if not alerts:
        return {"checked": 0, "rang": 0}
    prices = quotes(sorted({a["tradingsymbol"] for a in alerts}))
    rang = 0
    for a in alerts:
        q = prices.get(a["tradingsymbol"])
        if not q:
            continue
        outcome = evaluate(a, q["price"], q["prev_close"], today)
        params = {"id": a["id"], "p": q["price"], "src": q["source"], "now": now, "today": today}
        if outcome == "ring":
            rang += 1
            off = a["kind"] != "pct_move" and not a["rearm"]
            execute_sql(
                f"""UPDATE price_alerts SET triggered_at = :now, triggered_price = :p,
                        triggered_on = :today, armed = FALSE, active = {"FALSE" if off else "TRUE"},
                        last_price = :p, last_quote_source = :src, last_checked_at = :now
                    WHERE id = :id""",
                params,
            )
        else:
            execute_sql(
                f"""UPDATE price_alerts SET last_price = :p, last_quote_source = :src,
                        last_checked_at = :now{", armed = TRUE" if outcome == "rearm" else ""}
                    WHERE id = :id""",
                params,
            )
    logger.info("Price alerts: {} checked, {} rang", len(alerts), rang)
    return {"checked": len(alerts), "rang": rang}


def describe(a: dict) -> str:
    if a["kind"] == "pct_move":
        way = {"up": "rises", "down": "falls", "either": "moves"}[a["direction"]]
        return f"{a['tradingsymbol']} {way} {a['pct']:g}% in a day"
    verb = "rises to" if a["kind"] == "above" else "falls to"
    return f"{a['tradingsymbol']} {verb} ₹{a['price']:,.2f}"


def bell_items(user_id: str, now: datetime, hours: int = 24) -> list[dict]:
    """Recent rings for the notification bell. Text is escaped: the bell inserts it as HTML."""
    rows = execute_sql(
        f"""SELECT {COLS} FROM price_alerts
            WHERE user_id = :u AND triggered_at > :since ORDER BY triggered_at DESC""",
        {"u": user_id, "since": now - timedelta(hours=hours)},
    )
    items = []
    for a in rows:
        note = f" Note: {a['note']}" if a["note"] else ""
        items.append(
            {
                "id": f"price_alert_{a['id']}_{int(a['triggered_at'].timestamp())}",
                "type": "PRICE",
                "severity": "WARNING",
                "title": html.escape(describe(a)),
                "message": html.escape(
                    f"Price ₹{a['triggered_price']:,.2f} ({a['last_quote_source']}). "
                    f"This is an alert only; no order was placed.{note}"
                ),
                "action_label": "Open Analyzer",
                "action_url": f"/analyzer.html?sym={quote(a['tradingsymbol'])}",
                "timestamp": a["triggered_at"].isoformat(),
                "metadata": {"symbol": a["tradingsymbol"], "alert_id": a["id"]},
            }
        )
    return items
