"""
PortfolioIQ — Robust Multi-Source Quote Poller
Daemon process that polls live prices from Zerodha Kite and Yahoo Finance fallbacks,
ensuring real-time quotes update seamlessly without permission errors or rate limiting.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from kiteconnect.exceptions import GeneralException, NetworkException, PermissionException, TokenException
from loguru import logger

from src.config.settings import get_settings
from src.db.connection import execute_sql
from src.ingestion.kite_auth import get_authenticated_kite, invalidate_token
from src.ingestion.market_hours import is_market_open, seconds_until_market_open
from src.ingestion.ticker_map import get_yf_ticker


def get_active_holdings() -> list[dict[str, Any]]:
    """
    Query user_holdings for instruments with active balance.
    Returns list of holding dictionaries.
    """
    return execute_sql(
        """
        SELECT instrument_token, tradingsymbol, exchange, quantity, t1_quantity,
               average_price, COALESCE(data_source, 'KITE') AS data_source
        FROM user_holdings
        WHERE (quantity + t1_quantity) > 0
        """
    )


def fetch_quote_from_yahoo(tradingsymbol: str, exchange: str = "NSE") -> dict[str, Any] | None:
    """Fetch live/latest quote from Yahoo Finance using direct chart API."""
    from src.analytics.historical_cache import _fetch_from_yfinance

    yf_ticker = get_yf_ticker(tradingsymbol, exchange)
    try:
        df = _fetch_from_yfinance(yf_ticker, period="5d")
        if df is not None and not df.empty:
            last_row = df.iloc[-1]
            prev_close = float(df.iloc[-2]["Close"]) if len(df) >= 2 else float(last_row["Open"])
            last_p = float(last_row["Close"])
            open_p = float(last_row["Open"])
            high_p = float(last_row["High"])
            low_p = float(last_row["Low"])
            volume = int(last_row.get("Volume", 0))
            chg_abs = round(last_p - prev_close, 2)
            chg_pct = round((chg_abs / prev_close) * 100, 4) if prev_close > 0 else 0.0

            return {
                "last_price": last_p,
                "open_price": open_p,
                "high_price": high_p,
                "low_price": low_p,
                "close_price": prev_close,
                "volume": volume,
                "change_absolute": chg_abs,
                "change_percent": chg_pct,
                "source": "yahoo",
            }
    except Exception as exc:
        logger.debug("Failed Yahoo quote fetch for {}: {}", yf_ticker, exc)

    return None


def poll_kite_quotes() -> dict[str, Any]:
    """
    Polls real-time quotes for all active holdings.
    Gracefully uses Kite quote, Kite holdings, and Yahoo Finance fallbacks.
    """
    holdings = get_active_holdings()
    if not holdings:
        logger.debug("No active holdings found. Skipping quote polling.")
        return {"status": "no_holdings", "updated": 0}

    quotes_by_token: dict[int, dict[str, Any]] = {}
    kite = None

    try:
        kite = get_authenticated_kite()
    except Exception as exc:
        logger.debug("Kite auth unavailable for polling: {}", exc)

    # 1. Try Kite Connect .quote() or .holdings()
    if kite:
        symbol_map = {f"{h['exchange']}:{h['tradingsymbol']}": h["instrument_token"] for h in holdings}
        symbols = list(symbol_map.keys())

        # Attempt A: Standard quote API
        try:
            raw_quotes = kite.quote(symbols)
            for sym_key, q in raw_quotes.items():
                tok = symbol_map.get(sym_key)
                if tok:
                    ltp = float(q.get("last_price", 0))
                    ohlc = q.get("ohlc", {})
                    close_p = float(ohlc.get("close", ltp))
                    quotes_by_token[tok] = {
                        "last_price": ltp,
                        "open_price": float(ohlc.get("open", ltp)),
                        "high_price": float(ohlc.get("high", ltp)),
                        "low_price": float(ohlc.get("low", ltp)),
                        "close_price": close_p,
                        "volume": int(q.get("volume", 0)),
                        "change_absolute": round(ltp - close_p, 2),
                        "change_percent": float(q.get("net_change", 0)),
                        "source": "kite",
                    }
        except PermissionException:
            logger.info("Kite live quote API lacks permission; falling back to Kite holdings API and Yahoo quotes.")
        except TokenException as exc:
            logger.warning("Kite token expired during polling: {}", exc)
            invalidate_token()
        except (NetworkException, GeneralException) as exc:
            logger.warning("Kite API error during quote polling: {}", exc)

        # Attempt B: Use Kite .holdings() for broker-verified real-time prices
        if not quotes_by_token:
            try:
                raw_holdings = kite.holdings()
                for rh in raw_holdings:
                    tok = rh.get("instrument_token")
                    if tok:
                        ltp = float(rh.get("last_price", 0))
                        close_p = float(rh.get("close_price", ltp))
                        quotes_by_token[tok] = {
                            "last_price": ltp,
                            "open_price": ltp,
                            "high_price": ltp,
                            "low_price": ltp,
                            "close_price": close_p,
                            "volume": 0,
                            "change_absolute": float(rh.get("day_change", 0)),
                            "change_percent": float(rh.get("day_change_percentage", 0)),
                            "source": "kite",
                        }
            except Exception as exc:
                logger.debug("Kite holdings fallback failed: {}", exc)

    # 2. Fill missing holdings via fast Yahoo Finance quote engine
    for h in holdings:
        tok = h["instrument_token"]
        if tok not in quotes_by_token or quotes_by_token[tok].get("last_price", 0) <= 0:
            yq = fetch_quote_from_yahoo(h["tradingsymbol"], h.get("exchange", "NSE"))
            if yq:
                quotes_by_token[tok] = yq

    # 3. Write into database
    updated_count = 0
    for h in holdings:
        tok = h["instrument_token"]
        q = quotes_by_token.get(tok)
        if q and q.get("last_price", 0) > 0:
            last_p = q["last_price"]
            open_p = q.get("open_price")
            high_p = q.get("high_price")
            low_p = q.get("low_price")
            close_p = q.get("close_price")
            volume = q.get("volume", 0)
            chg_abs = q.get("change_absolute", 0.0)
            chg_pct = q.get("change_percent", 0.0)
            source = q.get("source", "kite")

            # Update live_prices
            execute_sql(
                """
                INSERT INTO live_prices (
                    instrument_token, last_price, open_price, high_price,
                    low_price, close_price, volume, change_absolute,
                    change_percent, source, is_stale, last_updated
                ) VALUES (
                    :token, :last_price, :open_price, :high_price,
                    :low_price, :close_price, :volume, :chg_abs,
                    :chg_pct, :source, FALSE, NOW()
                )
                ON CONFLICT (instrument_token) DO UPDATE SET
                    last_price      = EXCLUDED.last_price,
                    open_price      = COALESCE(EXCLUDED.open_price, live_prices.open_price),
                    high_price      = COALESCE(EXCLUDED.high_price, live_prices.high_price),
                    low_price       = COALESCE(EXCLUDED.low_price, live_prices.low_price),
                    close_price     = COALESCE(EXCLUDED.close_price, live_prices.close_price),
                    volume          = COALESCE(EXCLUDED.volume, live_prices.volume),
                    change_absolute = EXCLUDED.change_absolute,
                    change_percent  = EXCLUDED.change_percent,
                    source          = EXCLUDED.source,
                    is_stale        = FALSE,
                    last_updated    = NOW()
                """,
                {
                    "token": tok,
                    "last_price": last_p,
                    "open_price": open_p,
                    "high_price": high_p,
                    "low_price": low_p,
                    "close_price": close_p,
                    "volume": volume,
                    "chg_abs": chg_abs,
                    "chg_pct": chg_pct,
                    "source": source,
                },
            )

            # Record price_history
            try:
                execute_sql(
                    """
                    INSERT INTO price_history (
                        instrument_token, last_price, open_price, high_price,
                        low_price, close_price, volume, change_percent, source, recorded_at
                    ) VALUES (
                        :token, :last_price, :open_price, :high_price,
                        :low_price, :close_price, :volume, :chg_pct, :source, NOW()
                    )
                    """,
                    {
                        "token": tok,
                        "last_price": last_p,
                        "open_price": open_p,
                        "high_price": high_p,
                        "low_price": low_p,
                        "close_price": close_p,
                        "volume": volume,
                        "chg_pct": chg_pct,
                        "source": source,
                    },
                )
            except Exception as exc:
                logger.debug("price_history insert skipped: {}", exc)

            # Update user_holdings current state
            total_qty = h["quantity"] + h.get("t1_quantity", 0)
            avg_p = float(h.get("average_price", 0))
            pnl = round((last_p - avg_p) * total_qty, 2)
            execute_sql(
                """
                UPDATE user_holdings
                SET last_price = :last_price,
                    close_price = COALESCE(:close_price, close_price),
                    day_change = :chg_abs,
                    day_change_pct = :chg_pct,
                    pnl = :pnl,
                    updated_at = NOW()
                WHERE instrument_token = :token AND (quantity + t1_quantity) > 0
                """,
                {
                    "token": tok,
                    "last_price": last_p,
                    "close_price": close_p,
                    "chg_abs": chg_abs,
                    "chg_pct": chg_pct,
                    "pnl": pnl,
                },
            )

            updated_count += 1
        else:
            # Mark missing quote as stale
            execute_sql(
                "UPDATE live_prices SET is_stale = TRUE WHERE instrument_token = :token",
                {"token": tok},
            )

    return {"status": "ok", "updated": updated_count, "total": len(holdings)}


def run_kite_polling_daemon():
    """Main loop for polling quotes during market hours."""
    settings = get_settings()
    interval = settings.polling_interval_sec
    logger.info("Starting Multi-Source Quote Polling Daemon (interval={}s)", interval)

    while True:
        if is_market_open():
            res = poll_kite_quotes()
            logger.debug("Poll result: {}", res)
            time.sleep(interval)
        else:
            wait_sec = seconds_until_market_open()
            logger.info("Market closed. Waiting {:.0f} seconds until next open.", wait_sec)
            time.sleep(min(wait_sec, 60))
