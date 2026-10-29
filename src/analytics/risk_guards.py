"""
Risk guard alerts: holding loss, trailing stop, sector exposure.

After QuantConnect Lean's risk models (MaximumDrawdownPercentPerSecurity,
TrailingStopRiskManagementModel, MaximumSectorExposureRiskManagementModel). Lean's models
liquidate; these only raise alerts. Nothing here can create, size or send an order.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from src.analytics.data_quality import assess
from src.analytics.valuator import PortfolioValuation
from src.db.connection import execute_sql

TRAILING_WINDOW = 60  # trading days for the trailing high
MIN_TRAILING_BARS = 40  # fewer and the "60-day high" is mostly missing data
UNMAPPED_SECTOR = "Uncategorised"


@dataclass(frozen=True)
class GuardThresholds:
    max_holding_loss_pct: float = 15.0
    trailing_stop_pct: float = 12.0
    max_sector_pct: float = 30.0


def load_thresholds() -> GuardThresholds:
    rows = execute_sql(
        """SELECT key, value FROM system_config
           WHERE key IN ('risk_max_holding_loss_pct', 'risk_trailing_stop_pct',
                         'risk_max_sector_pct')"""
    )
    cfg = {r["key"]: r["value"] for r in rows}
    d = GuardThresholds()

    def num(key: str, default: float) -> float:
        try:
            return float(cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    return GuardThresholds(
        max_holding_loss_pct=num("risk_max_holding_loss_pct", d.max_holding_loss_pct),
        trailing_stop_pct=num("risk_trailing_stop_pct", d.trailing_stop_pct),
        max_sector_pct=num("risk_max_sector_pct", d.max_sector_pct),
    )


def _alert(alert_id: str, severity: str, title: str, message: str, now: datetime, **meta) -> dict:
    return {
        "id": alert_id,
        "type": "RISK",
        "severity": severity,
        "title": title,
        "message": message,
        "action_label": "Open Analyzer" if meta.get("symbol") else "View Rebalance",
        "action_url": f"/analyzer.html?sym={meta['symbol']}"
        if meta.get("symbol")
        else "/rebalance.html",
        "timestamp": now.isoformat(),
        "metadata": meta,
    }


def _notify_loss_alerts(alerts: list[dict]) -> None:
    """Push new holding-loss warnings. The alert itself is unchanged; this only tells you."""
    from src.ingestion import notify

    for a in alerts:
        if a["id"].startswith("risk_loss_"):
            title, body = notify.guard_message(a["id"][len("risk_loss_") :], a["title"])
            notify.send(
                "guard_critical", title, body, dedupe_key=f"{a['id']}:{a['timestamp'][:10]}"
            )


def evaluate_risk_guards(
    valuation: PortfolioValuation,
    thresholds: GuardThresholds,
    load_bars: Callable[[str], pd.DataFrame],
    now: datetime,
) -> list[dict]:
    """Alert dicts in the /alerts envelope. `load_bars(symbol)` returns adjusted daily bars."""
    alerts: list[dict] = []
    skipped: list[str] = []

    for h in valuation.holdings:
        sym = h.tradingsymbol
        loss = float(h.unrealised_pnl_pct)
        if h.invested_value > 0 and loss <= -thresholds.max_holding_loss_pct:
            alerts.append(
                _alert(
                    f"risk_loss_{sym}",
                    "WARNING",
                    f"{sym} is {abs(loss):.1f}% below cost",
                    f"Unrealised loss {loss:.1f}% passes your {thresholds.max_holding_loss_pct:g}% "
                    "holding-loss limit.",
                    now,
                    symbol=sym,
                    loss_pct=loss,
                )
            )

        try:
            closes = load_bars(sym)["Close"].dropna().astype(float).tail(TRAILING_WINDOW)
        except Exception:
            closes = pd.Series(dtype=float)
        reason = assess(closes, MIN_TRAILING_BARS, now.date())
        if reason:
            skipped.append(f"{sym}: {reason}")
            continue
        high = float(closes.max())
        off_high = (float(closes.iloc[-1]) / high - 1.0) * 100.0 if high > 0 else 0.0
        if off_high <= -thresholds.trailing_stop_pct:
            alerts.append(
                _alert(
                    f"risk_trail_{sym}",
                    "WARNING",
                    f"{sym} is {abs(off_high):.1f}% off its 60-day high",
                    f"Last close is {abs(off_high):.1f}% below the {TRAILING_WINDOW}-day high "
                    f"(dividend-adjusted), past your {thresholds.trailing_stop_pct:g}% trailing stop.",
                    now,
                    symbol=sym,
                    off_high_pct=round(off_high, 2),
                )
            )

    for sector, weight in valuation.sector_weights.items():
        w = float(weight)
        if sector and sector != UNMAPPED_SECTOR and w > thresholds.max_sector_pct:
            alerts.append(
                _alert(
                    f"risk_sector_{sector}",
                    "WARNING",
                    f"{sector} is {w:.1f}% of the portfolio",
                    f"Above your {thresholds.max_sector_pct:g}% single-sector limit.",
                    now,
                    sector=sector,
                    weight_pct=w,
                )
            )

    if skipped:
        alerts.append(
            _alert(
                "risk_trail_skipped",
                "INFO",
                "Trailing-stop check skipped",
                f"Price data failed a check, so no trailing-stop alert: {'; '.join(skipped)}.",
                now,
                symbols=skipped,
            )
        )
    _notify_loss_alerts(alerts)
    return alerts
