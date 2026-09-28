"""
PortfolioIQ — Market Hours & Calendar Logic
Determines whether NSE is currently open for trading.
All time comparisons use IST (Asia/Kolkata).
"""

from __future__ import annotations

import datetime
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

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

FALLBACK_JSON_PATH = Path(__file__).parent.parent / "config" / "nse_holidays.json"


def now_ist() -> datetime.datetime:
    """Return the current time in IST."""
    return datetime.datetime.now(tz=IST)


def today_ist() -> datetime.date:
    """Return today's date in IST."""
    return now_ist().date()


def _load_fallback_calendar() -> list[dict[str, Any]]:
    try:
        if FALLBACK_JSON_PATH.exists():
            with open(FALLBACK_JSON_PATH, encoding="utf-8") as f:
                data = json.load(f)
                return data.get("holidays", [])
    except Exception as e:
        logger.error(f"Failed to load fallback calendar: {e}")
    return []


@lru_cache(maxsize=32)
def _get_calendar_records(year: int) -> dict[datetime.date, dict[str, Any]]:
    """
    Fetch NSE holidays and special sessions for a given year.
    Tries DB first, falls back to nse_holidays.json on failure.
    """
    records = {}
    try:
        rows = execute_sql(
            """
            SELECT holiday_date, is_trading_holiday, special_session_open, special_session_close, description
            FROM market_calendar
            WHERE exchange = 'NSE'
              AND EXTRACT(YEAR FROM holiday_date) = :year
            """,
            {"year": year},
        )
        for row in rows:
            records[row["holiday_date"]] = dict(row)
        logger.debug("Loaded {} calendar records for {} from DB", len(records), year)
        return records
    except Exception as e:
        logger.warning(f"DB unavailable for calendar ({e}). Falling back to JSON.")
        fallback = _load_fallback_calendar()
        for item in fallback:
            dt = datetime.datetime.strptime(item["holiday_date"], "%Y-%m-%d").date()
            if dt.year == year:
                open_time = (
                    datetime.datetime.strptime(item["special_session_open"], "%H:%M:%S").time()
                    if item.get("special_session_open")
                    else None
                )
                close_time = (
                    datetime.datetime.strptime(item["special_session_close"], "%H:%M:%S").time()
                    if item.get("special_session_close")
                    else None
                )
                records[dt] = {
                    "holiday_date": dt,
                    "is_trading_holiday": item.get("is_trading_holiday", True),
                    "special_session_open": open_time,
                    "special_session_close": close_time,
                    "description": item.get("description"),
                }
        return records


def get_special_session_hours(
    date: datetime.date | None = None,
) -> tuple[datetime.time, datetime.time] | None:
    if date is None:
        date = today_ist()
    records = _get_calendar_records(date.year)
    record = records.get(date)
    if record and record.get("special_session_open") and record.get("special_session_close"):
        return record["special_session_open"], record["special_session_close"]
    return None


def is_holiday(date: datetime.date | None = None) -> bool:
    if date is None:
        date = today_ist()
    records = _get_calendar_records(date.year)
    record = records.get(date)
    if record and record.get("is_trading_holiday"):
        return True
    return False


def is_weekend(date: datetime.date | None = None) -> bool:
    if date is None:
        date = today_ist()
    return date.weekday() >= 5  # 5 = Saturday, 6 = Sunday


def is_market_day(date: datetime.date | None = None) -> bool:
    if date is None:
        date = today_ist()

    # If there's a special session on this date, it's definitely a market day
    if get_special_session_hours(date) is not None:
        return True

    return not is_weekend(date) and not is_holiday(date)


def is_market_open(dt: datetime.datetime | None = None) -> bool:
    if dt is None:
        dt = now_ist()
    elif dt.tzinfo is None:
        dt = IST.localize(dt)
    else:
        dt = dt.astimezone(IST)

    special_hours = get_special_session_hours(dt.date())
    if special_hours:
        open_time = dt.replace(
            hour=special_hours[0].hour,
            minute=special_hours[0].minute,
            second=special_hours[0].second,
            microsecond=0,
        )
        close_time = dt.replace(
            hour=special_hours[1].hour,
            minute=special_hours[1].minute,
            second=special_hours[1].second,
            microsecond=0,
        )
        return open_time <= dt <= close_time

    if is_weekend(dt.date()) or is_holiday(dt.date()):
        return False

    settings = get_settings()
    open_h, open_m = map(int, settings.market_open_time.split(":"))
    close_h, close_m = map(int, settings.market_close_time.split(":"))

    open_time = dt.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
    close_time = dt.replace(hour=close_h, minute=close_m, second=0, microsecond=0)

    return open_time <= dt <= close_time


def seconds_until_market_open(now: datetime.datetime | None = None) -> float:
    if now is None:
        now = now_ist()
    elif now.tzinfo is None:
        now = IST.localize(now)
    else:
        now = now.astimezone(IST)

    if is_market_open(now):
        return 0.0

    settings = get_settings()
    open_h, open_m = map(int, settings.market_open_time.split(":"))

    def get_open_time_for_date(date_to_check: datetime.date) -> datetime.datetime:
        special_hours = get_special_session_hours(date_to_check)
        if special_hours:
            return IST.localize(datetime.datetime.combine(date_to_check, special_hours[0]))
        return IST.localize(
            datetime.datetime(
                date_to_check.year, date_to_check.month, date_to_check.day, open_h, open_m, 0
            )
        )

    candidate = get_open_time_for_date(now.date())

    if candidate <= now or not is_market_day(now.date()):
        candidate_date = now.date() + datetime.timedelta(days=1)
        while not is_market_day(candidate_date):
            candidate_date += datetime.timedelta(days=1)
        candidate = get_open_time_for_date(candidate_date)

    delta = (candidate - now).total_seconds()
    return max(0.0, delta)


def get_market_status() -> dict:
    now = now_ist()
    today = now.date()
    open_status = is_market_open(now)
    settings = get_settings()
    open_h, open_m = map(int, settings.market_open_time.split(":"))
    close_h, close_m = map(int, settings.market_close_time.split(":"))

    special_hours = get_special_session_hours(today)

    if special_hours:
        open_h, open_m = special_hours[0].hour, special_hours[0].minute
        close_h, close_m = special_hours[1].hour, special_hours[1].minute

        pre_open = now.replace(
            hour=open_h, minute=open_m, second=0, microsecond=0
        ) - datetime.timedelta(minutes=15)
        open_time = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)

        if now < pre_open:
            status_text = "PRE-MARKET"
        elif now < open_time:
            status_text = "PRE-OPEN SESSION"
        elif open_status:
            status_text = "SPECIAL SESSION — Muhurat Trading"
        else:
            status_text = "CLOSED — Post Market"
    else:
        if is_weekend(today):
            status_text = "CLOSED — Weekend"
        elif is_holiday(today):
            status_text = "CLOSED — NSE Holiday"
        else:
            pre_open = now.replace(hour=PRE_OPEN_H, minute=PRE_OPEN_M, second=0, microsecond=0)
            open_time = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
            if now < pre_open:
                status_text = "PRE-MARKET"
            elif now < open_time:
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
        "seconds_until_open": seconds_until_market_open(now),
    }
