"""
PortfolioIQ — Kite Connect Quote Poller
Daemon process that polls Kite Connect for live prices and updates the database.
"""

from __future__ import annotations

import time
from typing import Any

from kiteconnect.exceptions import GeneralException, NetworkException, TokenException
from loguru import logger

from src.config.settings import get_settings
from src.db.connection import execute_sql
from src.ingestion.kite_auth import get_authenticated_kite, invalidate_token
from src.ingestion.market_hours import is_market_open, seconds_until_market_open


def get_active_holding_symbols() -> dict[str, int]:
    """
    Query user_holdings for instruments with (quantity + t1_quantity) > 0.
    Returns mapping { "exchange:tradingsymbol": instrument_token }
    """
    rows = execute_sql(
        """
        SELECT exchange, tradingsymbol, instrument_token
        FROM user_holdings
        WHERE (quantity + t1_quantity) > 0
        """
    )
    return {f"{row['exchange']}:{row['tradingsymbol']}": row["instrument_token"] for row in rows}


def poll_kite_quotes() -> dict[str, Any]:
    """
    Polls Kite Connect for active holdings and updates live_prices and price_history.
    """
    active_holdings = get_active_holding_symbols()
    if not active_holdings:
        logger.debug("No active holdings found. Skipping quote polling.")
        return {"status": "no_holdings", "updated": 0}

    symbols = list(active_holdings.keys())

    try:
        kite = get_authenticated_kite()
        if not kite:
            logger.error("Failed to get authenticated Kite instance for polling.")
            return {"status": "auth_error", "updated": 0}

        quotes = kite.quote(symbols)
    except TokenException as e:
        logger.error(f"Kite token expired during polling: {e}")
        invalidate_token()
        return {"status": "token_expired"}
    except (NetworkException, GeneralException) as e:
        logger.warning(f"Kite API error during polling: {e}")
        return {"status": "network_error"}

    updated_count = 0
    missing_symbols = []

    for symbol_key, instrument_token in active_holdings.items():
        if symbol_key in quotes:
            q = quotes[symbol_key]
            execute_sql(
                """
                INSERT INTO live_prices (
                    instrument_token, tradingsymbol, last_price, open_price, high_price,
                    low_price, close_price, volume, change_absolute, change_percent,
                    source, is_stale, last_updated
                ) VALUES (
                    :token, :symbol, :last_price, :open, :high, :low, :close, :volume,
                    :change_abs, :change_pct, 'kite', FALSE, NOW()
                )
                ON CONFLICT (instrument_token) DO UPDATE SET
                    last_price = EXCLUDED.last_price,
                    open_price = EXCLUDED.open_price,
                    high_price = EXCLUDED.high_price,
                    low_price = EXCLUDED.low_price,
                    close_price = EXCLUDED.close_price,
                    volume = EXCLUDED.volume,
                    change_absolute = EXCLUDED.change_absolute,
                    change_percent = EXCLUDED.change_percent,
                    source = EXCLUDED.source,
                    is_stale = FALSE,
                    last_updated = EXCLUDED.last_updated
                WHERE live_prices.last_price IS DISTINCT FROM EXCLUDED.last_price
                   OR live_prices.volume IS DISTINCT FROM EXCLUDED.volume
                """,
                {
                    "token": instrument_token,
                    "symbol": symbol_key.split(":")[1],
                    "last_price": q.get("last_price", 0),
                    "open": q.get("ohlc", {}).get("open", 0),
                    "high": q.get("ohlc", {}).get("high", 0),
                    "low": q.get("ohlc", {}).get("low", 0),
                    "close": q.get("ohlc", {}).get("close", 0),
                    "volume": q.get("volume", 0),
                    "change_abs": q.get("last_price", 0) - q.get("ohlc", {}).get("close", 0)
                    if q.get("ohlc", {}).get("close")
                    else 0,
                    "change_pct": q.get("net_change", 0) if "net_change" in q else 0,
                },
            )

            # Record in price_history
            execute_sql(
                """
                INSERT INTO price_history (
                    instrument_token, tradingsymbol, close_price, open_price,
                    high_price, low_price, volume, change_percent, source, recorded_at
                ) VALUES (
                    :token, :symbol, :close, :open, :high, :low, :volume, :change_pct, 'kite', NOW()
                )
                """,
                {
                    "token": instrument_token,
                    "symbol": symbol_key.split(":")[1],
                    "close": q.get("last_price", 0),
                    "open": q.get("ohlc", {}).get("open", 0),
                    "high": q.get("ohlc", {}).get("high", 0),
                    "low": q.get("ohlc", {}).get("low", 0),
                    "volume": q.get("volume", 0),
                    "change_pct": q.get("net_change", 0) if "net_change" in q else 0,
                },
            )
            updated_count += 1
        else:
            missing_symbols.append(instrument_token)

    if missing_symbols:
        # Mark missing quotes as stale
        for token in missing_symbols:
            execute_sql(
                """
                UPDATE live_prices
                SET is_stale = TRUE
                WHERE instrument_token = :token
                """,
                {"token": token},
            )

    return {"status": "ok", "updated": updated_count, "total": len(symbols)}


def run_kite_polling_daemon():
    """
    Main loop for polling Kite Connect quotes during market hours.
    """
    settings = get_settings()
    interval = settings.polling_interval_sec
    logger.info(f"Starting Kite Quote Polling Daemon (interval={interval}s)")

    while True:
        if is_market_open():
            res = poll_kite_quotes()
            logger.debug(f"Poll result: {res}")
            time.sleep(interval)
        else:
            wait_sec = seconds_until_market_open()
            logger.info(f"Market closed. Waiting {wait_sec:.0f} seconds until next open.")
            time.sleep(
                min(wait_sec, 60)
            )  # Check at least every minute to gracefully handle config changes
