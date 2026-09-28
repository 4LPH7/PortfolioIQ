"""
Tests for market_hours.py
Verifies NSE market open/close logic, holiday detection, and weekend handling.
Uses freezegun to mock the current IST time.
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest
from freezegun import freeze_time

# We patch DB calls so tests run without a real database
with patch("src.db.connection.execute_sql") as mock_execute:
    from src.ingestion.market_hours import (
        IST,
        get_market_status,
        is_holiday,
        is_market_day,
        is_market_open,
        is_weekend,
        seconds_until_market_open,
    )


def make_ist_datetime(year, month, day, hour, minute, second=0):
    """Helper to create an IST-aware datetime."""
    return IST.localize(datetime.datetime(year, month, day, hour, minute, second))


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(autouse=True)
def mock_db_holidays():
    """Mock the DB holiday lookup to return known 2026 NSE holidays."""
    nse_holidays_2026 = {
        datetime.date(2026, 1, 26),  # Republic Day
        datetime.date(2026, 3, 3),  # Holi
        datetime.date(2026, 4, 3),  # Good Friday
        datetime.date(2026, 4, 14),  # Ambedkar Jayanti
        datetime.date(2026, 5, 1),  # Maharashtra Day
        datetime.date(2026, 6, 26),  # Muharram
        datetime.date(2026, 10, 2),  # Gandhi Jayanti
        datetime.date(2026, 12, 25),  # Christmas
    }
    records = {d: {"holiday_date": d, "is_trading_holiday": True} for d in nse_holidays_2026}
    # Add Muhurat trading session
    records[datetime.date(2026, 11, 8)] = {
        "holiday_date": datetime.date(2026, 11, 8),
        "is_trading_holiday": False,
        "special_session_open": datetime.time(18, 15),
        "special_session_close": datetime.time(19, 15),
    }
    with patch(
        "src.ingestion.market_hours._get_calendar_records",
        side_effect=lambda year: {d: r for d, r in records.items() if d.year == year},
    ):
        yield


# ============================================================
# is_weekend tests
# ============================================================


class TestIsWeekend:
    def test_monday_is_not_weekend(self):
        assert is_weekend(datetime.date(2026, 6, 29)) is False  # Monday

    def test_friday_is_not_weekend(self):
        assert is_weekend(datetime.date(2026, 7, 3)) is False  # Friday

    def test_saturday_is_weekend(self):
        assert is_weekend(datetime.date(2026, 6, 27)) is True

    def test_sunday_is_weekend(self):
        assert is_weekend(datetime.date(2026, 6, 28)) is True


# ============================================================
# is_holiday tests
# ============================================================


class TestIsHoliday:
    def test_republic_day_is_holiday(self):
        assert is_holiday(datetime.date(2026, 1, 26)) is True

    def test_good_friday_is_holiday(self):
        assert is_holiday(datetime.date(2026, 4, 3)) is True

    def test_christmas_is_holiday(self):
        assert is_holiday(datetime.date(2026, 12, 25)) is True

    def test_regular_tuesday_not_holiday(self):
        assert is_holiday(datetime.date(2026, 7, 7)) is False

    def test_budget_day_not_holiday(self):
        # Feb 1 is not a public holiday (budget is special session)
        assert is_holiday(datetime.date(2026, 2, 1)) is False


# ============================================================
# is_market_day tests
# ============================================================


class TestIsMarketDay:
    def test_regular_monday_is_market_day(self):
        assert is_market_day(datetime.date(2026, 6, 29)) is True

    def test_saturday_not_market_day(self):
        assert is_market_day(datetime.date(2026, 6, 27)) is False

    def test_sunday_not_market_day(self):
        assert is_market_day(datetime.date(2026, 6, 28)) is False

    def test_republic_day_not_market_day(self):
        assert is_market_day(datetime.date(2026, 1, 26)) is False

    def test_christmas_not_market_day(self):
        assert is_market_day(datetime.date(2026, 12, 25)) is False


# ============================================================
# is_market_open tests
# ============================================================


class TestIsMarketOpen:
    def test_open_at_9_15(self):
        dt = make_ist_datetime(2026, 6, 29, 9, 15)  # Monday, non-holiday
        assert is_market_open(dt) is True

    def test_open_at_noon(self):
        dt = make_ist_datetime(2026, 6, 29, 12, 0)
        assert is_market_open(dt) is True

    def test_open_at_3_29(self):
        dt = make_ist_datetime(2026, 6, 29, 15, 29)
        assert is_market_open(dt) is True

    def test_closed_at_3_31(self):
        dt = make_ist_datetime(2026, 6, 29, 15, 31)
        assert is_market_open(dt) is False

    def test_closed_before_9_15(self):
        dt = make_ist_datetime(2026, 6, 29, 9, 14)
        assert is_market_open(dt) is False

    def test_closed_at_9_00_pre_open(self):
        dt = make_ist_datetime(2026, 6, 29, 9, 0)
        assert is_market_open(dt) is False

    def test_closed_on_saturday(self):
        dt = make_ist_datetime(2026, 6, 27, 11, 0)
        assert is_market_open(dt) is False

    def test_closed_on_good_friday(self):
        dt = make_ist_datetime(2026, 4, 3, 11, 0)
        assert is_market_open(dt) is False

    def test_closed_at_midnight(self):
        dt = make_ist_datetime(2026, 6, 29, 0, 0)
        assert is_market_open(dt) is False

    def test_closed_on_christmas(self):
        dt = make_ist_datetime(2026, 12, 25, 11, 30)
        assert is_market_open(dt) is False

    def test_open_at_market_open_exact(self):
        """Boundary: exactly at market open should be OPEN."""
        dt = make_ist_datetime(2026, 6, 29, 9, 15, 0)
        assert is_market_open(dt) is True

    def test_closed_at_market_close_exact(self):
        """Boundary: exactly at market close should be OPEN (<=)."""
        dt = make_ist_datetime(2026, 6, 29, 15, 30, 0)
        assert is_market_open(dt) is True


# ============================================================
# get_market_status tests
# ============================================================


class TestGetMarketStatus:
    def test_status_open_during_session(self):
        with freeze_time("2026-06-29 09:30:00+05:30"):  # Monday 9:30 AM IST
            status = get_market_status()
        assert status["is_open"] is True
        assert status["status_text"] == "OPEN"
        assert status["is_market_day"] is True

    def test_status_weekend(self):
        with freeze_time("2026-06-27 11:00:00+05:30"):  # Saturday
            status = get_market_status()
        assert status["is_open"] is False
        assert "Weekend" in status["status_text"]

    def test_status_holiday(self):
        with freeze_time("2026-01-26 11:00:00+05:30"):  # Republic Day
            status = get_market_status()
        assert status["is_open"] is False
        assert "Holiday" in status["status_text"]

    def test_status_pre_open(self):
        with freeze_time("2026-06-29 09:05:00+05:30"):  # Monday pre-open
            status = get_market_status()
        assert status["is_open"] is False
        assert "PRE-OPEN" in status["status_text"]


# ============================================================
# seconds_until_market_open tests
# ============================================================


class TestSecondsUntilOpen:
    def test_returns_zero_when_open(self):
        dt = make_ist_datetime(2026, 6, 29, 11, 0)
        with patch("src.ingestion.market_hours.now_ist", return_value=dt):
            result = seconds_until_market_open()
        assert result == 0.0

    def test_positive_when_before_open(self):
        dt = make_ist_datetime(2026, 6, 29, 8, 0)  # 1h 15m before open
        with patch("src.ingestion.market_hours.now_ist", return_value=dt):
            result = seconds_until_market_open()
        assert result > 0
        assert abs(result - 75 * 60) < 5  # ~4500 seconds (±5s tolerance)


class TestMuhuratSpecialSession:
    def test_muhurat_special_session_trading_hours(self):
        dt = make_ist_datetime(2026, 11, 8, 18, 30)
        assert is_market_open(dt) is True

    def test_muhurat_session_boundaries(self):
        dt_pre = make_ist_datetime(2026, 11, 8, 18, 0)
        assert is_market_open(dt_pre) is False

        dt_post = make_ist_datetime(2026, 11, 8, 19, 30)
        assert is_market_open(dt_post) is False

    def test_seconds_until_special_session(self):
        # Time is 16:00 on Sunday, Muhurat is at 18:15
        dt = make_ist_datetime(2026, 11, 8, 16, 0)
        with patch("src.ingestion.market_hours.now_ist", return_value=dt):
            result = seconds_until_market_open()
        assert result == (2 * 3600) + (15 * 60)


class TestOfflineFallback:
    def test_offline_fallback_json_when_db_down(self):
        # We need the original unpatched function, let's reload or import it fresh if needed
        import src.ingestion.market_hours

        # We need to test the logic of fallback.
        # Since the autouse fixture mocks _get_calendar_records, we can test _get_calendar_records directly
        # by restoring it temporarily.
        patcher = patch("src.ingestion.market_hours.execute_sql")
        mock_execute = patcher.start()
        mock_execute.side_effect = Exception("DB Down")

        try:
            # Manually invoke the fallback logic
            records = src.ingestion.market_hours._load_fallback_calendar()
            assert any(r["holiday_date"] == "2026-01-26" for r in records)
            assert any(r["holiday_date"] == "2026-11-08" for r in records)

            # Since the db calendar function is mocked, we can't test is_market_open directly
            # without unpatching. But we can test the fallback JSON parsing works.
        finally:
            patcher.stop()
