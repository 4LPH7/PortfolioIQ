"""
PortfolioIQ — Tableau EOD Export
Generates CSV files for Tableau Desktop / Public dashboards.

Exports at 3:45 PM IST daily:
    1. portfolio_snapshot_{date}.csv — full valued holdings
    2. sector_allocation_{date}.csv — sector weights
    3. price_history_{date}.csv — intraday price ticks
"""
from __future__ import annotations

import csv
import os
from datetime import date
from pathlib import Path

from loguru import logger

from src.analytics.valuator import get_valuation_summary
from src.db.connection import execute_sql

EXPORT_DIR = Path(__file__).parent.parent.parent / "exports"


def _ensure_export_dir() -> Path:
    """Create exports/ directory if it doesn't exist."""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    return EXPORT_DIR


def export_portfolio_snapshot(today: date | None = None) -> Path:
    """Export full portfolio with valuations to CSV."""
    today = today or date.today()
    summary = get_valuation_summary()

    filepath = _ensure_export_dir() / f"portfolio_snapshot_{today}.csv"

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "date", "symbol", "exchange", "sector", "qty",
            "avg_price", "current_price", "invested", "current_value",
            "pnl", "pnl_pct", "day_change", "weight_pct", "price_source",
        ])
        writer.writeheader()
        for h in summary["holdings"]:
            writer.writerow({
                "date": str(today),
                "symbol": h["symbol"],
                "exchange": h["exchange"],
                "sector": h.get("sector", ""),
                "qty": h["qty"],
                "avg_price": h["avg_price"],
                "current_price": h["current_price"],
                "invested": h["invested"],
                "current_value": h["current_value"],
                "pnl": h["pnl"],
                "pnl_pct": h["pnl_pct"],
                "day_change": h["day_change"],
                "weight_pct": h["weight_pct"],
                "price_source": h["price_source"],
            })

    logger.info("Exported portfolio snapshot: {}", filepath)
    return filepath


def export_sector_allocation(today: date | None = None) -> Path:
    """Export sector allocation weights to CSV."""
    today = today or date.today()
    summary = get_valuation_summary()

    filepath = _ensure_export_dir() / f"sector_allocation_{today}.csv"

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "sector", "weight_pct"])
        writer.writeheader()
        for sector, weight in summary["sector_weights"].items():
            writer.writerow({
                "date": str(today),
                "sector": sector,
                "weight_pct": weight,
            })

    logger.info("Exported sector allocation: {}", filepath)
    return filepath


def export_price_history(today: date | None = None) -> Path:
    """Export today's intraday price ticks to CSV."""
    today = today or date.today()

    rows = execute_sql("""
        SELECT
            ph.instrument_token,
            im.tradingsymbol,
            ph.last_price,
            ph.open_price,
            ph.high_price,
            ph.low_price,
            ph.volume,
            ph.change_percent,
            ph.recorded_at
        FROM price_history ph
        JOIN instrument_master im ON ph.instrument_token = im.instrument_token
        WHERE ph.recorded_at::date = :today
        ORDER BY ph.recorded_at ASC
    """, {"today": str(today)})

    filepath = _ensure_export_dir() / f"price_history_{today}.csv"

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "timestamp", "symbol", "last_price", "open", "high", "low",
            "volume", "change_pct",
        ])
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "timestamp": str(r["recorded_at"]),
                "symbol": r["tradingsymbol"],
                "last_price": r["last_price"],
                "open": r.get("open_price"),
                "high": r.get("high_price"),
                "low": r.get("low_price"),
                "volume": r.get("volume"),
                "change_pct": r.get("change_percent"),
            })

    logger.info("Exported price history: {} ({} records)", filepath, len(rows))
    return filepath


def run_eod_export(today: date | None = None) -> list[Path]:
    """Run all EOD exports. Called by the scheduler at 3:45 PM IST."""
    today = today or date.today()
    logger.info("Running EOD export for {}...", today)

    paths = [
        export_portfolio_snapshot(today),
        export_sector_allocation(today),
        export_price_history(today),
    ]

    logger.success("EOD export complete. {} files written.", len(paths))
    return paths


if __name__ == "__main__":
    paths = run_eod_export()
    for p in paths:
        print(f"  Exported: {p}")
