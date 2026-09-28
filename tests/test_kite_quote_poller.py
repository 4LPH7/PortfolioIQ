"""
PortfolioIQ — Tests for Kite Quote Poller
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from kiteconnect.exceptions import NetworkException, TokenException

from src.ingestion.kite_quote_poller import (
    get_active_holding_symbols,
    poll_kite_quotes,
)


@patch("src.ingestion.kite_quote_poller.execute_sql")
def test_get_active_holding_symbols(mock_execute):
    mock_execute.return_value = [
        {"exchange": "NSE", "tradingsymbol": "INFY", "instrument_token": 408065},
        {"exchange": "BSE", "tradingsymbol": "TCS", "instrument_token": 123456},
    ]

    result = get_active_holding_symbols()

    assert mock_execute.called
    assert result == {
        "NSE:INFY": 408065,
        "BSE:TCS": 123456,
    }


@patch("src.ingestion.kite_quote_poller.get_active_holding_symbols")
def test_poll_kite_quotes_no_holdings(mock_get_symbols):
    mock_get_symbols.return_value = {}

    result = poll_kite_quotes()

    assert result == {"status": "no_holdings", "updated": 0}


@patch("src.ingestion.kite_quote_poller.get_authenticated_kite")
@patch("src.ingestion.kite_quote_poller.execute_sql")
@patch("src.ingestion.kite_quote_poller.get_active_holding_symbols")
def test_poll_kite_quotes_successful_upsert(mock_get_symbols, mock_execute, mock_get_kite):
    mock_get_symbols.return_value = {
        "NSE:INFY": 408065,
        "NSE:TCS": 123456,
    }

    mock_kite = MagicMock()
    mock_kite.quote.return_value = {
        "NSE:INFY": {
            "instrument_token": 408065,
            "last_price": 1500.5,
            "volume": 1000,
            "ohlc": {"open": 1490, "high": 1510, "low": 1480, "close": 1495},
            "net_change": 0.5,
        },
        "NSE:TCS": {
            "instrument_token": 123456,
            "last_price": 3500.0,
            "volume": 500,
            "ohlc": {"open": 3490, "high": 3510, "low": 3480, "close": 3495},
            "net_change": 0.2,
        },
    }
    mock_get_kite.return_value = mock_kite

    result = poll_kite_quotes()

    assert result == {"status": "ok", "updated": 2, "total": 2}

    # 2 upserts into live_prices, 2 inserts into price_history
    assert mock_execute.call_count == 4
    mock_kite.quote.assert_called_once_with(["NSE:INFY", "NSE:TCS"])


@patch("src.ingestion.kite_quote_poller.invalidate_token")
@patch("src.ingestion.kite_quote_poller.get_authenticated_kite")
@patch("src.ingestion.kite_quote_poller.get_active_holding_symbols")
def test_poll_kite_quotes_token_expired(mock_get_symbols, mock_get_kite, mock_invalidate):
    mock_get_symbols.return_value = {"NSE:INFY": 408065}

    mock_kite = MagicMock()
    mock_kite.quote.side_effect = TokenException("Token expired")
    mock_get_kite.return_value = mock_kite

    result = poll_kite_quotes()

    assert result == {"status": "token_expired"}
    assert mock_invalidate.called


@patch("src.ingestion.kite_quote_poller.get_authenticated_kite")
@patch("src.ingestion.kite_quote_poller.get_active_holding_symbols")
def test_poll_kite_quotes_network_exception(mock_get_symbols, mock_get_kite):
    mock_get_symbols.return_value = {"NSE:INFY": 408065}

    mock_kite = MagicMock()
    mock_kite.quote.side_effect = NetworkException("Connection lost")
    mock_get_kite.return_value = mock_kite

    result = poll_kite_quotes()

    assert result == {"status": "network_error"}


@patch("src.ingestion.kite_quote_poller.get_authenticated_kite")
@patch("src.ingestion.kite_quote_poller.execute_sql")
@patch("src.ingestion.kite_quote_poller.get_active_holding_symbols")
def test_poll_kite_quotes_missing_symbol_marked_stale(
    mock_get_symbols, mock_execute, mock_get_kite
):
    mock_get_symbols.return_value = {
        "NSE:INFY": 408065,
        "NSE:MISSING": 999999,
    }

    mock_kite = MagicMock()
    # Only return INFY, MISSING is not returned
    mock_kite.quote.return_value = {
        "NSE:INFY": {
            "instrument_token": 408065,
            "last_price": 1500.5,
            "volume": 1000,
        }
    }
    mock_get_kite.return_value = mock_kite

    result = poll_kite_quotes()

    assert result == {"status": "ok", "updated": 1, "total": 2}

    # 1 upsert into live_prices, 1 insert into price_history, 1 update to mark stale
    assert mock_execute.call_count == 3

    # Check that the last call was the UPDATE live_prices SET is_stale = TRUE
    last_call = mock_execute.call_args_list[-1]
    query = last_call[0][0]
    params = last_call[0][1]

    assert "UPDATE live_prices" in query
    assert "is_stale = TRUE" in query
    assert params["token"] == 999999
