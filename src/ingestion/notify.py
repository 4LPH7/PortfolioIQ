"""
Push notifications through ntfy.sh, opt-in per event.

Off until a topic is set in Settings. The topic is the only secret: ntfy.sh is a public server, so
choose an unguessable name. Messages are plain text; they say when something is information and not
an order, and carry no keys or account numbers. Sending never raises and never blocks the caller
(five-second timeout, failures are logged and reported as not sent).
"""

from __future__ import annotations

import re
from datetime import date

import requests
from loguru import logger

from src.db.connection import execute_sql
from src.ingestion.market_hours import today_ist

TIMEOUT_SEC = 5
TOPIC = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
EVENTS = ("price_alert", "scanner_verdict", "scanner_top5", "guard_critical")
NOT_AN_ORDER = "This is information only. No order was placed."
_sent: set[tuple[str, str, date]] = set()  # dedupe within this process; reset at restart


def _config() -> dict[str, str]:
    rows = execute_sql(
        "SELECT key, value FROM system_config WHERE key LIKE 'ntfy_%' OR key LIKE 'notify_%'"
    )
    return {r["key"]: r["value"] for r in rows}


def _enabled(cfg: dict[str, str], event: str) -> bool:
    return str(cfg.get(f"notify_{event}", "false")).lower() == "true"


def _header_safe(text: str) -> str:
    """HTTP headers are latin-1 on the wire, so the title is sent as ASCII with the rupee sign
    spelled out. The full text, with the rupee sign, goes in the body."""
    return text.replace("₹", "Rs ").encode("ascii", "replace").decode("ascii")


def _today() -> date:
    return today_ist()


def send(
    event: str,
    title: str,
    body: str,
    dedupe_key: str | None = None,
    force: bool = False,
) -> bool:
    """Send one notification. Returns True only when the server accepted it."""
    cfg = _config()
    topic = (cfg.get("ntfy_topic") or "").strip()
    if not topic or not TOPIC.match(topic):
        return False
    if not force and not _enabled(cfg, event):
        return False
    if dedupe_key is not None:
        marker = (event, dedupe_key, _today())
        if marker in _sent:
            return False
    server = (cfg.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
    try:
        resp = requests.post(
            f"{server}/{topic}",
            data=body.encode("utf-8"),
            headers={"Title": _header_safe(title), "Priority": "default"},
            timeout=TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        logger.warning("Notification not sent ({}): {}", event, exc.__class__.__name__)
        return False
    if resp.status_code >= 300:
        logger.warning("Notification rejected by server: HTTP {}", resp.status_code)
        return False
    if dedupe_key is not None:
        _sent.add((event, dedupe_key, _today()))
    return True


def price_alert_message(
    symbol: str, direction: str, level: float, price: float, source: str
) -> tuple[str, str]:
    title = f"{symbol} {direction} ₹{level:,.2f}"
    return title, f"Price ₹{price:,.2f} ({source}). {NOT_AN_ORDER}"


def verdict_change_message(old: str, new: str) -> tuple[str, str]:
    def label(status: str) -> str:
        return "tested edge" if status == "edge" else "no tested edge"

    return (
        f"Scanner verdict: now {label(new)}",
        f"Was {label(old)}. Read the scanner page before acting. {NOT_AN_ORDER}",
    )


def top5_message(symbol: str, rank: int) -> tuple[str, str]:
    return (f"{symbol} entered the scanner's top 5", f"Now rank {rank}. {NOT_AN_ORDER}")


def guard_message(symbol: str, detail: str) -> tuple[str, str]:
    return f"Critical risk alert: {symbol}", f"{detail} {NOT_AN_ORDER}"
