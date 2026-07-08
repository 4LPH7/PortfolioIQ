"""
PortfolioIQ — Market Hours & Calendar Logic
Determines whether NSE is currently open for trading.
All time comparisons use IST (Asia/Kolkata).
"""
from __future__ import annotations

import datetime
from functools import lru_cache

import pytz
from loguru import logger

from src.config.settings import get_settings
from src.db.connection import execute_sql

IST = pytz.timezone("Asia/Kolkata")

# NSE regular trading session boundaries
MARKET_OPEN_H, MARKET_OPEN_M = 9, 15
MARKET_CLOSE_H, MARKET_CLOSE_M = 15, 30

# Pre-open session (order entry) — polling paused here
PRE_OPEN_H, PRE_OPEN_M = 9, 0


def now_ist() -> datetime.datetime:
    """Return the current time in IST."""
    return datetime.datetime.now(tz=IST)


def today_ist() -> datetime.date:
    """Return today's date in IST."""
    return now_ist().date()


@lru_cache(maxsize=32)
def _get_holidays_for_year(year: int) -> set[datetime.date]:
    """
    Fetch NSE holidays for a given year from the database.
    Cached per year — refreshes automatically on new year (different cache key).
    """
    rows = execute_sql(
        """
        SELECT holiday_date
        FROM market_calendar
        WHERE exchange = 'NSE'
          AND session_type = 'CLOSED'
          AND EXTRACT(YEAR FROM holiday_date) = :year
        """,
        {"year": year},
    )
    holidays = {row["holiday_date"] for row in rows}
    logger.debug("Loaded {} NSE holidays for year {}", len(holidays), year)
    return holidays


def is_holiday(date: datetime.date | None = None) -> bool:
    """
    Check if a given date is an NSE trading holiday.
    Defaults to today in IST.
    """
    if date is None:
        date = today_ist()
    holidays = _get_holidays_for_year(date.year)
    return date in holidays


def is_weekend(date: datetime.date | None = None) -> bool:
    """Check if a given date falls on a Saturday or Sunday."""
    if date is None:
        date = today_ist()
    return date.weekday() >= 5  # 5 = Saturday, 6 = Sunday


def is_market_day(date: datetime.date | None = None) -> bool:
    """
    Return True if the market is open on the given date.
    A market day is a weekday that is NOT an NSE holiday.
    """
    if date is None:
        date = today_ist()
    return not is_weekend(date) and not is_holiday(date)


def is_market_open(dt: datetime.datetime | None = None) -> bool:
    """
    Return True if NSE is currently in the normal trading session.
    Normal session: 09:15 IST to 15:30 IST on market days.

    Args:
        dt: Datetime to check (defaults to current IST time)

    Returns:
        True if within normal trading hours on a market day.
    """
    if dt is None:
        dt = now_ist()
    elif dt.tzinfo is None:
        # Treat naive datetime as IST
        dt = IST.localize(dt)
    else:
        dt = dt.astimezone(IST)

    # Must be a market day
    if not is_market_day(dt.date()):
        return False

    settings = get_settings()
    open_h, open_m = map(int, settings.market_open_time.split(":"))
    close_h, close_m = map(int, settings.market_close_time.split(":"))

    open_time  = dt.replace(hour=open_h,  minute=open_m,  second=0, microsecond=0)
    close_time = dt.replace(hour=close_h, minute=close_m, second=0, microsecond=0)

    return open_time <= dt <= close_time


def seconds_until_market_open() -> float:
    """
    Returns seconds until next market open.
    Returns 0.0 if market is currently open.
    """
    now = now_ist()

    if is_market_open(now):
        return 0.0

    settings = get_settings()
    open_h, open_m = map(int, settings.market_open_time.split(":"))

    # Try today first
    candidate = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)

    # If past today's open, move to next market day
    if candidate <= now or not is_market_day(now.date()):
        candidate_date = now.date() + datetime.timedelta(days=1)
        # Skip weekends and holidays
        while not is_market_day(candidate_date):
            candidate_date += datetime.timedelta(days=1)
        candidate = IST.localize(
            datetime.datetime(
                candidate_date.year, candidate_date.month, candidate_date.day,
                open_h, open_m, 0
            )
        )

    delta = (candidate - now).total_seconds()
    return max(0.0, delta)


def get_market_status() -> dict:
    """
    Return a detailed market status dictionary.
    Used by the Streamlit dashboard status indicator.
    """
    now = now_ist()
    today = now.date()
    open_status = is_market_open(now)
    settings = get_settings()
    open_h, open_m = map(int, settings.market_open_time.split(":"))
    close_h, close_m = map(int, settings.market_close_time.split(":"))

    if is_weekend(today):
        status_text = "CLOSED — Weekend"
    elif is_holiday(today):
        status_text = "CLOSED — NSE Holiday"
    elif now < now.replace(hour=PRE_OPEN_H, minute=PRE_OPEN_M, second=0, microsecond=0):
        status_text = "PRE-MARKET"
    elif now < now.replace(hour=open_h, minute=open_m, second=0, microsecond=0):
        status_text = "PRE-OPEN SESSION"
    elif open_status:
        status_text = "OPEN"
    else:
        status_text = "CLOSED — Post Market"

    return {
        "is_open": open_status,
        "is_market_day": is_market_day(today),
        "status_text": status_text,
        "current_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "market_open": f"{open_h:02d}:{open_m:02d} IST",
        "market_close": f"{close_h:02d}:{close_m:02d} IST",
        "seconds_until_open": seconds_until_market_open(),
    }
