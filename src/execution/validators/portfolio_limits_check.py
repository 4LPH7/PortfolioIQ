"""
Portfolio limits: a BUY must not push one sector, or the portfolio's drawdown, past your limits.

Live, a breach rejects the order, and so does a check that can't be confirmed (no recent equity
curve). In dry run a breach passes with a "WOULD BLOCK LIVE" note and a missing input with an
"UNVERIFIED" note, matching the other order guards. Sells are never blocked: they reduce risk.
Sector weights use mapped sectors only; a stock with no sector skips just the sector check.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.analytics.rebalancer import OrderSide, RebalanceOrder
from src.db.connection import execute_sql
from src.ingestion.market_hours import today_ist

DEFAULT_SECTOR_PCT = 35.0
DEFAULT_DRAWDOWN_PCT = 25.0
CURVE_DAYS = 365
MAX_CURVE_AGE_DAYS = 7


def _today() -> date:
    return today_ist()


def _config() -> dict[str, float]:
    rows = execute_sql(
        "SELECT key, value FROM system_config "
        "WHERE key IN ('risk_block_max_sector_pct', 'risk_block_max_drawdown_pct')"
    )
    cfg = {r["key"]: r["value"] for r in rows}

    def num(key: str, default: float) -> float:
        try:
            return float(cfg[key])
        except (KeyError, TypeError, ValueError):
            return default

    return {
        "sector": num("risk_block_max_sector_pct", DEFAULT_SECTOR_PCT),
        "drawdown": num("risk_block_max_drawdown_pct", DEFAULT_DRAWDOWN_PCT),
    }


def _position(order: RebalanceOrder, user_id: str) -> dict:
    """The order stock's sector, that sector's value, and the whole portfolio's value."""
    rows = execute_sql(
        """
        SELECT im.sector AS sector,
               (h.quantity + h.t1_quantity) * COALESCE(lp.last_price, h.last_price, 0) AS value
        FROM user_holdings h
        JOIN instrument_master im ON im.instrument_token = h.instrument_token
        LEFT JOIN live_prices lp ON lp.instrument_token = h.instrument_token
        WHERE h.user_id = :uid AND (h.quantity + h.t1_quantity) > 0
        """,
        {"uid": user_id},
    )
    sector_rows = execute_sql(
        "SELECT sector FROM instrument_master WHERE instrument_token = :t",
        {"t": order.instrument_token},
    )
    sector = sector_rows[0]["sector"] if sector_rows else None
    total = sum(float(r["value"] or 0) for r in rows)
    in_sector = sum(float(r["value"] or 0) for r in rows if sector and r["sector"] == sector)
    return {"sector": sector, "sector_value": in_sector, "total_value": total}


def _nav_history(user_id: str) -> list[tuple[date, float]]:
    """Unit NAV (time-weighted, so deposits don't hide a fall) for the last year, oldest first."""
    rows = execute_sql(
        """SELECT snapshot_date, unit_nav::float AS nav FROM portfolio_daily_snapshots
           WHERE user_id = :uid AND snapshot_date >= :since ORDER BY snapshot_date""",
        {"uid": user_id, "since": _today() - timedelta(days=CURVE_DAYS)},
    )
    return [(r["snapshot_date"], r["nav"]) for r in rows]


def validate_portfolio_limits(
    order: RebalanceOrder, live: bool, user_id: str = "default"
) -> tuple[bool, str]:
    """Returns (passed, message). Dry-run caveats start with "WOULD BLOCK LIVE:" or "UNVERIFIED:"."""
    if order.side is not OrderSide.BUY:
        return True, "Sells are never blocked by portfolio limits."

    cfg = _config()
    notes: list[str] = []
    breaches: list[str] = []
    unverified: list[str] = []

    pos = _position(order, user_id)
    if not pos["total_value"]:
        notes.append("portfolio is empty, sector limit skipped")
    elif not pos["sector"]:
        notes.append("sector unknown, sector limit skipped")
    else:
        added = float(order.estimated_value)
        after = (pos["sector_value"] + added) * 100 / (pos["total_value"] + added)
        if after > cfg["sector"]:
            breaches.append(
                f"Sector breach: {pos['sector']} would be {after:.1f}% of the portfolio "
                f"(limit {cfg['sector']:g}%)."
            )
        else:
            notes.append(f"{pos['sector']} {after:.1f}% (limit {cfg['sector']:g}%)")

    navs = _nav_history(user_id)
    if len(navs) < 2 or (_today() - navs[-1][0]).days > MAX_CURVE_AGE_DAYS:
        unverified.append("no recent equity-curve snapshots to measure drawdown")
    else:
        peak = max(n for _, n in navs)
        drawdown = (peak - navs[-1][1]) * 100 / peak
        if drawdown > cfg["drawdown"]:
            breaches.append(
                f"Drawdown breach: portfolio is {drawdown:.1f}% below its peak "
                f"(limit {cfg['drawdown']:g}%)."
            )
        else:
            notes.append(f"drawdown {drawdown:.1f}% (limit {cfg['drawdown']:g}%)")

    if breaches:
        text = " ".join(breaches)
        return (False, f"{text} Buy not sent.") if live else (True, f"WOULD BLOCK LIVE: {text}")
    if unverified:
        reason = "; ".join(unverified)
        if live:
            return False, f"Portfolio limits can't be confirmed: {reason}. Order not sent."
        return True, f"UNVERIFIED: portfolio limits not checked ({reason})."
    return True, "Portfolio limits OK: " + "; ".join(notes) + "."
