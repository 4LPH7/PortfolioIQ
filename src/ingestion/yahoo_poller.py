"""
PortfolioIQ — Yahoo Finance Live Price Polling Daemon
Background daemon that polls Yahoo Finance every ~50 seconds
for live prices of all current holdings during market hours.

Design principles:
    - Exponential backoff on rate limit errors (HTTP 403/429/999)
    - Batch fetching for all holdings in a single yf.download() call
    - Respects market hours and NSE holidays (pauses automatically)
    - Writes atomically to live_prices (UPSERT with IS DISTINCT FROM filter)
    - Archives every snapshot to price_history for EOD analytics
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import pytz
import yfinance as yf
from loguru import logger
from sqlalchemy import text
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from src.db.connection import get_db_session, execute_sql
from src.ingestion.instrument_mapper import get_yf_tickers_for_holdings
from src.ingestion.market_hours import is_market_open, seconds_until_market_open, now_ist
from src.config.settings import get_settings

import logging as std_logging  # tenacity uses standard logging

IST = pytz.timezone("Asia/Kolkata")


class RateLimitError(Exception):
    """Raised when Yahoo Finance returns HTTP 403/429/999."""
    pass


@retry(
    retry=retry_if_exception_type(RateLimitError),
    wait=wait_exponential(multiplier=2, min=10, max=120),
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log(std_logging.getLogger("tenacity"), std_logging.WARNING),
)
def _fetch_prices_from_yahoo(yf_tickers: list[str]) -> dict[str, dict[str, Any]]:
    """
    Fetch current prices for multiple tickers in a single batch request.
    Uses yf.Tickers for multi-ticker info fetch.

    Returns:
        Dict mapping yf_ticker → price data dict
    """
    if not yf_tickers:
        return {}

    results: dict[str, dict[str, Any]] = {}

    # yf.download is more efficient than looping Ticker.info individually
    # but for live price + OHLC we use Tickers().tickers[sym].fast_info
    tickers_obj = yf.Tickers(" ".join(yf_tickers))

    for ticker_sym in yf_tickers:
        try:
            t = tickers_obj.tickers.get(ticker_sym)
            if t is None:
                logger.warning("Ticker {} not found in yfinance response", ticker_sym)
                continue

            # fast_info is faster than .info (no heavy metadata fetch)
            fi = t.fast_info
            last_price = getattr(fi, "last_price", None)

            if last_price is None or last_price <= 0:
                # Fall back to .info for currentPrice
                info = t.info
                last_price = info.get("currentPrice") or info.get("regularMarketPrice")

            if last_price is None:
                logger.warning("Could not get price for {}", ticker_sym)
                continue

            prev_close = getattr(fi, "previous_close", None) or getattr(fi, "regular_market_previous_close", None)
            open_price = getattr(fi, "open", None)
            high_price = getattr(fi, "day_high", None)
            low_price  = getattr(fi, "day_low", None)
            volume     = getattr(fi, "three_month_average_volume", None)  # fallback
            try:
                volume = int(getattr(fi, "last_volume", None) or 0)
            except (TypeError, ValueError):
                volume = None

            change_abs = (last_price - prev_close) if prev_close else None
            change_pct = (change_abs / prev_close * 100) if (prev_close and change_abs is not None) else None

            results[ticker_sym] = {
                "last_price":     round(float(last_price), 2),
                "open_price":     round(float(open_price), 2) if open_price else None,
                "high_price":     round(float(high_price), 2) if high_price else None,
                "low_price":      round(float(low_price), 2) if low_price else None,
                "close_price":    round(float(prev_close), 2) if prev_close else None,
                "volume":         volume,
                "change_absolute": round(float(change_abs), 2) if change_abs is not None else None,
                "change_percent":  round(float(change_pct), 4) if change_pct is not None else None,
            }

        except Exception as exc:
            err_str = str(exc).lower()
            if any(code in err_str for code in ["403", "429", "999", "rate limit", "too many"]):
                logger.warning("Rate limit hit fetching {}: {}", ticker_sym, exc)
                raise RateLimitError(f"Rate limited: {exc}") from exc
            logger.warning("Error fetching price for {}: {}", ticker_sym, exc)

    return results


def _upsert_live_prices(
    token_ticker_map: dict[int, str],
    price_data: dict[str, dict[str, Any]],
) -> int:
    """
    Upsert price data into live_prices and append to price_history.
    Uses IS DISTINCT FROM to skip unnecessary writes.

    Returns:
        Number of prices updated.
    """
    updated = 0
    now = datetime.now(tz=IST)

    with get_db_session() as session:
        for instrument_token, yf_ticker in token_ticker_map.items():
            prices = price_data.get(yf_ticker)
            if prices is None:
                # Mark as stale if we couldn't get price
                session.execute(
                    text("""
                        UPDATE live_prices
                        SET is_stale = TRUE
                        WHERE instrument_token = :token
                    """),
                    {"token": instrument_token},
                )
                continue

            # UPSERT live_prices — skip write if price unchanged (IS DISTINCT FROM)
            result = session.execute(
                text("""
                    INSERT INTO live_prices (
                        instrument_token,
                        last_price, open_price, high_price, low_price,
                        close_price, volume,
                        change_absolute, change_percent,
                        source, is_stale, last_updated
                    )
                    VALUES (
                        :token,
                        :last_price, :open_price, :high_price, :low_price,
                        :close_price, :volume,
                        :change_absolute, :change_percent,
                        'yahoo', FALSE, NOW()
                    )
                    ON CONFLICT (instrument_token) DO UPDATE SET
                        last_price      = EXCLUDED.last_price,
                        open_price      = EXCLUDED.open_price,
                        high_price      = EXCLUDED.high_price,
                        low_price       = EXCLUDED.low_price,
                        close_price     = EXCLUDED.close_price,
                        volume          = EXCLUDED.volume,
                        change_absolute = EXCLUDED.change_absolute,
                        change_percent  = EXCLUDED.change_percent,
                        is_stale        = FALSE,
                        last_updated    = NOW()
                    WHERE
                        live_prices.last_price IS DISTINCT FROM EXCLUDED.last_price
                     OR live_prices.volume     IS DISTINCT FROM EXCLUDED.volume
                """),
                {"token": instrument_token, **prices},
            )

            # Archive to price_history (always append)
            session.execute(
                text("""
                    INSERT INTO price_history (
                        instrument_token,
                        last_price, open_price, high_price, low_price,
                        close_price, volume, change_percent,
                        source, recorded_at
                    )
                    VALUES (
                        :token,
                        :last_price, :open_price, :high_price, :low_price,
                        :close_price, :volume, :change_percent,
                        'yahoo', NOW()
                    )
                """),
                {"token": instrument_token, **prices},
            )
            updated += 1

    return updated


def poll_once() -> dict[str, Any]:
    """
    Execute one polling cycle:
    1. Get active holdings with YF tickers
    2. Fetch prices from Yahoo Finance (with retry)
    3. Upsert into live_prices + archive to price_history

    Returns:
        Dict with polling summary metrics
    """
    token_ticker_map = get_yf_tickers_for_holdings()

    if not token_ticker_map:
        logger.info("No holdings to poll.")
        return {"status": "no_holdings", "updated": 0}

    tickers = list(token_ticker_map.values())
    logger.debug("Polling {} tickers: {}", len(tickers), tickers)

    try:
        price_data = _fetch_prices_from_yahoo(tickers)
        updated = _upsert_live_prices(token_ticker_map, price_data)
        logger.info(
            "Poll complete — {}/{} prices updated at {}",
            updated, len(tickers),
            now_ist().strftime("%H:%M:%S IST")
        )
        return {"status": "ok", "updated": updated, "total": len(tickers)}

    except RateLimitError as exc:
        logger.error("Rate limit exceeded after retries: {}", exc)
        return {"status": "rate_limited", "error": str(exc)}

    except Exception as exc:
        logger.exception("Unexpected poll error: {}", exc)
        return {"status": "error", "error": str(exc)}


def run_polling_daemon() -> None:
    """
    Main polling loop. Runs continuously:
    - During market hours: polls every POLLING_INTERVAL_SEC seconds
    - Outside market hours: sleeps until next market open
    - On weekends/holidays: sleeps until Monday 9:15 AM

    This function blocks forever. Run in a background thread or process.
    """
    settings = get_settings()

    logger.info("=" * 50)
    logger.info("YAHOO FINANCE POLLING DAEMON STARTED")
    logger.info("Interval: {}s | Dry-run: {}", settings.polling_interval_sec, settings.is_dry_run)
    logger.info("=" * 50)

    while True:
        try:
            if is_market_open():
                poll_once()
                time.sleep(settings.polling_interval_sec)
            else:
                wait_sec = seconds_until_market_open()
                if wait_sec > 3600:
                    logger.info(
                        "Market closed. Next open in {:.1f} hours. Sleeping...",
                        wait_sec / 3600
                    )
                    # Sleep in chunks so we can check for shutdown signals
                    time.sleep(min(wait_sec, 1800))  # Max 30-min sleep chunks
                else:
                    logger.info(
                        "Market opens in {:.0f} minutes. Waiting...",
                        wait_sec / 60
                    )
                    time.sleep(max(wait_sec - 30, 10))  # Wake up 30s before open

        except KeyboardInterrupt:
            logger.info("Polling daemon stopped by user.")
            break
        except Exception as exc:
            logger.exception("Unhandled error in polling daemon: {}", exc)
            time.sleep(60)  # Cool-down before retry


if __name__ == "__main__":
    """
    Run the polling daemon directly:
        python -m src.ingestion.yahoo_poller
    """
    run_polling_daemon()
