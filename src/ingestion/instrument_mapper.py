"""
PortfolioIQ — Instrument Mapper
Maps Zerodha trading symbols to Yahoo Finance tickers (.NS suffix).
Handles edge cases: M&M → M&M.NS, BAJAJ-AUTO → BAJAJ-AUTO.NS, etc.

Sources:
    1. instrument_master.csv (seed file checked in to data/)
    2. Kite instruments API (refreshed daily)
    3. Manual overrides in the CSV for problematic symbols
"""

from __future__ import annotations

import csv
from pathlib import Path

from loguru import logger
from sqlalchemy import text

from src.db.connection import get_db_session
from src.ingestion.kite_auth import get_authenticated_kite
from src.ingestion.ticker_map import get_yf_ticker

# Path to the seed CSV mapping file
MAPPING_CSV = Path(__file__).parent.parent.parent / "data" / "instrument_master.csv"


def load_mapping_from_csv() -> dict[str, dict]:
    """
    Load the instrument_master.csv mapping file.
    Returns a dict keyed by tradingsymbol with YF ticker + sector info.
    """
    if not MAPPING_CSV.exists():
        logger.warning("instrument_master.csv not found at {}. Skipping CSV load.", MAPPING_CSV)
        return {}

    mapping = {}
    with open(MAPPING_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = row.get("tradingsymbol", "").strip()
            if symbol:
                mapping[symbol] = {
                    "yf_ticker": row.get("yf_ticker", "").strip(),
                    "sector": row.get("sector", "").strip() or None,
                    "industry": row.get("industry", "").strip() or None,
                    "market_cap_category": row.get("market_cap_category", "").strip() or None,
                }

    logger.info("Loaded {} instrument mappings from CSV", len(mapping))
    return mapping


def sync_instrument_master_from_kite() -> int:
    """
    Pull the NSE instruments list from Kite API and upsert into instrument_master.
    This runs ~8:30 AM IST daily to refresh tokens before market open.

    Returns:
        Number of instruments upserted.
    """
    logger.info("Syncing instrument master from Kite API...")
    kite = get_authenticated_kite()

    try:
        instruments: list[dict] = kite.instruments("NSE")
    except Exception as exc:
        logger.error("Failed to fetch instruments from Kite: {}", exc)
        raise

    # Load CSV overrides for sector/industry/YF ticker
    csv_mapping = load_mapping_from_csv()

    upserted = 0
    with get_db_session() as session:
        for inst in instruments:
            # Only sync equity instruments
            if inst.get("instrument_type") != "EQ":
                continue

            symbol = inst.get("tradingsymbol", "")
            csv_data = csv_mapping.get(symbol, {})

            # Use CSV override for YF ticker, fall back to auto-generated
            yf_ticker = csv_data.get("yf_ticker") or get_yf_ticker(
                symbol, inst.get("exchange", "NSE")
            )

            session.execute(
                text("""
                    INSERT INTO instrument_master (
                        instrument_token,
                        exchange_token,
                        tradingsymbol,
                        name,
                        isin,
                        yf_ticker,
                        exchange,
                        instrument_type,
                        segment,
                        tick_size,
                        lot_size,
                        sector,
                        industry,
                        market_cap_category,
                        is_active,
                        last_synced_at
                    )
                    VALUES (
                        :instrument_token,
                        :exchange_token,
                        :tradingsymbol,
                        :name,
                        :isin,
                        :yf_ticker,
                        :exchange,
                        :instrument_type,
                        :segment,
                        :tick_size,
                        :lot_size,
                        :sector,
                        :industry,
                        :market_cap_category,
                        TRUE,
                        NOW()
                    )
                    ON CONFLICT (instrument_token) DO UPDATE SET
                        exchange_token      = EXCLUDED.exchange_token,
                        tradingsymbol       = EXCLUDED.tradingsymbol,
                        name                = EXCLUDED.name,
                        isin                = EXCLUDED.isin,
                        yf_ticker           = COALESCE(EXCLUDED.yf_ticker, instrument_master.yf_ticker),
                        tick_size           = EXCLUDED.tick_size,
                        lot_size            = EXCLUDED.lot_size,
                        sector              = COALESCE(EXCLUDED.sector, instrument_master.sector),
                        industry            = COALESCE(EXCLUDED.industry, instrument_master.industry),
                        market_cap_category = COALESCE(EXCLUDED.market_cap_category, instrument_master.market_cap_category),
                        is_active           = TRUE,
                        last_synced_at      = NOW()
                """),
                {
                    "instrument_token": inst.get("instrument_token"),
                    "exchange_token": inst.get("exchange_token"),
                    "tradingsymbol": symbol,
                    "name": inst.get("name", ""),
                    "isin": None,  # Not in Kite instruments list
                    "yf_ticker": yf_ticker,
                    "exchange": inst.get("exchange", "NSE"),
                    "instrument_type": inst.get("instrument_type", "EQ"),
                    "segment": inst.get("segment", "NSE"),
                    "tick_size": float(inst.get("tick_size", 0.05)),
                    "lot_size": int(inst.get("lot_size", 1)),
                    "sector": csv_data.get("sector"),
                    "industry": csv_data.get("industry"),
                    "market_cap_category": csv_data.get("market_cap_category"),
                },
            )
            upserted += 1

    logger.success("Instrument master sync complete. {} NSE equity instruments upserted.", upserted)
    return upserted


if __name__ == "__main__":
    """
    Sync instrument master from Kite API.
    Run: python -m src.ingestion.instrument_mapper
    """
    count = sync_instrument_master_from_kite()
    print(f"Synced {count} instruments.")
