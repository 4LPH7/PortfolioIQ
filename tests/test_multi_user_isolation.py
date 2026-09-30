"""
Tests for Phase 7 Multi-User & Tenant Isolation.
Verifies that User A's financial data is physically isolated from User B.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.db.repository import (
    get_cash_flows,
    get_current_holdings,
    get_daily_snapshots,
    get_signal_snapshots,
    record_order_attempt,
)


@pytest.fixture
def mock_session():
    """Mock database session."""
    session = MagicMock()
    return session


def test_holdings_tenant_isolation(mock_session):
    """User A's holdings query filters exclusively by user_id."""
    with patch("src.db.repository.get_db_session") as mock_get_db:
        mock_get_db.return_value.__enter__.return_value = mock_session
        mock_session.execute.return_value.fetchall.return_value = []

        get_current_holdings("user_alice")

        call_args = mock_session.execute.call_args
        sql = str(call_args[0][0])
        params = call_args[0][1]

        assert "WHERE user_id = :user_id" in sql or "h.user_id = :user_id" in sql
        assert params["user_id"] == "user_alice"
        assert params["user_id"] != "user_bob"


def test_cash_flows_tenant_isolation(mock_session):
    """User A cannot query User B's cash flows."""
    with patch("src.db.repository.get_db_session") as mock_get_db:
        mock_get_db.return_value.__enter__.return_value = mock_session
        mock_session.execute.return_value.fetchall.return_value = []

        get_cash_flows(user_id="user_alice")

        call_args = mock_session.execute.call_args
        sql = str(call_args[0][0])
        params = call_args[0][1]

        assert "WHERE user_id = :user_id" in sql
        assert params["user_id"] == "user_alice"


def test_snapshots_tenant_isolation(mock_session):
    """User A cannot query User B's portfolio snapshots."""
    with patch("src.db.repository.get_db_session") as mock_get_db:
        mock_get_db.return_value.__enter__.return_value = mock_session
        mock_session.execute.return_value.fetchall.return_value = []

        get_daily_snapshots(user_id="user_alice")

        call_args = mock_session.execute.call_args
        sql = str(call_args[0][0])
        params = call_args[0][1]

        assert "WHERE user_id = :user_id" in sql
        assert params["user_id"] == "user_alice"


def test_signals_tenant_isolation(mock_session):
    """User A cannot query User B's signal history."""
    with patch("src.db.repository.get_db_session") as mock_get_db:
        mock_get_db.return_value.__enter__.return_value = mock_session
        mock_session.execute.return_value.mappings.return_value.all.return_value = []

        get_signal_snapshots(tradingsymbol="INFY", user_id="user_alice")

        call_args = mock_session.execute.call_args
        sql = str(call_args[0][0])
        params = call_args[0][1]

        assert "WHERE user_id = :user_id" in sql
        assert params["user_id"] == "user_alice"


def test_order_attempt_tenant_isolation(mock_session):
    """Orders are strictly tagged with caller's user_id."""
    with patch("src.db.repository.get_db_session") as mock_get_db:
        mock_get_db.return_value.__enter__.return_value = mock_session
        mock_row = {
            "id": 101,
            "internal_order_id": "uuid-123",
            "kite_order_id": None,
            "user_id": "user_alice",
            "instrument_token": 256265,
            "tradingsymbol": "INFY",
            "exchange": "NSE",
            "transaction_type": "BUY",
            "order_type": "MARKET",
            "variety": "regular",
            "product": "CNC",
            "validity": "DAY",
            "requested_quantity": 10,
            "executed_quantity": 0,
            "requested_price": None,
            "trigger_price": None,
            "execution_price": None,
            "price_at_signal": 1500.0,
            "validation_status": "PENDING",
            "broker_status": "NOT_SENT",
            "rejection_reason": None,
            "is_dry_run": True,
            "notes": None,
            "trigger_source": "MANUAL",
            "created_at": None,
            "updated_at": None,
        }
        mock_session.execute.return_value.mappings.return_value.one.return_value = mock_row

        attempt = record_order_attempt(
            internal_order_id="uuid-123",
            instrument_token=256265,
            tradingsymbol="INFY",
            exchange="NSE",
            transaction_type="BUY",
            requested_quantity=10,
            price_at_signal=1500.0,
            validation_status="PENDING",
            is_dry_run=True,
            user_id="user_alice",
        )

        assert attempt.user_id == "user_alice"
        call_args = mock_session.execute.call_args
        params = call_args[0][1]
        assert params["user_id"] == "user_alice"
